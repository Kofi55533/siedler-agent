#!/usr/bin/env python3
"""Work list of the per-call differential (crates/s5xcheck capture tests).

  xc.py merge <dir> <sha> <out> [meta]
                                      per-scenario capture JSONs -> worklist.json (kinds x scenarios); meta: a directory
                                      with fn_calls.json (static calls between class-A functions) and cited.txt (native
                                      addresses the clean code cites), the order of the function units
  xc.py regressions <old> <new>       (kind, scenario) pairs that compared clean in <old> and fail in <new> (JSON list)
  xc.py pick <worklist> <n> [--claim] [--limit=L]
                                      up to n failing kinds no open PR, claim or exhausted attempt covers (JSON list);
                                      --limit: at most L live claims of this pool (GITHUB_REPOSITORY) in all
  xc.py scenarios <worklist> <kind>   names of the scenarios where <kind> was sampled, failing ones first
  xc.py failing <worklist> <kind>     names of the scenarios where <kind> fails
  xc.py check <base> <new> <kind>     a worker's verdict: <kind> clean everywhere, nothing else newly failing (a
                                      function kind FN_<address> also bound and observed)
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
CENSUS = 3   # version of what the census measures (2: function units, 3: their callers); a work list of another version is re-measured
CLAIM_H = 7   # a claim (a worker picked the kind) blocks other picks this long; a worker job lasts at most 6 h
PROJECT = os.environ.get("XC_PROJECT", "Kofi55533/siedler-sim-rs")
# the tracking issues live in the main pipeline repo for every pool (forks pick from the same work list)
AGENT = os.environ.get("XC_AGENT", "Kofi55533/siedler-agent")
POOL = os.environ.get("GITHUB_REPOSITORY", "local")  # the repo whose runners do the work (main or a pool)


def load(path):
    return json.loads(Path(path).read_text())


def branch(kind):
    return "xc/" + kind.lower().replace("_", "-")


# the native functions the census measures by type instead (capture-ticks TICK_<CLASS>_<type>, ExecuteTask's task
# kinds): their FN_ kinds are no work unit of their own
COVERED = {"FN_4AF630", "FN_4A6A1B", "FN_511DF9", "FN_57BF33"}


def point(kind):
    """the capture point (test binary) that measures a kind: one function per address (capture-functions), the entity
    ticks per type, else ExecuteTask's task kinds"""
    if kind.startswith("FN_"):
        return "capture-functions"
    return "capture-ticks" if kind.startswith("TICK_") else "capture-execute-task"


def address(kind):
    return kind.removeprefix("FN_").lstrip("0").lower()


def fn_state(k):
    """a function kind's state: port (no binding yet), covered (no binding, but every call of it runs inside verified
    functions, see covered()), fix (bound, a sampled call differs), observe (bound, the sampled calls change nothing
    the comparison sees, or none was sampled), done (bound, identical and observed)"""
    if k.get("unbound"):
        return "covered" if k.get("covered") else "port"
    if k["failing"]:
        return "fix"
    return "done" if k["samples"] and k.get("observed", 0) else "observe"


