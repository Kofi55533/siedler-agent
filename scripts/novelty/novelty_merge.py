#!/usr/bin/env python3
"""Union the shard results of one or more waves against union3 + the known set so far.
usage: novelty_merge.py --known known.json [--box-known FILE ...] --out merged.json --known-out known.json R1.json[.gz] ...
known.json: {"addrs": [...hex...], "first": {addr: probe key}}  (logic functions found by the search outside union3)
--box-known: extra known sources on the box (loop_new.jsonl lines with "addr", or a JSON list) so counts are
relative to everything already found locally. Prints counts only (runner logs are public)."""
import argparse, gzip, json, pathlib
ap = argparse.ArgumentParser()
ap.add_argument('--known', default='')
ap.add_argument('--box-known', nargs='*', default=[])
ap.add_argument('--out', required=True)
ap.add_argument('--known-out', required=True)
ap.add_argument('results', nargs='+')
A = ap.parse_args()

def load(p):
    b = pathlib.Path(p).read_bytes()
    return json.loads(gzip.decompress(b) if p.endswith('.gz') else b)

known = {'addrs': [], 'first': {}}
if A.known and pathlib.Path(A.known).exists():
    known = load(A.known)
prev = set(known['addrs'])
box = set()
for p in A.box_known:
    t = pathlib.Path(p).read_text()
    if p.endswith('.jsonl'):
        box |= {json.loads(l)['addr'] for l in t.splitlines() if l.strip()}
    else:
        d = json.loads(t); box |= set(d if isinstance(d, list) else d.get('addrs', d.get('new_addrs', [])))
waves, shards, probes, ok, forks, turns, el = set(), 0, 0, 0, 0, 0, []
first = {}
fails = 0
partial, bad = 0, 0
for p in A.results:
    # tolerate partial shard results (killed/promoted .part: no 'elapsed', maybe missing lists) and unreadable files
    try:
        r = load(p)
    except Exception:
        bad += 1; continue
    shards += 1; waves.add(r.get('wave'))
    partial += bool(r.get('partial') or 'elapsed' not in r)
    pr = r.get('probes') or []; adv = r.get('advances') or []
    probes += len(pr); ok += sum(1 for x in pr if x.get('rc') == 0)
    fails += sum(1 for a in adv if a.get('rc') != 0)
    forks += sum(a.get('forks', 0) for a in adv); turns += r.get('turns_sim', 0)
    if r.get('elapsed') is not None:
        el.append(r['elapsed'])
    for x in pr:
        for a in x.get('new', []):
            first.setdefault(a, x['k'])
wave_new = set(first)
fresh = wave_new - prev
for a in sorted(fresh):
    known['first'][a] = first[a]
known['addrs'] = sorted(prev | wave_new)
m = {'waves': sorted(w for w in waves if w is not None), 'shards': shards, 'shards_partial': partial,
     'files_unreadable': bad, 'probes': probes, 'probes_ok': ok, 'adv_fail': fails, 'forks': forks,
     'turns_sim': turns, 'shards_with_elapsed': len(el), 'shard_elapsed_max': max(el) if el else 0, 'shard_elapsed_sum': round(sum(el), 1),
     'new_vs_union3_this_wave': len(wave_new), 'new_vs_known_this_wave': len(fresh),
     'known_vs_union3_total': len(known['addrs']),
     'not_in_box_runs': len(set(known['addrs']) - box) if box else None,
     'fresh_vs_box_this_wave': len(fresh - box) if box else None,
     'fresh': {a: first[a] for a in sorted(fresh)}}
pathlib.Path(A.out).write_text(json.dumps(m, indent=1))
pathlib.Path(A.known_out).write_text(json.dumps(known))
print(json.dumps({k: v for k, v in m.items() if k != 'fresh'}))
