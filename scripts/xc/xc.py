#!/usr/bin/env python3
"""Work list of the per-call differential (crates/s5xcheck capture tests).

  xc.py merge <dir> <sha> <out>       per-scenario capture JSONs -> worklist.json (kinds x scenarios)
  xc.py regressions <old> <new>       (kind, scenario) pairs that compared clean in <old> and fail in <new> (JSON list)
  xc.py pick <worklist> <n> [--claim] [--limit=L]
                                      up to n failing kinds no open PR, claim or exhausted attempt covers (JSON list);
                                      --limit: at most L live claims of this pool (GITHUB_REPOSITORY) in all
  xc.py scenarios <worklist> <kind>   names of the scenarios where <kind> was sampled, failing ones first
  xc.py failing <worklist> <kind>     names of the scenarios where <kind> fails
  xc.py check <base> <new> <kind>     a worker's verdict: <kind> clean everywhere, nothing else newly failing
  xc.py branch <kind>                 the fix branch of a kind
  xc.py issue <kind> <op> [sha]       tracking issue of a kind: claim | unclaim | attempt <census sha> |
                                      merged <census sha> | close

The JSON files hold game state (diff lines); they stay on the runner or in the private release, never in a log.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ATTEMPTS = 3  # failed worker attempts per kind (on the same census) before it waits for a new census
CLAIM_H = 7   # a claim (a worker picked the kind) blocks other picks this long; a worker job lasts at most 6 h
PROJECT = os.environ.get("XC_PROJECT", "Kofi55533/siedler-sim-rs")
# the tracking issues live in the main pipeline repo for every pool (forks pick from the same work list)
AGENT = os.environ.get("XC_AGENT", "Kofi55533/siedler-agent")
POOL = os.environ.get("GITHUB_REPOSITORY", "local")  # the repo whose runners do the work (main or a pool)


def load(path):
    return json.loads(Path(path).read_text())


def branch(kind):
    return "xc/" + kind.lower().replace("_", "-")


def merge(directory, sha, out):
    kinds, scenarios = {}, {}
    for f in sorted(Path(directory).glob("*.json")):
        doc = load(f)
        name = f.stem
        scenarios[name] = {"turns": doc["turns"], "entries": doc["entries"], "import_errors": len(doc["import_errors"])}
        for kind, g in doc["groups"].items():
            k = kinds.setdefault(kind, {"entries": 0, "samples": 0, "failing": 0, "scenarios": {}})
            k["entries"] += g["entries"]
            k["samples"] += g["samples"]
            k["failing"] += g["failing"]
            k["scenarios"][name] = {"entries": g["entries"], "samples": g["samples"], "failing": g["failing"],
                                    "examples": g["examples"]}
    doc = {"sha": sha, "function": "0x57BF33", "scenarios": scenarios, "kinds": kinds,
           "summary": {"kinds": len(kinds), "failing_kinds": sum(1 for k in kinds.values() if k["failing"]),
                       "samples": sum(k["samples"] for k in kinds.values()),
                       "failing": sum(k["failing"] for k in kinds.values())}}
    Path(out).write_text(json.dumps(doc, indent=1))
    s = doc["summary"]
    print(f"worklist {sha[:8]}: {len(scenarios)} scenarios, {s['kinds']} kinds, {s['failing_kinds']} failing kinds, "
          f"{s['failing']}/{s['samples']} samples differ")


def pairs(doc):
    """(kind, scenario) -> (samples, failing)"""
    return {(kind, sc): (v["samples"], v["failing"]) for kind, k in doc["kinds"].items()
            for sc, v in k["scenarios"].items()}


def regressions(old, new):
    o, n = pairs(load(old)), pairs(load(new))
    out = [{"kind": k, "scenario": s} for (k, s), (samples, failing) in sorted(n.items())
           if failing and (k, s) in o and o[(k, s)][0] and not o[(k, s)][1]]
    print(json.dumps(out))


def gh(*args):
    r = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


def fields(body):
    """tracking issue body: "census <sha12>", "attempts <n>", "claimed <epoch> <run> <pool>", "merged <sha12>" lines"""
    return dict(line.split(" ", 1) for line in (body or "").splitlines() if " " in line)


def render(f):
    return "".join(f"{k} {v}\n" for k, v in f.items())


def tried(body, sha):
    """failed attempts recorded in a tracking issue body for this census sha"""
    f = fields(body)
    return int(f.get("attempts", "0")) if f.get("census") == sha[:12] else 0


def claimed(body, now, pool=None):
    """a live claim (of any pool, or of <pool> only)"""
    c = fields(body).get("claimed", "").split()
    return bool(c) and now - int(c[0]) < CLAIM_H * 3600 and (pool is None or c[2:3] == [pool])


def tracking():
    """open tracking issues: kind -> (number, body)"""
    out = {}
    for page in range(1, 4):
        issues = gh(f"repos/{AGENT}/issues?state=open&labels=xc-kind&per_page=100&page={page}") or []
        for i in issues:
            out.setdefault(i["title"].removeprefix("[xc] "), (i["number"], i["body"] or ""))
        if len(issues) < 100:
            break
    return out


def issue(kind, op, sha=None):
    num, body = tracking().get(kind, (None, ""))
    f = fields(body)
    if op == "claim":
        f["claimed"] = f"{int(time.time())} {os.environ.get('GITHUB_RUN_ID', 'local')} {POOL}"
    elif op == "unclaim":
        f.pop("claimed", None)
    elif op == "attempt":
        f = {"census": sha[:12], "attempts": str(tried(body, sha) + 1)}
    elif op == "merged":  # the issue stays open: the work list measured on <sha> still lists the kind as failing
        f = {"merged": sha[:12]}
    elif op == "close":
        if num:
            gh("-X", "PATCH", f"repos/{AGENT}/issues/{num}", "-f", "state=closed", "-f", "body=" + render({}))
        return
    if num:
        gh("-X", "PATCH", f"repos/{AGENT}/issues/{num}", "-f", "body=" + render(f))
    else:
        gh(f"repos/{AGENT}/issues", "-f", f"title=[xc] {kind}", "-f", "body=" + render(f), "-f", "labels[]=xc-kind")


def pick(worklist, n, claim=False, limit=None):
    doc = load(worklist)
    taken = set()
    for page in range(1, 6):
        prs = gh(f"repos/{PROJECT}/pulls?state=open&per_page=100&page={page}") or []
        taken |= {p["head"]["ref"] for p in prs}
        if len(prs) < 100:
            break
    issues, now = tracking(), time.time()
    if limit is not None:  # this pool's slots: its live claims are its running worker jobs
        n = min(n, limit - sum(1 for _, body in issues.values() if claimed(body, now, POOL)))
    ranked = []
    for kind, k in doc["kinds"].items():
        if not k["failing"] or kind.startswith("task type ") or branch(kind) in taken:
            continue
        body = issues.get(kind, (None, ""))[1]
        # the attempt counter restarts with every census sha (new base, new examples)
        merged = fields(body).get("merged") == doc["sha"][:12]  # fixed since this census; the next one re-measures
        if merged or claimed(body, now) or tried(body, doc["sha"]) >= ATTEMPTS:
            continue
        failing_scenarios = sum(1 for v in k["scenarios"].values() if v["failing"])
        ranked.append((-failing_scenarios, -k["failing"], kind))
    ranked.sort()
    kinds = [kind for _, _, kind in ranked[:max(n, 0)]]
    if claim:
        for kind in kinds:
            issue(kind, "claim")
    print(json.dumps(kinds))


def scenarios(worklist, kind, only_failing=False):
    k = load(worklist)["kinds"].get(kind, {"scenarios": {}})
    items = sorted(k["scenarios"].items(), key=lambda kv: (-kv[1]["failing"], kv[0]))
    print(" ".join(name for name, v in items if v["samples"] and (v["failing"] or not only_failing)))


def check(base, new, kind):
    """base/new: directories with per-scenario JSONs (the kind-only runs plus full runs of the failing scenarios)."""
    ok, lines = True, []
    samples = failing = 0
    for f in sorted(Path(new, "only").glob("*.json")):
        g = load(f)["groups"].get(kind)
        if g:
            samples += g["samples"]
            failing += g["failing"]
            if g["failing"]:
                lines.append(f"{kind} still differs in {f.stem}: {g['failing']}/{g['samples']}")
    if not samples:
        ok = False
        lines.append(f"{kind}: no sampled call (scenario runs missing or crashed)")
    if failing:
        ok = False
    for f in sorted(Path(new, "full").glob("*.json")):
        b = Path(base, "full", f.name)
        if not b.exists():
            continue
        bg, ng = load(b)["groups"], load(f)["groups"]
        for other, g in ng.items():
            if other != kind and g["failing"] and other in bg and bg[other]["samples"] and not bg[other]["failing"]:
                ok = False
                lines.append(f"regression: {other} now differs in {f.stem} ({g['failing']}/{g['samples']})")
    for f in sorted(Path(base, "full").glob("*.json")):
        if not Path(new, "full", f.name).exists():
            ok = False
            lines.append(f"full run of {f.stem} missing after the change (crash?)")
    lines.append(f"{kind}: {samples - failing}/{samples} sampled calls identical")
    print("\n".join(lines))
    print("VERDICT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv):
    cmd = argv[1]
    if cmd == "merge":
        merge(argv[2], argv[3], argv[4])
    elif cmd == "regressions":
        regressions(argv[2], argv[3])
    elif cmd == "pick":
        lim = [int(a.split("=")[1]) for a in argv if a.startswith("--limit=")]
        pick(argv[2], int(argv[3]), claim="--claim" in argv, limit=lim[0] if lim else None)
    elif cmd == "scenarios":
        scenarios(argv[2], argv[3])
    elif cmd == "failing":
        scenarios(argv[2], argv[3], only_failing=True)
    elif cmd == "check":
        return check(argv[2], argv[3], argv[4])
    elif cmd == "issue":
        issue(argv[2], argv[3], argv[4] if len(argv) > 4 else None)
    elif cmd == "branch":
        print(branch(argv[2]))
    else:
        raise SystemExit(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
