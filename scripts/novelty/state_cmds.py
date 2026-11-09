#!/usr/bin/env python3
"""State-aware player commands for novelty probes: read the live game state out of a fork image (S5IMG00x /
S5MEM001) and build commands that the game's handlers actually execute.

Why: the old probe generator wrote 'id' = 5000+i / 7000+i. In command_ingest's jsonl 'id' is the NET EVENT ID
(0x11002..0x1104c) that GGL::CGLGameLogic dispatches on, so no handler ever ran, and the entity ids came from
pool files of other states. Here the event ids are the registered ones (engine_command_events.json; the class
per id is the one every recorded/generated commands.jsonl in the repo uses) and every id/position comes from the
image: own units, own buildings by kind, enemy targets, trees/rocks, construction sites.

Self-contained (no repo imports): stdlib only, so the runner bundle needs just this file.
usage (CLI): state_cmds.py IMAGE [--player P] [--summary] | IMAGE STRATEGY OUT.jsonl [--seed S] [--short N]
"""
from __future__ import annotations
import bisect, json, math, random, struct, sys, zlib

EGL_LOGIC = 0x895DAC
ENTITY_MANAGER = 0x897558
IDM_ENTITY_TYPES = 0x895DC0

# net event ids (registered handlers, engine_command_events.json) by command
EV = dict(move=69634, build=69635, buy_serf=69636, sell=69638, upgrade=69640, buy_leader=69643, buy_soldier=69644,
          tax=69646, expel=69647, attack=69650, leader_e=69651, leader_int=69653, construct=69655, repair=69656,
          extract=69657, research=69658, cancel_research=69659, trade=69660, cancel_trade=69661,
          cancel_upgrade=69662, patrol=69669, worker_limit=69672, battle_serf_off=69677, battle_serf_on=69678,
          alarm_on=69680, alarm_off=69681, overtime=69683)
HERO_E = [69665, 69667, 69682, 69685, 69689, 69690, 69694, 69703, 69707, 69651]
HERO_POS = [69663, 69668, 69676, 69704, 69706]
# 69695 with an enemy unit as target crashes the original handler (rc 101, gen-market-trade@400)
HERO_2E = [69664, 69688, 69693, 69701, 69702, 69705, 69708]
# building upgrade categories the GUI places (data/scope/gui_build_buttons.json); bridges left out (need water)
BUILD_CATS = [3, 1, 4, 13, 12, 23, 21, 16, 15, 14, 22, 17, 20, 25, 24, 9, 26, 10, 11, 18, 19, 64, 65, 63,
              28, 29, 30, 31, 66, 32, 67, 33, 34, 35, 36, 37]
RECRUIT = {'PB_Barracks': [46, 48], 'PB_Archery': [50, 56], 'PB_Stable': [52, 53], 'PB_Tavern': [75, 76],
           'PB_VillageCenter': [], 'PB_Foundry': [71, 72, 73, 74]}
SOLDIER_OF = [('PU_LeaderPoleArm', 49), ('PU_LeaderSword', 47), ('PU_LeaderBow', 51), ('PU_LeaderHeavyCavalry', 55),
              ('PU_LeaderCavalry', 54), ('PU_LeaderRifle', 57), ('CU_BlackKnight_LeaderMace', 43),
              ('CU_Barbarian_Leader', 45), ('CU_BanditLeaderSword', 41), ('CU_BanditLeaderBow', 82),
              ('CU_Evil_LeaderBearman', 78), ('CU_Evil_LeaderSkirmisher', 80)]
GT_TECHS = list(range(1, 22))                 # GT_* university technologies
OTHER_TECHS = list(range(61, 200))            # T_* (blacksmith, alchemist, ...), the game rejects what fails
RESOURCES = [1, 5, 7, 9, 11, 13]              # Gold Stone Iron Sulfur Clay Wood (refined)

GGL_LOGIC = 0x85A3A0


def player_status(im, player):
    """GGL::CPlayerStatus of a player: [[[GGL_LOGIC]+0x28] + player*8 + 4]; resources are floats at +0x5C+4*type"""
    return im.u32(im.u32(im.u32(GGL_LOGIC) + 0x28) + player * 8 + 4)


def poke_writes(im, player, amount=20000.0):
    """memory writes that give the player plenty of every resource (discovery only, not realistic play)"""
    p = player_status(im, player)
    if not p:
        return []
    return [(p + 0x5C + 4 * r, struct.pack('<f', amount)) for r in range(1, 18)]


