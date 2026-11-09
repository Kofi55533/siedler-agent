#!/usr/bin/env python3
"""One shard of one wave of the COVER-mode novelty search (fork + probe), runner or box.

Work unit = (fixture, fork turn, strategy, rep). For a wave W with N shards the fixtures are dealt
round-robin (sorted by name, rotated by W) so shards are disjoint; the fork turns move with W so each
wave reaches new game states. Commands come from random.Random(key) -> the same key gives the same
probe on any machine (equality check: --only FIXTURE on the box vs the runner shard).

Per fixture: one s5recomp-cover run from the start image dumps MEMIMAGE at every fork turn,
mem2img makes fork images, then every fork gets len(STRATS)*reps probes of SHORT turns with COVER=;
the COVER file is reduced to EXE function starts via body_ranges (same rule as cover_reduce.py).
Output: one JSON (counts, per-probe hash of the function set, logic functions outside union3).
Never prints function addresses or game data to stdout (runner logs are public).

usage: novelty_shard.py --bundle B --imgdir I --wave W --shard S --nshards N --out R.json
       [--only FIXTURE] [--forks 12] [--reps 2] [--budget-min 40] [--workers nproc] [--tmp DIR]
env: S5_GAME, S5_ARTIFACTS (verify.remap_for)
"""
from __future__ import annotations
import argparse, bisect, hashlib, json, os, pathlib, queue, random, shutil, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

SHORT = 50
CAP = 6000
FRAGILE = {'ai-citygates', 'ai-builds-attacks', 'ai-builds-battle', 'ai-builds-citygates',
           'ai-builds-triggered', 'map-sandbox', 'saveload-stable', 'camp-bigcity'}
STRATS = ('move', 'build', 'ent_pos', 'tech', 'build_wide', 'upgrade_wide', 'recruit', 'cannon', 'tech_wide')
EXE_BASE = 0x400000

ap = argparse.ArgumentParser()
ap.add_argument('--bundle', required=True)
ap.add_argument('--imgdir', required=True)
ap.add_argument('--wave', type=int, required=True)
ap.add_argument('--shard', type=int, required=True)
ap.add_argument('--nshards', type=int, required=True)
ap.add_argument('--out', default='')
ap.add_argument('--list', action='store_true', help='print this shard\'s fixture names and exit')
ap.add_argument('--only', default='')
ap.add_argument('--forks', type=int, default=24)
ap.add_argument('--reps', type=int, default=1)
ap.add_argument('--strats', default=','.join(STRATS), help='comma list of command strategies per fork')
ap.add_argument('--budget-min', type=float, default=40.0)
ap.add_argument('--workers', type=int, default=os.cpu_count() or 4)
ap.add_argument('--tmp', default='/tmp/nv')
A = ap.parse_args()

B = pathlib.Path(A.bundle); IMG = pathlib.Path(A.imgdir); TMP = pathlib.Path(A.tmp)
BIN = str(B/'bin'/'s5recomp-cover')
sys.path.insert(0, str(B/'py'))
if not A.list:
    import verify  # noqa: E402  (remap_for only)
MEM2IMG = str(B/'py'/'mem2img.py'); INGEST = str(B/'py'/'command_ingest.py')
cat = json.load(open(B/'data'/'catalog.json'))
STARTS, LO, HI = cat['starts'], cat['lo'], cat['hi']
LOGIC = set(json.load(open(B/'data'/'logic.json')))
UNION3 = set(json.load(open(B/'data'/'union3.json')))
POOLS = json.load(open(B/'data'/'pools.json'))
PLAN = json.load(open(B/'data'/'plan.json'))
T0 = time.time(); DEADLINE = T0 + A.budget_min*60


def my_fixtures():
    fx = sorted(PLAN['fixtures'], key=lambda f: f['name'])
    if A.only:
        return [f for f in fx if f['name'] == A.only]
    n = len(fx)
    return [fx[i] for i in range(n) if (i + A.wave) % A.nshards == A.shard]


