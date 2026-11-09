#!/usr/bin/env python3
"""Box side: build the novelty bundle (binary, helper .py, catalog, logic set, union3, pools, plan) and an
image dir (symlinks <name>/start.img{,.state.json}) from the box's fuzz-20261109 inputs.
usage: make_bundle.py OUTDIR [KNOWN.json]   -> OUTDIR/bundle, OUTDIR/images"""
import json, os, pathlib, re, shutil, sys
F = pathlib.Path('/workspace/siedler/fuzz-20261109')
FJ = '/workspace/siedler/siedler-forschung/cloud-re/results/rust_handoff/source/functions.jsonl'
INV = '/workspace/siedler/v2fleet/scripts/scoreboard/function_inventory.json'
UNION3 = '/workspace/siedler/coverage-20261108/union3.json'
REC = pathlib.Path('/workspace/siedler/codex-box/wt/clean-integration/recomp')
out = pathlib.Path(sys.argv[1]); b = out/'bundle'; im = out/'images'
for d in ('bin', 'py', 'data'):
    (b/d).mkdir(parents=True, exist_ok=True)
shutil.copy2(F/'bin/s5recomp-cover', b/'bin/s5recomp-cover')
for s in (REC/'verify.py', REC/'mem2img.py', F/'vendor/command_ingest.py', pathlib.Path(__file__).resolve().parent/'state_cmds.py'):
    shutil.copy2(s, b/'py'/s.name)
rows = []
for line in open(FJ):
    if '"addr"' not in line:
        continue
    try:
        o = json.loads(line)
    except Exception:
        continue
    a = int(o['addr'], 16) if isinstance(o['addr'], str) else int(o['addr'])
    lo, hi = a, a + 1
    br = o.get('body_ranges') or []
    if br:
        lo = min(int(x, 16) for x, _ in br); hi = max(int(y, 16) for _, y in br)
    rows.append((a, lo, hi))
rows.sort()
json.dump({'starts': [r[0] for r in rows], 'lo': [r[1] for r in rows], 'hi': [r[2] for r in rows]}, open(b/'data/catalog.json', 'w'))
logic = sorted(int(re.match(r'[0-9a-f]+', r).group(0), 16) for r in json.load(open(INV))['functions'] if r.endswith('L'))
json.dump(logic, open(b/'data/logic.json', 'w'))
json.dump(sorted(int(x, 16) for x in json.load(open(UNION3))['logic_hit']), open(b/'data/union3.json', 'w'))
pools = {}
for k in ('camp-crawford', 'map-winterisland', 'combat'):
    pools[k] = [i for i in json.load(open(F/f'pilot/pool-{k}.json'))['entity_ids'] if i >= 1000]
json.dump(pools, open(b/'data/pools.json', 'w'))
# fixture command streams (the forks replay them): first non-empty <name>/commands.jsonl under the box roots
import subprocess
idx = {}
for p in subprocess.run(['find', '/workspace/siedler/coverage-20261108', '/workspace/siedler/artifacts', '/workspace/siedler/codex-box/wt/clean-integration',
                         '-name', 'commands.jsonl', '-size', '+0'], capture_output=True, text=True).stdout.split():
    idx.setdefault(os.path.basename(os.path.dirname(p)), p)
(b/'data/fxcmds').mkdir(exist_ok=True)
# known = every logic function any earlier box/runner novelty run found (union3 is separate)
if len(sys.argv) > 2:
    shutil.copy2(sys.argv[2], b/'data/known.json')
by = {}
allfx = json.load(open(F/'pilot/fixtures_with_img.json')) + [dict(f, start=f.get('start', 0), turns=f.get('turns', 5000)) for f in json.load(open(F/'pilot/novelty/extra_fixtures.json'))]
for f in allfx:
    if not os.path.exists(f['img']):
        continue
    c = idx.get(f['name'])
    h = None
    if c:
        import hashlib
        h = hashlib.sha256(open(c, 'rb').read()).hexdigest()[:12]
    k = (f['img'], h)   # same image with a different command stream = a different game
    if k not in by or f['turns'] > by[k]['turns']:
        by[k] = {'name': f['name'], 'img': f['img'], 'start': f['start'], 'turns': f['turns'], 'cmds_src': c}
fx = []
for f in sorted(by.values(), key=lambda f: f['name']):
    d = im/f['name']; d.mkdir(parents=True, exist_ok=True)
    for suf in ('', '.state.json'):
        l = d/('start.img' + suf)
        if l.is_symlink() or l.exists():
            l.unlink()
        if os.path.exists(f['img'] + suf):
            l.symlink_to(f['img'] + suf)
    if f['cmds_src']:
        shutil.copy2(f['cmds_src'], b/'data/fxcmds'/f'{f["name"]}.jsonl')
    fx.append({'name': f['name'], 'img': f'{f["name"]}/start.img', 'start': f['start'], 'turns': f['turns'],
               'has_cmds': bool(f['cmds_src'])})
json.dump({'fixtures': fx}, open(b/'data/plan.json', 'w'), indent=1)
print(f'bundle: {len(rows)} functions, {len(logic)} logic, {len(fx)} fixtures ({sum(f["has_cmds"] for f in fx)} with commands)')