def add_pokes(binpath, writes):
    """append raw memory writes to the FIRST record of an S5CMD001 file (they land before its event is posted)"""
    b = bytearray(open(binpath, 'rb').read())
    assert b[:8] == b'S5CMD001'
    n = struct.unpack_from('<I', b, 8)[0]
    if not n or not writes:
        return False
    i = 12
    nw = struct.unpack_from('<I', b, i + 8)[0]
    extra = b''.join(struct.pack('<II', a, len(d)) + d for a, d in writes)
    j = i + 12   # insert the extra writes right after the record header (before its own writes)
    struct.pack_into('<I', b, i + 8, nw + len(writes))
    b[j:j] = extra
    open(binpath, 'wb').write(bytes(b))
    return True


def poke_row(st, turn):
    """carrier command for the pokes: alarm off is a no-op when the alarm is not on"""
    return {'turn': turn, 'id': EV['alarm_off'], 'class': 'EGL::CNetEventPlayerID', 'fields': {'PlayerID': st['player']}}


STRATS = ('none', 'move', 'patrol', 'attack', 'build', 'upgrade', 'recruit', 'research', 'trade',
          'serf_work', 'economy', 'hero', 'sell')


class Img:
    def __init__(self, path):
        f = open(path, 'rb')
        magic = f.read(8)
        assert magic in (b'S5IMG002', b'S5IMG003', b'S5MEM001'), magic
        n = struct.unpack('<I', f.read(4))[0]
        regs = []
        for _ in range(n):
            a, raw, zl = struct.unpack('<III', f.read(12))
            regs.append((a, zlib.decompress(f.read(zl))))
        regs.sort(key=lambda r: r[0])
        self.regions = regs; self.starts = [r[0] for r in regs]

    def rb(self, a, n):
        i = bisect.bisect_right(self.starts, a) - 1
        if i >= 0:
            lo, d = self.regions[i]
            if a + n <= lo + len(d):
                return d[a - lo:a - lo + n]
        return bytes(n)

    def u32(self, a):
        return struct.unpack('<I', self.rb(a, 4))[0]

    def cstr(self, a, n=128):
        return self.rb(a, n).split(b'\0')[0].decode('latin1') if a else None

    def turn(self):
        return self.u32(self.u32(self.u32(EGL_LOGIC) + 0x1C))

    def type_names(self, limit=5000):
        m = self.u32(IDM_ENTITY_TYPES)
        first, last = self.u32(m + 8 + 4), self.u32(m + 8 + 8)
        return {i: self.cstr(self.u32(first + 8 * i)) for i in range(min(limit, (last - first) // 8))}

    def entities(self):
        names = self.type_names()
        mgr = self.u32(ENTITY_MANAGER)
        out = []
        for idx in range(self.u32(mgr + 4) + 1):
            w, p = self.u32(mgr + 0x10 + idx * 8), self.u32(mgr + 0x14 + idx * 8)
            if not p:
                continue
            b = self.rb(p, 0x60)
            eid, ty, pl = struct.unpack_from('<I', b, 8)[0], struct.unpack_from('<I', b, 0x10)[0], struct.unpack_from('<I', b, 0x18)[0]
            x, y = struct.unpack_from('<ff', b, 0x58)
            out.append({'id': eid, 'type': ty, 'name': names.get(ty) or str(ty), 'player': pl, 'x': x, 'y': y})
        return out


def _ok(v):
    return isinstance(v, float) and math.isfinite(v) and 0 < v < 1e6


def read_state(path, player=None):
    im = Img(path)
    ents = [e for e in im.entities() if _ok(e['x']) and _ok(e['y'])]
    hqs = [e for e in ents if e['name'].startswith('PB_Headquarters') and 1 <= e['player'] <= 8]
    if player is None:
        own_hq = [h for h in hqs if h['player'] == 1]
        if own_hq:
            player = 1
        else:   # the player with the most settlers/buildings
            cnt = {}
            for e in ents:
                if 1 <= e['player'] <= 8 and e['name'].startswith(('PU_', 'PB_')):
                    cnt[e['player']] = cnt.get(e['player'], 0) + 1
            player = max(cnt, key=cnt.get) if cnt else 1
    own = [e for e in ents if e['player'] == player]
    st = {'turn': im.turn(), 'player': player, 'n_entities': len(ents), 'status': player_status(im, player)}
    pick = lambda pre: [e for e in own if e['name'].startswith(pre)]
    st['hq'] = pick('PB_Headquarters') or pick('PB_VillageCenter') or pick('PB_')
    st['serfs'] = pick('PU_Serf')
    st['workers'] = [e for e in own if e['name'].startswith('PU_') and not e['name'].startswith(('PU_Serf', 'PU_Leader', 'PU_Soldier', 'PU_Hero', 'PU_BattleSerf'))]
    st['leaders'] = [e for e in own if 'Leader' in e['name'] and e['name'].startswith(('PU_', 'CU_'))]
    st['soldiers'] = [e for e in own if 'Soldier' in e['name']]
    st['heroes'] = [e for e in own if e['name'].startswith(('PU_Hero', 'CU_Hero'))]
    st['cannons'] = [e for e in own if 'Cannon' in e['name'] and e['name'].startswith('PV_')]
    st['battleserfs'] = pick('PU_BattleSerf')
    st['buildings'] = pick('PB_')
    st['sites'] = [e for e in own if e['name'].startswith('ZB_') or 'BuildingSite' in e['name']]
    st['markets'] = pick('PB_Market')
    st['universities'] = pick('PB_University')
    st['recruiters'] = [e for e in own if any(e['name'].startswith(k) for k, v in RECRUIT.items() if v)]
    st['military'] = st['leaders'] + st['heroes'] + st['cannons'] + st['battleserfs']
    st['mobile'] = st['serfs'] + st['workers'] + st['military'] + st['soldiers']
    enemy = [e for e in ents if 1 <= e['player'] <= 8 and e['player'] != player and e['name'].startswith(('PU_', 'PB_', 'CU_', 'PV_', 'CB_'))]
    st['enemies'] = enemy
    st['trees'] = [e for e in ents if e['player'] == 0 and e['name'].startswith(('XD_Fir', 'XD_Pine', 'XD_Tree', 'XD_Cypress', 'XD_Palm', 'XD_Willow', 'XD_Birch', 'XD_Oak', 'XD_DeadTree'))]
    st['rocks'] = [e for e in ents if e['player'] == 0 and e['name'].startswith(('XD_Rock', 'XD_Stone'))]
    st['center'] = (st['hq'][0]['x'], st['hq'][0]['y']) if st['hq'] else ((own[0]['x'], own[0]['y']) if own else (30000.0, 30000.0))
    return st


def summary(st):
    return {k: (len(v) if isinstance(v, list) else v) for k, v in st.items() if k != 'status'}


def _near(lst, c, r):
    return [e for e in lst if (e['x'] - c[0]) ** 2 + (e['y'] - c[1]) ** 2 < r * r]


def _nearest(lst, e, k=5):
    return sorted(lst, key=lambda o: (o['x'] - e['x']) ** 2 + (o['y'] - e['y']) ** 2)[:k]


def gen(st, strategy, turn0, rng, short=50, n=8):
    """[(turn, event id, class, fields)]: n commands of one kind spread over the first short-5 turns."""
    P = st['player']; c = st['center']
    rows = []
    def T(i):
        return turn0 + 1 + (i * max(1, (short - 10))) // n
    def pos_around(x, y, r):
        return float(round(x + rng.uniform(-r, r), -2)), float(round(y + rng.uniform(-r, r), -2))
    def add(i, ev, cls, **f):
        rows.append({'turn': T(i), 'id': ev, 'class': cls, 'fields': f})
    ch = rng.choice
    if strategy == 'none':
        return rows
    for i in range(n):
        if strategy == 'move':
            src = st['mobile']
            if not src: break
            e = ch(src); x, y = pos_around(e['x'], e['y'], 2500)
            add(i, EV['move'], 'EGL::CNetEventEntityAndPosArray', EntityID=e['id'], Orientation=0.0,
                **{'Position.1.X': x, 'Position.1.Y': y})
        elif strategy == 'patrol':
            src = st['military'] or st['mobile']
            if not src: break
            e = ch(src); f = {'EntityID': e['id'], 'Orientation': 0.0}
            for k in range(1, 3):
                x, y = pos_around(e['x'], e['y'], 2000); f[f'Position.{k}.X'] = x; f[f'Position.{k}.Y'] = y
            add(i, EV['patrol'], 'EGL::CNetEventEntityAndPosArray', **f)
        elif strategy == 'attack':
            src = st['military'] or st['soldiers'] or st['serfs']
            if not src or not st['enemies']: break
            e = ch(src); tgt = ch(_nearest(st['enemies'], e, 6))
            if i % 3 == 2:   # attack-move to the enemy position (move handler, military branch)
                add(i, EV['move'], 'EGL::CNetEventEntityAndPosArray', EntityID=e['id'], Orientation=0.0,
                    **{'Position.1.X': float(round(tgt['x'])), 'Position.1.Y': float(round(tgt['y']))})
            else:
                add(i, EV['attack'], 'EGL::CNetEvent2Entities', EntityID1=e['id'], EntityID2=tgt['id'])
        elif strategy == 'build':
            if not st['serfs']: break
            cat = BUILD_CATS[(i + rng.randrange(len(BUILD_CATS))) % len(BUILD_CATS)] if i else 3
            x, y = pos_around(c[0], c[1], 3500)
            serfs = rng.sample(st['serfs'], min(3, len(st['serfs'])))
            add(i, EV['build'], 'GGL::CNetEventBuildingCreator', PlayerID=P, EntityType=cat, Orientation=ch([0.0, 90.0, 180.0, 270.0]),
                **{'Position.X': x, 'Position.Y': y}, **{f'Serf.{k + 1}': s['id'] for k, s in enumerate(serfs)})
        elif strategy == 'upgrade':
            if not st['buildings']: break
            b = ch(st['buildings'])
            if i == n - 1:
                add(i, EV['cancel_upgrade'], 'EGL::CNetEventEntityID', EntityID=b['id'])
            else:
                add(i, EV['upgrade'], 'EGL::CNetEventEntityID', EntityID=b['id'])
        elif strategy == 'recruit':
            opts = []
            for b in st['recruiters']:
                for k, cats in RECRUIT.items():
                    if b['name'].startswith(k):
                        opts += [('L', b, cat) for cat in cats]
            for l in st['leaders']:
                for pre, cat in SOLDIER_OF:
                    if l['name'].startswith(pre):
                        opts.append(('S', l, cat)); break
            if not opts:
                if st['hq']:   # nothing to recruit at: buy serfs at the headquarters instead
                    add(i, EV['buy_serf'], 'EGL::CNetEventEntityIDAndPlayerID', PlayerID=P, EntityID=st['hq'][0]['id'])
                continue
            kind, e, cat = ch(opts)
            add(i, EV['buy_leader'] if kind == 'L' else EV['buy_soldier'], 'GGL::CNetEventEntityIDAndUpgradeCategory',
                EntityID=e['id'], UpgradeCategory=cat)
        elif strategy == 'research':
            if st['universities'] and rng.random() < 0.7:
                add(i, EV['research'], 'GGL::CNetEventTechnologyAndEntityID', EntityID=ch(st['universities'])['id'],
                    TechnologyType=ch(GT_TECHS))
            elif st['buildings']:
                add(i, EV['research'], 'GGL::CNetEventTechnologyAndEntityID', EntityID=ch(st['buildings'])['id'],
                    TechnologyType=ch(OTHER_TECHS))
        elif strategy == 'trade':
            if not st['markets']: break
            m = ch(st['markets']); s, b = rng.sample(RESOURCES, 2)
            add(i, EV['trade'], 'GGL::CNetEventTransaction', EntityID=m['id'], SellType=s, BuyType=b,
                BuyAmount=float(ch([10, 25, 50, 100])))
        elif strategy == 'serf_work':
            if not st['serfs']: break
            s = ch(st['serfs']); k = i % 4
            if k in (0, 1) and (st['trees'] or st['rocks']):
                src = [l for l in (st['trees'], st['rocks']) if l]
                lst = src[i % len(src)]
                r = ch(_nearest(lst, s, 8)); rt = 14 if lst is st['trees'] else 6
                add(i, EV['extract'], 'GGL::CNetEventExtractResource', SerfID=s['id'], ResourceType=rt,
                    **{'Position.X': float(round(r['x'])), 'Position.Y': float(round(r['y']))})
            elif k == 2 and st['sites']:
                add(i, EV['construct'], 'EGL::CNetEvent2Entities', EntityID1=s['id'], EntityID2=ch(st['sites'])['id'])
            elif st['buildings']:
                add(i, EV['repair'] if k == 3 else EV['construct'], 'EGL::CNetEvent2Entities', EntityID1=s['id'],
                    EntityID2=ch(st['buildings'])['id'])
        elif strategy == 'economy':
            k = i % 8
            if k == 0: add(i, EV['tax'], 'EGL::CNetEventIntegerAndPlayerID', PlayerID=P, Value=rng.randint(0, 4))
            elif k == 1: add(i, EV['alarm_on'], 'EGL::CNetEventPlayerID', PlayerID=P)
            elif k == 5: add(i, EV['alarm_off'], 'EGL::CNetEventPlayerID', PlayerID=P)
            elif k == 2 and st['buildings']: add(i, EV['overtime'], 'EGL::CNetEventEntityID', EntityID=ch(st['buildings'])['id'])
            elif k == 3 and st['buildings']: add(i, EV['worker_limit'], 'EGL::CNetEventEntityIDAndInteger', EntityID=ch(st['buildings'])['id'], Value=rng.randint(0, 8))
            elif k == 4 and st['hq']: add(i, EV['buy_serf'], 'EGL::CNetEventEntityIDAndPlayerID', PlayerID=P, EntityID=st['hq'][0]['id'])
            elif k == 6 and st['serfs']: add(i, EV['battle_serf_on'], 'EGL::CNetEventEntityID', EntityID=ch(st['serfs'])['id'])
            elif k == 7 and st['battleserfs']: add(i, EV['battle_serf_off'], 'EGL::CNetEventEntityID', EntityID=ch(st['battleserfs'])['id'])
            elif k == 7 and st['workers']: add(i, EV['expel'], 'EGL::CNetEventEntityID', EntityID=ch(st['workers'])['id'])
        elif strategy == 'hero':
            if not st['heroes']: break
            h = ch(st['heroes']); k = i % 3
            if k == 0:
                add(i, ch(HERO_E), 'EGL::CNetEventEntityID', EntityID=h['id'])
            elif k == 1:
                x, y = pos_around(h['x'], h['y'], 1500)
                add(i, ch(HERO_POS), 'EGL::CNetEventEntityAndPos', EntityID=h['id'], X=x, Y=y)
            else:
                tg = _nearest(st['enemies'], h, 4) or _nearest(st['buildings'], h, 4)
                if tg: add(i, ch(HERO_2E), 'EGL::CNetEvent2Entities', EntityID1=h['id'], EntityID2=ch(tg)['id'])
        elif strategy == 'sell':
            cand = [b for b in st['buildings'] if not b['name'].startswith(('PB_Headquarters', 'PB_VillageCenter'))]
            if not cand: break
            add(i, EV['sell'], 'EGL::CNetEventEntityIDAndPlayerID', PlayerID=P, EntityID=ch(cand)['id'])
            if i >= 1: break   # selling two buildings is enough
        else:
            raise ValueError(strategy)
    return rows


def bootstrap(st, start, until, every=40, n=12):
    """advance-time commands for a fixture without its own command stream: buy serfs at the HQ (69636)."""
    if not st['hq'] or not st['hq'][0]['name'].startswith('PB_Headquarters'):
        return []
    hq = st['hq'][0]['id']
    return [{'turn': start + 5 + k * every, 'id': EV['buy_serf'], 'class': 'EGL::CNetEventEntityIDAndPlayerID',
             'fields': {'PlayerID': st['player'], 'EntityID': hq}} for k in range(n) if start + 5 + k * every < until]


def available(st):
    """strategies that have the entities they need in this state"""
    need = {'move': st['mobile'], 'patrol': st['mobile'], 'attack': (st['military'] or st['soldiers'] or st['serfs']) and st['enemies'],
            'build': st['serfs'], 'upgrade': st['buildings'], 'recruit': st['recruiters'] or st['leaders'] or st['hq'],
            'research': st['buildings'], 'trade': st['markets'], 'serf_work': st['serfs'], 'economy': [1],
            'hero': st['heroes'], 'sell': st['buildings'], 'none': [1]}
    return [s for s in STRATS if need.get(s)]


if __name__ == '__main__':
    a = sys.argv
    pl = int(a[a.index('--player') + 1]) if '--player' in a else None
    st = read_state(a[1], pl)
    if len(a) < 4 or a[2].startswith('--'):
        print(json.dumps({**summary(st), 'available': available(st)}))
    else:
        seed = int(a[a.index('--seed') + 1]) if '--seed' in a else 1
        rows = gen(st, a[2], st['turn'], random.Random(seed))
        open(a[3], 'w').write(''.join(json.dumps(r) + '\n' for r in rows))
        print(len(rows), 'commands')