def merge(directory, sha, out, meta=None):
    kinds, scenarios, functions = {}, {}, set()
    for f in sorted(Path(directory).glob("*.json")):
        doc = load(f)
        name = f.stem
        functions.update(doc["function"].split(","))
        scenarios[name] = {"turns": doc["turns"], "entries": doc["entries"], "import_errors": len(doc["import_errors"]),
                           "missing_points": doc.get("missing_points", [])}
        for kind, g in doc["groups"].items():
            k = kinds.setdefault(kind, {"point": point(kind), "entries": 0, "samples": 0, "failing": 0, "scenarios": {}})
            k["entries"] += g["entries"]
            k["samples"] += g["samples"]
            k["failing"] += g["failing"]
            if "observed" in g:
                k["observed"] = k.get("observed", 0) + g["observed"]
            if g.get("unbound"):
                k["unbound"] = True
            k["scenarios"][name] = {"entries": g["entries"], "samples": g["samples"], "failing": g["failing"],
                                    "examples": g["examples"]}
            if g.get("callers"):
                c = k.setdefault("callers", {})
                for caller, n in g["callers"].items():
                    c[caller] = c.get(caller, 0) + n
    fns = {kind: k for kind, k in kinds.items() if kind.startswith("FN_")}
    covered(fns)
    if meta and Path(meta, "fn_calls.json").is_file():
        # a function's executed class-A callees (static direct calls) that have no binding yet
        calls = load(Path(meta, "fn_calls.json"))["calls"]
        executed = {address(kind): kind for kind in fns}
        for kind, k in fns.items():
            callees = [executed[c] for c in calls.get(address(kind), []) if c in executed]
            k["callees"] = len(callees)
            k["callees_open"] = sum(1 for c in callees if fns[c].get("unbound"))
            k["callers_static_open"] = 0
        for kind in fns:  # the reverse (top-down without caller counts): its executed callers without a binding
            if fns[kind].get("unbound"):
                for c in calls.get(address(kind), []):
                    if c in executed:
                        fns[executed[c]]["callers_static_open"] += 1
    if meta and Path(meta, "cited.txt").is_file():
        cited = set(Path(meta, "cited.txt").read_text().split())
        for kind, k in fns.items():
            k["cited"] = address(kind) in cited  # the clean code names the address: maybe ported, binding missing
    states = [fn_state(k) for kind, k in fns.items() if kind not in COVERED]
    doc = {"sha": sha, "census": CENSUS, "function": ",".join(sorted(functions)), "scenarios": scenarios, "kinds": kinds,
           "summary": {"kinds": len(kinds), "failing_kinds": sum(1 for k in kinds.values() if k["failing"]),
                       "samples": sum(k["samples"] for k in kinds.values()),
                       "failing": sum(k["failing"] for k in kinds.values()),
                       "functions": {s: states.count(s) for s in ("port", "covered", "fix", "observe", "done")}}}
    Path(out).write_text(json.dumps(doc, indent=1))
    s = doc["summary"]
    print(f"worklist {sha[:8]}: {len(scenarios)} scenarios, {s['kinds']} kinds, {s['failing_kinds']} failing kinds, "
          f"{s['failing']}/{s['samples']} samples differ; executed functions: "
          + ", ".join(f"{n} {st}" for st, n in s["functions"].items()))


def covered(fns):
    """the function kinds whose every call runs inside verified functions (Group::callers of capture-functions: the
    innermost observed function enclosing each call): inside a bound function identical at its sampled calls, or
    inside a function covered the same way. Such a function needs no unit of its own: its behaviour is the part of
    the verified caller's port that does its job (a native container walk, an accessor, ...), however that port is
    written. Each kind also gets callers_open (its calls inside functions still to port, or from the turn itself)
    and children_open (the functions still to port called inside it), the top-down order of the rest."""
    verified = {kind for kind, k in fns.items()
                if fn_state(k) == "done" or (kind in COVERED and k["samples"] and not k["failing"])}
    cov = set()
    while True:
        new = {kind for kind, k in fns.items() if k.get("unbound") and kind not in cov and k.get("callers")
               and all(c in verified or c in cov for c in k["callers"])}
        if not new:
            break
        cov |= new
    def open_(kind):  # still to port: no binding and not covered (ROOT, the turn itself, is no function)
        return kind in fns and fns[kind].get("unbound") and kind not in cov
    for kind, k in fns.items():
        if kind in cov:
            k["covered"] = True
        if "callers" in k:
            k["callers_open"] = sum(n for c, n in k["callers"].items() if open_(c))
    for kind, k in fns.items():
        if open_(kind):
            for c in k.get("callers", {}):
                if open_(c):
                    fns[c]["children_open"] = fns[c].get("children_open", 0) + 1


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
    for page in range(1, 51):
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


def todo(kind, k):
    """a work unit: a failing kind, or an executed function without binding (port) or with differing calls (fix)"""
    # unnamed groups (a task type or entity type the data does not name) are no work unit
    if kind.startswith("task type ") or "_type" in kind or kind in COVERED:
        return False
    if kind.startswith("FN_"):
        return fn_state(k) in ("port", "fix")
    return bool(k["failing"])


