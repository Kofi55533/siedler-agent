#!/usr/bin/env python3
"""One shard of one wave of the COVER-mode novelty search (fork + probe), runner or box.

Commands are generated from the live state of each fork (state_cmds.py): the registered net event ids
(0x11002..), the fork's own units/buildings/serfs/heroes/markets, enemy targets, trees/rocks, and the player's
resources poked to 20000 each before the first command (discovery, not realistic play). Strategies per fork are the
ones whose entities exist (state_cmds.available): none (poke only, the baseline), move, patrol, attack, build,
upgrade, recruit, research, trade, serf_work, economy, hero, sell.
Forks: one s5recomp-cover run per fixture from its start image WITH the fixture's own command stream
(bundle data/fxcmds/<name>.jsonl); a fixture without one gets a bootstrap (resources poked, serfs bought at the HQ),
so forks are played states, not an idle player.

Work unit = (fixture, fork turn, strategy, rep); random.Random(key) -> same key, same probe on any machine.
Output: one JSON (per-probe function-set hash, posted count, logic functions outside union3+known).
Never prints function addresses or game data to stdout (runner logs are public).

usage: novelty_shard.py --bundle B --imgdir I --wave W --shard S --nshards N --out R.json
       [--only FIXTURE] [--forks 12] [--reps 1] [--budget-min 40] [--workers nproc] [--tmp DIR] [--no-poke]
env: S5_GAME, S5_ARTIFACTS (verify.remap_for)
"""
from __future__ import annotations
import argparse, bisect, hashlib, json, os, pathlib, queue, random, shutil, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

SHORT = 50
CAP = 6000
FRAGILE = {'ai-citygates', 'ai-builds-attacks', 'ai-builds-battle', 'ai-builds-citygates',
           'ai-builds-triggered', 'map-sandbox', 'saveload-stable', 'camp-bigcity'}
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
ap.add_argument('--forks', type=int, default=12)
ap.add_argument('--reps', type=int, default=1)
ap.add_argument('--strats', default='', help='comma list to restrict the strategies (default: all available)')
ap.add_argument('--no-poke', action='store_true')
ap.add_argument('--cap', type=int, default=CAP, help='fork turns lie within the first CAP turns of a fixture')
ap.add_argument('--adv-par', type=int, default=1, help='fixtures advanced in parallel')
ap.add_argument('--budget-min', type=float, default=40.0)
ap.add_argument('--workers', type=int, default=os.cpu_count() or 4)
ap.add_argument('--tmp', default='/tmp/nv')
A = ap.parse_args()

B = pathlib.Path(A.bundle); IMG = pathlib.Path(A.imgdir); TMP = pathlib.Path(A.tmp)
BIN = str(B/'bin'/'s5recomp-cover')
sys.path.insert(0, str(B/'py'))
import state_cmds as S  # noqa: E402
if not A.list:
    import verify  # noqa: E402  (remap_for only)
MEM2IMG = str(B/'py'/'mem2img.py'); INGEST = str(B/'py'/'command_ingest.py')
cat = json.load(open(B/'data'/'catalog.json'))
STARTS, LO, HI = cat['starts'], cat['lo'], cat['hi']
LOGIC = set(json.load(open(B/'data'/'logic.json')))
UNION3 = set(json.load(open(B/'data'/'union3.json')))
KNOWN = UNION3 | (set(json.load(open(B/'data'/'known.json'))) if (B/'data'/'known.json').exists() else set())
PLAN = json.load(open(B/'data'/'plan.json'))
ONLY_STRATS = set(A.strats.split(',')) - {''}
T0 = time.time(); DEADLINE = T0 + A.budget_min*60


def my_fixtures():
    fx = sorted(PLAN['fixtures'], key=lambda f: f['name'])
    if A.only:
        return [f for f in fx if f['name'] in A.only.split(',')]
    n = len(fx)
    return [fx[i] for i in range(n) if (i + A.wave) % A.nshards == A.shard]


