#!/usr/bin/env python3
"""Cached native coverage map (coverage.yml).

extract   : stream the per-turn block traces of one fixture (written by s5recomp TRACE=<dir>/{turn}.bin) into a
            per-pc aggregate {pc: [blocks, first turn]} and delete each trace once it is complete (a trace is complete
            when a later turn's trace exists or s5recomp exited). Catalog-independent, so function attribution and the
            "cited in crates/s5clean/src" check can be redone cheaply on every run.
aggregate : rebuild COVERAGE.md from all per-fixture aggregates with siedler-sim-rs tools/coverage_map.py itself
            (its collect_fixtures is replaced by a reader of the aggregates; catalog, filters, cited check and report
            are the upstream code, so the output matches `coverage_map.sh` for the same sampled turns).
"""
import argparse, array, bisect, collections, glob, gzip, importlib.util, json, os, sys, time

TRACE_REC = 4 + 32 + 4 * 5 + 8 * 16   # = tools/coverage_map.py TRACE_REC
DWORDS = TRACE_REC // 4
EXE_LO, EXE_HI = 0x400000, 0x5E0000


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # zombie (exited, not yet reaped by the shell) counts as dead
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


def extract(a):
    pcs = {}
    turns = []
    def turn_files():
        out = []
        for fn in os.listdir(a.dir):
            stem = fn[:-4]
            if fn.endswith(".bin") and stem.isdigit():
                out.append(int(stem))
        return sorted(out)
    def take(t):
        path = os.path.join(a.dir, f"{t}.bin")
        arr = array.array("I")
        with open(path, "rb") as fh:
            arr.frombytes(fh.read())
        for pc, n in collections.Counter(arr[0::DWORDS]).items():
            if EXE_LO <= pc < EXE_HI:
                e = pcs.get(pc)
                if e is None:
                    pcs[pc] = [n, t]
                else:
                    e[0] += n
                    if t < e[1]:
                        e[1] = t
        os.remove(path)
        turns.append(t)
    while True:
        running = alive(a.pid)
        ts = turn_files()
        ready = ts if not running else ts[:-1]
        for t in ready:
            take(t)
        if not running:
            break
        time.sleep(2)
    data = {"fixture": a.fixture, "key": a.key, "start": a.start, "end": a.end, "step": a.step,
            "turns": sorted(turns), "pcs": {str(pc): v for pc, v in pcs.items()}}  # first-seen order (= upstream tie order)
    tmp = a.out + ".tmp"
    with gzip.open(tmp, "wt") as fh:
        json.dump(data, fh, separators=(",", ":"))
    os.replace(tmp, a.out)
    print(f"coverage_trace: {a.fixture} {len(turns)} traces, {len(pcs)} distinct pcs")


def aggregate(a):
    tools = os.path.join(a.sim, "tools")
    spec = importlib.util.spec_from_file_location("coverage_map", os.path.join(tools, "coverage_map.py"))
    cm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cm)
    files = {}
    for p in sorted(glob.glob(os.path.join(a.indir, "trace-*.json.gz"))):
        with gzip.open(p, "rt") as fh:
            d = json.load(fh)
        f = d["fixture"]
        # several assets of one fixture (old key + new key during an upload): prefer the wanted key
        if f not in files or d.get("key") in a.want:
            files[f] = d
    if a.only:
        files = {k: v for k, v in files.items() if k in a.only}

    def collect_fixtures(args, entries, starts):
        fixtures = collections.OrderedDict()
        for name in sorted(files):
            d = files[name]
            total = collections.Counter()
            first = {}
            for pc_s, (n, t) in d["pcs"].items():
                pc = int(pc_s)
                i = bisect.bisect_right(starts, pc) - 1
                if i < 0:
                    continue
                e = starts[i]
                total[e] += n
                if e not in first or t < first[e]:
                    first[e] = t
            fixtures[name] = (d["turns"], total, first)
        return fixtures, set()

    cm.collect_fixtures = collect_fixtures
    sys.argv = ["coverage_map.py", "--depth", "0", "--top", str(a.top), "--out", a.out]
    cm.main()
    stale = sorted(f for f, d in files.items() if a.want and d.get("key") not in a.want)
    print("fixtures=" + " ".join(f"{f}:{files[f]['start']}-{files[f]['end']}" for f in sorted(files)))
    print("stale=" + " ".join(stale))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--dir", required=True); e.add_argument("--pid", type=int, required=True)
    e.add_argument("--fixture", required=True); e.add_argument("--key", required=True)
    e.add_argument("--start", type=int, required=True); e.add_argument("--end", type=int, required=True)
    e.add_argument("--step", type=int, required=True); e.add_argument("--out", required=True)
    g = sub.add_parser("aggregate")
    g.add_argument("--sim", required=True); g.add_argument("--indir", required=True)
    g.add_argument("--out", required=True); g.add_argument("--top", type=int, default=50)
    g.add_argument("--want", nargs="*", default=[], help="current keys")
    g.add_argument("--only", nargs="*", default=[], help="restrict to these fixtures")
    a = ap.parse_args()
    extract(a) if a.cmd == "extract" else aggregate(a)


if __name__ == "__main__":
    main()