def rank(kind, k):
    """task kinds first: a settler's or animal's tick runs its current task, so a task handler that differs shows in
    the ticks of every type running it; fixing the handler is the narrower job and clears those as well. Then the
    entity ticks and the bound functions whose calls differ, then the functions to port from the top of the call tree
    down: the ones called only from verified functions or the turn itself first (their port goes into the clean
    world as the caller's part; porting one covers the helpers inside it, see covered()), then the fewest open
    functions inside (a smaller job), the ones the clean code already cites (maybe only the binding is missing) and
    the ones run in the most scenarios. Without caller counts (an older census) the static call graph stands in."""
    scen = sum(1 for v in k["scenarios"].values() if (v["entries"] if k.get("unbound") else v["failing"]))
    if not kind.startswith("FN_") or k["failing"]:
        return (point(kind) != "capture-execute-task", 0, -scen, -k["failing"], kind)
    if "callers_open" in k:
        return (2, k["callers_open"] > 0, k.get("children_open", 0), not k.get("cited"), -scen, -k["entries"], kind)
    return (2, k.get("callers_static_open", 0) > 0, k.get("callees_open", 0), not k.get("cited"), -scen, -k["entries"],
            kind)


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
        if not todo(kind, k) or branch(kind) in taken:
            continue
        body = issues.get(kind, (None, ""))[1]
        # the attempt counter restarts with every census sha (new base, new examples)
        merged = fields(body).get("merged") == doc["sha"][:12]  # fixed since this census; the next one re-measures
        if merged or claimed(body, now) or tried(body, doc["sha"]) >= ATTEMPTS:
            continue
        ranked.append(rank(kind, k))
    ranked.sort()
    kinds = [r[-1] for r in ranked[:max(n, 0)]]
    if claim:
        for kind in kinds:
            issue(kind, "claim")
        # a kind this census no longer lists as work (fixed, or its function is no longer called) closes its issue, so
        # the open issues stay the work in progress
        for kind, (num, body) in issues.items():
            k = doc["kinds"].get(kind)
            if not doc.get("partial") and not claimed(body, now) and (k is None or not todo(kind, k)):
                gh("-X", "PATCH", f"repos/{AGENT}/issues/{num}", "-f", "state=closed")
    print(json.dumps(kinds))


def scenarios(worklist, kind, only_failing=False):
    """a function without binding yet: the scenarios that call it, the most calls first (all of them its work)"""
    k = load(worklist)["kinds"].get(kind, {"scenarios": {}})
    if k.get("unbound"):
        items = sorted(k["scenarios"].items(), key=lambda kv: (-kv[1]["entries"], kv[0]))
        print(" ".join(name for name, v in items if v["entries"]))
        return
    items = sorted(k["scenarios"].items(), key=lambda kv: (-kv[1]["failing"], kv[0]))
    print(" ".join(name for name, v in items if v["samples"] and (v["failing"] or not only_failing)))


def check(base, new, kind):
    """base/new: directories with per-scenario JSONs (the kind-only runs plus full runs of the failing scenarios)."""
    ok, lines = True, []
    samples = failing = observed = entries = 0
    unbound = False
    for f in sorted(Path(new, "only").glob("*.json")):
        g = load(f)["groups"].get(kind)
        if g:
            samples += g["samples"]
            failing += g["failing"]
            observed += g.get("observed", 0)
            entries += g["entries"]
            unbound |= bool(g.get("unbound"))
            if g["failing"]:
                lines.append(f"{kind} still differs in {f.stem}: {g['failing']}/{g['samples']}")
    if unbound:
        ok = False
        lines.append(f"{kind}: not bound yet ({entries} calls; crates/s5xcheck/src/functions/fn_{address(kind)}.rs missing)")
    elif not samples:
        ok = False
        lines.append(f"{kind}: no sampled call (scenario runs missing or crashed)")
    elif kind.startswith("FN_") and not observed:
        # identical calls that change nothing the comparison sees prove nothing: the binding has to compare the
        # function's effect (its result, its writes)
        ok = False
        lines.append(f"{kind}: none of the {samples} sampled calls changed anything the comparison sees (compare the "
                     "result or written values in the binding's reference)")
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
        # the groups come from the original's side, so one that vanished means its capture run crashed
        gone = [other for other in bg if bg[other]["samples"] and other not in ng]
        if gone:
            ok = False
            lines.append(f"full run of {f.stem}: {len(gone)} kinds without result after the change (crash?), e.g. {gone[0]}")
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
        merge(argv[2], argv[3], argv[4], argv[5] if len(argv) > 5 else None)
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