def fork_turns(f):
    start, turns = f['start'], f['turns']
    if f['name'] in FRAGILE:
        return [start + 20 + (A.wave*37) % 100]
    span = min(turns, A.cap) - SHORT - 10
    if span < 60:
        return [start + max(1, span//2)]
    k = A.forks; step = span / k
    jit = ((A.wave*0.6180339887) % 1.0) * step
    out = sorted({start + int(jit + i*step) + 1 for i in range(k)})
    return [t for t in out if t < start + span + 1]


def ingest(jl, out):
    return subprocess.run([sys.executable, INGEST, str(jl), str(out)], capture_output=True, text=True).returncode == 0


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
res = {'gen': 'state_cmds', 'poke': not A.no_poke, 'wave': A.wave, 'shard': A.shard, 'nshards': A.nshards,
       'only': A.only, 'forks_per_fixture': A.forks, 'reps': A.reps, 'strats': A.strats, 'workers': A.workers, 'fixtures': [],
       'advances': [], 'probes': [], 'curve': [], 'union_logic': set(), 'turns_sim': 0, 'probe_wall': 0.0,
       'adv_wall': 0.0, 'stopped_by_budget': False}


def advance(f):
    """fork images at all fork turns of f: one run from the start image with the fixture's commands (or bootstrap)."""
    src = IMG/f['img']; turns = fork_turns(f)
    work = TMP/f'adv-{f["name"]}'; shutil.rmtree(work, ignore_errors=True); (work/'t').mkdir(parents=True)
    env = dict(os.environ, S5_REMAP=verify.remap_for(str(src), str(work/'rm')),
               MEMIMAGE_AT=','.join(f'{t}:{work}/t{t}.mem' for t in turns), TMPDIR=str(work/'t'))
    mode = 'idle'
    fc = B/'data'/'fxcmds'/f'{f.get("cmds") or f["name"]}.jsonl'
    if fc.exists() and fc.stat().st_size and ingest(fc, work/'fx.bin'):
        env['COMMANDS'] = str(work/'fx.bin'); mode = 'fixture'
    else:
        im = S.Img(str(src)); st = S.read_state(str(src))
        rows = [S.poke_row(st, st['turn'])] + S.bootstrap(st, st['turn'], max(turns))
        (work/'boot.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
        if ingest(work/'boot.jsonl', work/'boot.bin'):
            if not A.no_poke:
                S.add_pokes(work/'boot.bin', S.poke_writes(im, st['player']))
            env['COMMANDS'] = str(work/'boot.bin'); mode = 'bootstrap'
        del im
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
            if os.path.exists(str(src) + '.state.json'):
                shutil.copy2(str(src) + '.state.json', str(out) + '.state.json')
            forks.append((f, t, out))
    dt = time.perf_counter() - t0
    shutil.rmtree(work, ignore_errors=True)
    with lock:
        res['advances'].append({'fixture': f['name'], 'rc': p.returncode, 'wall': round(dt, 2), 'need': need,
                                'turns': turns, 'forks': len(forks), 'mode': mode})
        res['turns_sim'] += need; res['adv_wall'] += dt
    return forks


def plan_fork(f, t, img):
    """(state, strategies) of one fork"""
    try:
        st = S.read_state(str(img))
    except Exception:
        return None, []
    strats = [s for s in S.available(st) if not ONLY_STRATS or s in ONLY_STRATS or s == 'none']
    return st, strats


def probe(f, t, img, st, strategy, rep):
    key = f'{f["name"]}|{t}|{strategy}|{rep}|w{A.wave}'
    if time.time() > DEADLINE:
        res['stopped_by_budget'] = True
        return
    run = TMP/f'p-{f["name"]}-{t}-{strategy}-{rep}'; shutil.rmtree(run, ignore_errors=True); (run/'x').mkdir(parents=True)
    try:
        rng = random.Random(key)
        rows = [S.poke_row(st, st['turn'])] + S.gen(st, strategy, st['turn'], rng, SHORT)
        (run/'c.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
        if not ingest(run/'c.jsonl', run/'c.bin'):
            with lock: res['probes'].append({'k': key, 'rc': 'encode'})
            return
        if not A.no_poke:
            S.add_pokes(run/'c.bin', st['pokes'])
        cov = run/'o.cover'
        env = dict(os.environ, S5_REMAP=verify.remap_for(str(img), str(run/'rm')), COMMANDS=str(run/'c.bin'),
                   COVER=str(cov), TMPDIR=str(run/'x'))
        t0 = time.perf_counter()
        p = subprocess.run([BIN, str(img), str(SHORT)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        dt = time.perf_counter() - t0
        posted = next((int(l.split()[0]) for l in p.stderr.splitlines() if l.endswith('commands posted')), 0)
        hits = reduce_cover(cov) if cov.exists() else set()
        lg = hits & LOGIC
        h = hashlib.sha256(''.join(f'{a:x}\n' for a in sorted(hits)).encode()).hexdigest()[:16]
        with lock:
            new = sorted(lg - KNOWN - res['union_logic'])
            res['probes'].append({'k': key, 'rc': p.returncode, 'wall': round(dt, 2), 'nf': len(hits), 'nl': len(lg),
                                  'h': h, 'n_cmds': len(rows) - 1, 'posted': posted,
                                  'events': sorted({r['id'] for r in rows[1:]}),
                                  'new': [f'0x{a:06x}' for a in new]})
            res['union_logic'] |= lg; res['turns_sim'] += SHORT; res['probe_wall'] += dt
            res['curve'].append([round(time.time() - T0, 1), len(res['probes']), len(res['union_logic'] - KNOWN)])
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

    todo: queue.Queue = queue.Queue()
    for f in fxs:
        todo.put(f)
    done_producers = [0]

    def producer():
        while True:
            try:
                f = todo.get_nowait()
            except queue.Empty:
                break
            if time.time() > DEADLINE:
                break
            try:
                fq.put(advance(f))
            except Exception as e:  # one broken fixture must not stop the shard
                with lock: res['advances'].append({'fixture': f['name'], 'rc': f'exc {type(e).__name__}', 'forks': 0})
                fq.put([])
        with lock:
            done_producers[0] += 1
            if done_producers[0] == A.adv_par:
                fq.put(None)
    for _ in range(A.adv_par):
        threading.Thread(target=producer, daemon=True).start()
    with ThreadPoolExecutor(A.workers) as ex:
        while True:
            forks = fq.get()
            if forks is None:
                break
            futs = []
            for (f, t, img) in forks:
                st, strats = plan_fork(f, t, img)
                if st is None:
                    continue
                im = S.Img(str(img)); st['pokes'] = S.poke_writes(im, st['player']); del im
                futs += [ex.submit(probe, f, t, img, st, s, r) for s in strats for r in range(A.reps)]
            for fu in futs:
                fu.result()
            for (_, _, img) in forks:
                img.unlink(missing_ok=True); pathlib.Path(str(img) + '.state.json').unlink(missing_ok=True)
            n = len(res['probes'])
            print(f'  fixture {len(res["advances"])}/{len(fxs)}: forks={len(forks)} probes_total={n} '
                  f'new={len(res["union_logic"] - KNOWN)} t={time.time()-T0:.0f}s', flush=True)
            if A.out:   # partial result after every fixture (a killed run keeps what it found)
                snap = dict(res, union_logic=[f'0x{a:06x}' for a in sorted(res['union_logic'])])
                pathlib.Path(A.out + '.part').write_text(json.dumps(snap))
    res['union_logic'] = [f'0x{a:06x}' for a in sorted(res['union_logic'])]
    res['union_new'] = sorted({a for p in res['probes'] for a in p.get('new', [])})
    res['elapsed'] = round(time.time() - T0, 2)
    ok = sum(1 for p in res['probes'] if p.get('rc') == 0)
    pathlib.Path(A.out).write_text(json.dumps(res))
    print(f'done: probes={len(res["probes"])} ok={ok} forks={sum(a["forks"] for a in res["advances"])} '
          f'turns_sim={res["turns_sim"]} elapsed={res["elapsed"]}s new={len(res["union_new"])} '
          f'budget_stop={res["stopped_by_budget"]}', flush=True)


if __name__ == '__main__':
    main()