def fork_turns(f):
    start, turns = f['start'], f['turns']
    if f['name'] in FRAGILE:
        return [start + 20 + (A.wave*37) % 100]
    span = min(turns, CAP) - SHORT - 10
    if span < 60:
        return [start + max(1, span//2)]
    k = A.forks; step = span / k
    jit = ((A.wave*0.6180339887) % 1.0) * step
    out = sorted({start + int(jit + i*step) + 1 for i in range(k)})
    return [t for t in out if t < start + span + 1]


def pool(name):
    for k in ('camp-crawford', 'map-winterisland', 'combat'):
        if k.split('-')[0] in name or k in name:
            if POOLS.get(k):
                return POOLS[k]
    return POOLS.get('combat') or [65559]


def encode(dirp, strategy, turn0, ents, key):
    rng = random.Random(key)
    rows = []; eid = 7000
    serfs = (ents[:3] + ents*3)[:3]
    for i in range(8):
        turn = turn0 + i*max(1, SHORT//8)
        e = rng.choice(ents)
        X = lambda a=25000, b=45000: float(rng.randint(a, b))
        if strategy in ('build', 'build_wide'):
            et = rng.choice([1, 3, 15, 22, 27, 35]) if strategy == 'build' else rng.randint(1, 79)
            lo, hi = (25000, 45000) if strategy == 'build' else (20000, 50000)
            cls = 'GGL::CNetEventBuildingCreator'
            fields = {'PlayerID': 1, 'EntityType': et, 'Position.X': X(lo, hi), 'Position.Y': X(lo, hi),
                      'Orientation': 0.0, 'Serf.1': serfs[0], 'Serf.2': serfs[1], 'Serf.3': serfs[2]}
        elif strategy == 'move':
            cls = 'EGL::CNetEventEntityAndPosArray'
            fields = {'EntityID': e, 'Orientation': 0.0, 'Position.1.X': X(), 'Position.1.Y': X()}
        elif strategy == 'ent_pos':
            cls = 'EGL::CNetEventEntityAndPos'
            fields = {'EntityID': e, 'X': X(), 'Y': X()}
        elif strategy == 'upgrade_wide':
            cls = 'GGL::CNetEventEntityIDAndUpgradeCategory'
            fields = {'EntityID': e, 'UpgradeCategory': rng.randint(1, 59)}
        elif strategy == 'recruit':
            cls = 'EGL::CNetEventEntityIDAndPlayerIDAndEntityType'
            fields = {'PlayerID': 1, 'EntityID': e, 'EntityType': rng.randint(1, 99)}
        elif strategy == 'cannon':
            cls = 'GGL::CNetEventCannonCreator'
            fields = {'EntityID': e, 'FoundationType': rng.randint(1, 8), 'CannonType': rng.randint(1, 5),
                      'Position.X': X(20000, 50000), 'Position.Y': X(20000, 50000), 'Orientation': 0.0}
        else:
            cls = 'GGL::CNetEventTechnologyAndEntityID'
            tt = rng.choice([20, 40, 57, 135]) if strategy == 'tech' else rng.randint(1, 159)
            fields = {'EntityID': e, 'TechnologyType': tt}
        rows.append({'turn': turn, 'id': eid, 'class': cls, 'fields': fields}); eid += 1
    jl = dirp/'c.jsonl'; jl.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    binp = dirp/'c.bin'
    r = subprocess.run([sys.executable, INGEST, str(jl), str(binp)], capture_output=True, text=True)
    return binp if r.returncode == 0 else None


def reduce_cover(path):
    hits = set()
    for line in pathlib.Path(path).read_text().splitlines():
        if not line or line[0] == '#':
            continue
        name, rva = line.split('\t', 1)
        if name != 'settlershok.exe':
            continue
        pc = EXE_BASE + int(rva, 16)
        i = bisect.bisect_right(STARTS, pc) - 1
        if i >= 0 and LO[i] <= pc < HI[i]:
            hits.add(STARTS[i])
    return hits


lock = threading.Lock()
res = {'strats': A.strats, 'wave': A.wave, 'shard': A.shard, 'nshards': A.nshards, 'only': A.only, 'forks_per_fixture': A.forks,
       'reps': A.reps, 'workers': A.workers, 'fixtures': [], 'advances': [], 'probes': [],
       'union_logic': set(), 'turns_sim': 0, 'probe_wall': 0.0, 'adv_wall': 0.0, 'stopped_by_budget': False}


def advance(f):
    """fork images at all fork turns of f (one run from the start image)."""
    src = IMG/f['img']; turns = fork_turns(f)
    work = TMP/f'adv-{f["name"]}'; shutil.rmtree(work, ignore_errors=True); (work/'t').mkdir(parents=True)
    env = dict(os.environ, S5_REMAP=verify.remap_for(str(src), str(work/'rm')),
               MEMIMAGE_AT=','.join(f'{t}:{work}/t{t}.mem' for t in turns), TMPDIR=str(work/'t'))
    need = max(turns) - f['start'] + 1
    t0 = time.perf_counter()
    p = subprocess.run([BIN, str(src), str(need)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    forks = []
    for t in turns:
        mem = work/f't{t}.mem'
        if not mem.exists():
            continue
        out = TMP/f'fork-{f["name"]}-t{t}.img'
        r = subprocess.run([sys.executable, MEM2IMG, str(mem), str(src), str(out)], capture_output=True, text=True)
        mem.unlink(missing_ok=True)
        if r.returncode == 0 and out.exists():
            shutil.copy2(str(src) + '.state.json', str(out) + '.state.json')
            forks.append((f, t, out))
    dt = time.perf_counter() - t0
    shutil.rmtree(work, ignore_errors=True)
    with lock:
        res['advances'].append({'fixture': f['name'], 'rc': p.returncode, 'wall': round(dt, 2), 'need': need,
                                'turns': turns, 'forks': len(forks)})
        res['turns_sim'] += need; res['adv_wall'] += dt
    return forks


def probe(f, t, img, strategy, rep):
    key = f'{f["name"]}|{t}|{strategy}|{rep}|w{A.wave}'
    if time.time() > DEADLINE:
        res['stopped_by_budget'] = True
        return
    run = TMP/f'p-{f["name"]}-{t}-{strategy}-{rep}'; shutil.rmtree(run, ignore_errors=True); (run/'x').mkdir(parents=True)
    try:
        cmd = encode(run, strategy, t, pool(f['name']), key)
        if cmd is None:
            with lock: res['probes'].append({'k': key, 'rc': 'encode'})
            return
        cov = run/'o.cover'
        env = dict(os.environ, S5_REMAP=verify.remap_for(str(img), str(run/'rm')), COMMANDS=str(cmd),
                   COVER=str(cov), TMPDIR=str(run/'x'))
        t0 = time.perf_counter()
        p = subprocess.run([BIN, str(img), str(SHORT)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        dt = time.perf_counter() - t0
        hits = reduce_cover(cov) if cov.exists() else set()
        lg = hits & LOGIC
        new = sorted(lg - UNION3)
        h = hashlib.sha256(''.join(f'{a:x}\n' for a in sorted(hits)).encode()).hexdigest()[:16]
        with lock:
            res['probes'].append({'k': key, 'rc': p.returncode, 'wall': round(dt, 2), 'nf': len(hits), 'nl': len(lg),
                                  'h': h, 'new': [f'0x{a:06x}' for a in new]})
            res['union_logic'] |= lg; res['turns_sim'] += SHORT; res['probe_wall'] += dt
    finally:
        shutil.rmtree(run, ignore_errors=True)


def main():
    if A.list:
        print('\n'.join(f['name'] for f in my_fixtures()))
        return
    TMP.mkdir(parents=True, exist_ok=True)
    fxs = my_fixtures(); res['fixtures'] = [f['name'] for f in fxs]
    print(f'shard {A.shard}/{A.nshards} wave {A.wave}: {len(fxs)} fixtures, workers {A.workers}', flush=True)
    fq: queue.Queue = queue.Queue(maxsize=1)  # at most one fixture's forks waiting on disk

    def producer():
        for f in fxs:
            if time.time() > DEADLINE:
                break
            fq.put(advance(f))
        fq.put(None)
    threading.Thread(target=producer, daemon=True).start()
    with ThreadPoolExecutor(A.workers) as ex:
        while True:
            forks = fq.get()
            if forks is None:
                break
            futs = [ex.submit(probe, f, t, img, s, r) for (f, t, img) in forks for s in A.strats.split(',') for r in range(A.reps)]
            for fu in futs:
                fu.result()
            for (_, _, img) in forks:
                img.unlink(missing_ok=True); pathlib.Path(str(img) + '.state.json').unlink(missing_ok=True)
            n = len(res['probes'])
            print(f'  fixture {len(res["advances"])}/{len(fxs)}: forks={len(forks)} probes_total={n} '
                  f't={time.time()-T0:.0f}s', flush=True)
    res['union_logic'] = [f'0x{a:06x}' for a in sorted(res['union_logic'])]
    res['union_new'] = sorted({a for p in res['probes'] for a in p.get('new', [])})
    res['elapsed'] = round(time.time() - T0, 2)
    ok = sum(1 for p in res['probes'] if p.get('rc') == 0)
    pathlib.Path(A.out).write_text(json.dumps(res))
    print(f'done: probes={len(res["probes"])} ok={ok} forks={sum(a["forks"] for a in res["advances"])} '
          f'turns_sim={res["turns_sim"]} elapsed={res["elapsed"]}s new_vs_union3={len(res["union_new"])} '
          f'budget_stop={res["stopped_by_budget"]}', flush=True)


if __name__ == '__main__':
    main()
