#!/usr/bin/env python3
"""Work list of the per-call differential (crates/s5xcheck capture tests).

  xc.py merge <dir> <sha> <out> [meta]
                                      per-scenario capture JSONs -> worklist.json (kinds x scenarios); meta: a directory
                                      with fn_calls.json (static calls between class-A functions) and cited.txt (native
                                      addresses the clean code cites), the order of the function units; the
                                      callers of each function (capture-functions) mark the ones covered by their
                                      verified callers and order the rest top-down
  xc.py regressions <old> <new>       (kind, scenario) pairs that compared clean in <old> and fail in <new> (JSON list)
  xc.py pick <worklist> <n> [--claim] [--limit=L]
                                      up to n failing kinds no open PR, claim, exhausted attempt or merge since the
                                      work list was pinned covers (JSON list);
                                      --limit: at most L live claims of this pool (GITHUB_REPOSITORY) in all
                                      XC_CLAIM_TOKEN set: only the kinds whose claim still carries it after claiming
  xc.py bundle <worklist> <kind> <n> <native-dir>
                                      <kind> plus up to n-1 more small functions to port that share its run (claimed
                                      under the same XC_CLAIM_TOKEN), comma-joined; <kind> alone when it is no small port
  xc.py scenarios <worklist> <kind>   names of the scenarios where <kind> was sampled, failing ones first
  xc.py failing <worklist> <kind>     names of the scenarios where <kind> fails
  xc.py check <base> <new> <kind>[,<kind>...] [<worklist> <scenario>...]
                                      a worker's verdict: each <kind> clean everywhere, nothing else newly failing (a
                                      function kind FN_<address> also bound and observed); with the work list, the
                                      named scenarios need a full run in <new>, compared with <base>/full where
                                      measured (scenarios without one showed no suspect against the work list)
  xc.py suspects <worklist> <new-full> <kind>[,<kind>...]
                                      scenarios whose full run after a change shows another kind failing that the work
                                      list measured clean there, or missing (only those need the base's full run)
  xc.py stub <worklist> <kind>[,<kind>...] <dir> <scenario>...
                                      <dir>/<scenario>.json with the work list's call counts of unbound functions
  xc.py shard <i> <n> < scenario lines
                                      the lines of shard i of n: with XC_WEIGHTS (JSON scenario -> run seconds of the
                                      previous census) the heaviest first onto the lightest shard, else every n-th line
  xc.py branch <kind>                 the fix branch of a kind
  xc.py trial-waiting                 exit 0 while an xc-try run of the main repo waits for a job slot
  xc.py issue <kind> <op> [sha]       tracking issue of a kind: claim | unclaim | attempt <census sha> |
                                      merged <census sha> | close

The JSON files hold game state (diff lines); they stay on the runner or in the private release, never in a log.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ATTEMPTS = 3  # failed worker attempts per kind (on the same census) before it waits for a new census
CENSUS = 5   # version of what the census measures (2: function units, 3: their callers, 4: 1200 turns, 5: player commands); a work list of another version is re-measured
# a function port this small (recognized instructions) shares its worker run with up to n-1 more of them: the base
# measurement, the full runs and the tests of a verdict (~15 of a kind's ~18 min on 2026-10-25) are paid once
BUNDLE_INS = 15
CLAIM_H = 7   # a claim (a worker picked the kind) blocks other picks this long; a worker job lasts at most 6 h
PROJECT = os.environ.get("XC_PROJECT", "Kofi55533/siedler-sim-rs")
# the tracking issues live in the main pipeline repo for every pool (forks pick from the same work list)
AGENT = os.environ.get("XC_AGENT", "Kofi55533/siedler-agent")
POOL = os.environ.get("GITHUB_REPOSITORY", "local")  # the repo whose runners do the work (main or a pool)
# a job's own mark on its claims: a worker job that takes its next kind claims outside the wave's serialized pick job, so
# after claiming it re-reads the issue and keeps only the kinds whose claim is still its own (the last writer wins)
TOKEN = os.environ.get("XC_CLAIM_TOKEN", "")


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
                           "missing_points": doc.get("missing_points", []), "commands": doc.get("commands", 0),
                           "secs": doc.get("secs")}
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
    od, nd = load(old), load(new)
    if od.get("census", 1) != nd.get("census", 1):  # measured differently (e.g. fewer turns): no pair is comparable
        print("[]")
        return
    o, n = pairs(od), pairs(nd)
    # a scenario played with other player commands (new generated fixtures on its image) runs other calls
    cmds = lambda doc, s: (doc.get("scenarios", {}).get(s) or {}).get("commands", 0)
    out = [{"kind": k, "scenario": s} for (k, s), (samples, failing) in sorted(n.items())
           if failing and (k, s) in o and o[(k, s)][0] and not o[(k, s)][1] and cmds(od, s) == cmds(nd, s)]
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


def merged_since(body, sha, pinned):
    """merged after the census measured on <sha> was pinned (its commit time <pinned>): that work list still lists the
    kind as todo, the next one re-measures. "merged <census sha12> <epoch>"; an older body has no epoch."""
    m = fields(body).get("merged", "").split()
    if not m:
        return False
    if m[0] == sha[:12]:
        return True
    return len(m) > 1 and pinned is not None and int(m[1]) > pinned


RUNS = {}  # worker run id -> ended, asked once per pick


def ended(run, pool):
    """the claiming worker run is over: a job that died before its verdict (a setup failure, a lost runner) never
    released its claim; a run that cannot be looked up counts as going"""
    if run not in RUNS:
        r = gh(f"repos/{pool}/actions/runs/{run}") if run.isdigit() else None
        RUNS[run] = bool(r) and r.get("status") == "completed"
    return RUNS[run]


def claimed(body, now, pool=None):
    """a live claim (of any pool, or of <pool> only): younger than CLAIM_H and its worker run still going"""
    c = fields(body).get("claimed", "").split()
    return (bool(c) and now - int(c[0]) < CLAIM_H * 3600 and (pool is None or c[2:3] == [pool])
            and not (len(c) > 2 and ended(c[1], c[2])))


def jobs(issues, now, pool=None):
    """the running worker jobs behind the live claims: a job claims its whole bundle under its one token (bundle()),
    so the slots count distinct tokens; a claim without a token counts alone"""
    live = (fields(body).get("claimed", "").split() for _, body in issues.values() if claimed(body, now, pool))
    return len({c[3] if len(c) > 3 else i for i, c in enumerate(live)})


def trial_waiting(repo=None):
    """a hand or ref trial (xc-try.yml) of the main account waits for a job slot: the fleet makes way for it (the
    watchdog starts no wave, a worker job takes no further kind), so a trial never queues behind the fix jobs"""
    rs = (gh(f"repos/{repo or AGENT}/actions/workflows/xc-try.yml/runs?per_page=20") or {}).get("workflow_runs", [])
    now = time.time()
    for r in rs:
        # a push without the trial tag skips its job within seconds; a young run is not yet known to wait
        if now - datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).timestamp() < 60:
            continue
        if r["status"] in ("queued", "waiting", "pending", "requested"):
            return True
        if r["status"] == "in_progress":
            js = (gh(f"repos/{repo or AGENT}/actions/runs/{r['id']}/jobs?per_page=50") or {}).get("jobs", [])
            if any(j["status"] in ("queued", "waiting", "pending", "requested") for j in js):
                return True
    return False


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
        f["claimed"] = f"{int(time.time())} {os.environ.get('GITHUB_RUN_ID', 'local')} {POOL}" + (f" {TOKEN}" if TOKEN else "")
    elif op == "unclaim":
        f.pop("claimed", None)
    elif op == "attempt":
        f = {"census": sha[:12], "attempts": str(tried(body, sha) + 1), **({"merged": f["merged"]} if "merged" in f else {})}
    elif op == "merged":  # the issue stays open: the work lists pinned before the merge still list the kind as todo
        f = {"merged": f"{sha[:12]} {int(time.time())}"}
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


def available(doc, issues, now):
    """the work units a worker may take now: todo, no open PR on its branch, no merge since the work list was pinned,
    no live claim, attempts left on this census"""
    taken = set()
    for page in range(1, 6):
        prs = gh(f"repos/{PROJECT}/pulls?state=open&per_page=100&page={page}") or []
        taken |= {p["head"]["ref"] for p in prs}
        if len(prs) < 100:
            break
    commit = gh(f"repos/{PROJECT}/commits/{doc['sha']}") or {}
    pinned = commit.get("commit", {}).get("committer", {}).get("date")
    pinned = datetime.fromisoformat(pinned.replace("Z", "+00:00")).timestamp() if pinned else None
    for kind, k in doc["kinds"].items():
        if not todo(kind, k) or branch(kind) in taken:
            continue
        body = issues.get(kind, (None, ""))[1]
        # the attempt counter restarts with every census sha (new base, new examples)
        if merged_since(body, doc["sha"], pinned) or claimed(body, now) or tried(body, doc["sha"]) >= ATTEMPTS:
            continue
        yield kind, k


def claim_own(kinds):
    """claim the kinds; with XC_CLAIM_TOKEN only the ones whose claim still carries it (the last writer wins)"""
    for kind in kinds:
        issue(kind, "claim")
    if TOKEN and kinds:
        time.sleep(8)
        mine = tracking()
        kinds = [k for k in kinds if fields(mine.get(k, (None, ""))[1]).get("claimed", "").split()[3:4] == [TOKEN]]
    return kinds


def pick(worklist, n, claim=False, limit=None):
    doc = load(worklist)
    issues, now = tracking(), time.time()
    if limit is not None:  # this pool's slots: its live claims are its running worker jobs
        n = min(n, limit - jobs(issues, now, POOL))
    ranked = sorted(rank(kind, k) for kind, k in available(doc, issues, now))
    kinds = [r[-1] for r in ranked[:max(n, 0)]]
    if claim:
        kinds = claim_own(kinds)
        # a kind this census no longer lists as work (fixed, or its function is no longer called) closes its issue, so
        # the open issues stay the work in progress
        for kind, (num, body) in issues.items():
            k = doc["kinds"].get(kind)
            if not doc.get("partial") and not claimed(body, now) and (k is None or not todo(kind, k)):
                gh("-X", "PATCH", f"repos/{AGENT}/issues/{num}", "-f", "state=closed")
    print(json.dumps(kinds))


def sizes(native):
    """recognized instructions per function address (functions.jsonl of the decompilation)"""
    with open(Path(native, "functions.jsonl")) as f:
        return {int(d["addr"], 16): d.get("recognized_instructions", 0) for d in map(json.loads, f)}


def small_port(kind, k, ins):
    return kind.startswith("FN_") and fn_state(k) == "port" and 0 < ins.get(int(address(kind) or "0", 16), 0) <= BUNDLE_INS


def late(name):
    """a later window of the bc recording (lib.sh XC_BC_WINDOWS): the native replay of the skipped turns costs every run
    of it 145-403 s (probe 2026-10-25), so a job plays one only for a kind that no other scenario shows"""
    return name.startswith("bc-")


def cheap(names):
    """names without the late bc windows, or all of them when nothing else is left"""
    return [n for n in names if not late(n)] or names


def calling(k):
    """the scenarios that call an unbound function, the most calls first (the order of scenarios()), the late bc windows
    only when no other scenario calls it"""
    return cheap([name for name, v in sorted(k["scenarios"].items(), key=lambda kv: (-kv[1]["entries"], kv[0]))
                  if v["entries"]])


def bundle(worklist, kind, n, native):
    """<kind> and, when it is a small function to port, up to n-1 more of them in the order pick() would take them,
    each called most in one of the scenarios that <kind>'s verdict plays in full (worker.sh: its first three), so the
    verdict's runs sample every function of the bundle; they are claimed for this job"""
    doc = load(worklist)
    k = doc["kinds"].get(kind)
    out = [kind]
    if n > 1 and k:
        ins = sizes(native)
        if small_port(kind, k, ins):
            full = set(calling(k)[:3])
            issues, now = tracking(), time.time()
            ranked = sorted(rank(o, v) for o, v in available(doc, issues, now)
                            if o != kind and small_port(o, v, ins) and calling(v)[:1] and calling(v)[0] in full)
            more = [r[-1] for r in ranked[:n - 1]]
            # the pick job claimed <kind> under its own token: it moves to this job's, so jobs() counts the bundle once
            out += [o for o in claim_own([kind] + more if more else []) if o != kind]
    print(",".join(out))


def scenarios(worklist, kind, only_failing=False):
    """a function without binding yet: the scenarios that call it, the most calls first (all of them its work)"""
    k = load(worklist)["kinds"].get(kind, {"scenarios": {}})
    if k.get("unbound"):
        print(" ".join(calling(k)))
        return
    items = sorted(k["scenarios"].items(), key=lambda kv: (-kv[1]["failing"], kv[0]))
    failing = cheap([name for name, v in items if v["samples"] and v["failing"]])
    clean = [name for name, v in items if v["samples"] and not v["failing"]]
    # a late bc window joins only when the kind shows in no other scenario: a merge that breaks one is reverted by the
    # next census like any other regression, and a kind failing only there is picked again with it
    if any(not late(n) for n in failing + clean):
        clean = [n for n in clean if not late(n)]
    print(" ".join(failing if only_failing else failing + clean))


def suspect(wk, scen, groups, kinds):
    """another kind failing in a full run although the work list measured it clean in that scenario, or one the work
    list sampled there and the run lost"""
    for other, k in wk.items():
        v = k.get("scenarios", {}).get(scen)
        if other in kinds or not v or not v["samples"]:
            continue
        g = groups.get(other)
        if g is None or (g["failing"] and not v["failing"]):
            return True
    return False


def suspects(worklist, new_full, kind):
    wk = load(worklist)["kinds"]
    kinds = kind.split(",")
    print(" ".join(f.stem for f in sorted(Path(new_full).glob("*.json")) if suspect(wk, f.stem, load(f)["groups"], kinds)))


def stub(worklist, kind, out, names):
    wk = load(worklist)["kinds"]
    Path(out).mkdir(parents=True, exist_ok=True)
    for name in names:
        groups = {}
        for one in kind.split(","):
            v = wk.get(one, {"scenarios": {}})["scenarios"].get(name)
            if v and v["entries"]:
                groups[one] = {"entries": v["entries"], "samples": 0, "failing": 0, "unbound": True, "examples": []}
        if groups:
            Path(out, f"{name}.json").write_text(json.dumps({"groups": groups}))


def timed_out_short(base, new):
    """<new> (a full run's JSON) played fewer turns than <base> because every failed attempt at full length was a
    timeout (exit 124): its <name>.crash excerpt (verify.sh) names only timeouts (a crash at full length exits 101 or
    134 there; a panic line alone can be the shorter play's failing comparison, which still writes its document)"""
    crash = Path(new).with_suffix(".crash")
    if load(new).get("turns", 0) >= load(base).get("turns", 0) or not crash.is_file():
        return False
    text = crash.read_text(errors="replace")
    codes = re.findall(r"no result in \d+ turns \(exit (\d+)\)", text)
    return bool(codes) and set(codes) == {"124"}


def check(base, new, kind, worklist=None, full=None):
    """base/new: directories with per-scenario JSONs (the kind-only runs plus full runs of the failing scenarios; a
    scenario with a full run has no kind-only run, its full run holds the kind too). <kind> may be a bundle
    (comma-joined): each of its kinds has to pass, and none of them counts as another kind's regression."""
    ok, lines = True, []
    kinds = kind.split(",")
    runs = {f.stem: f for f in sorted(Path(new, "only").glob("*.json"))}
    runs.update({f.stem: f for f in sorted(Path(new, "full").glob("*.json")) if worklist})
    done = []
    for kind in kinds:
        samples = failing = observed = entries = 0
        unbound = False
        for _, f in sorted(runs.items()):
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
        done.append(f"{kind}: {samples - failing}/{samples} sampled calls identical")
    for f in sorted(Path(new, "full").glob("*.json")):
        b = Path(base, "full", f.name)
        if not b.exists():
            continue
        bg, ng = load(b)["groups"], load(f)["groups"]
        for other, g in ng.items():
            if other not in kinds and g["failing"] and other in bg and bg[other]["samples"] and not bg[other]["failing"]:
                ok = False
                lines.append(f"regression: {other} now differs in {f.stem} ({g['failing']}/{g['samples']})")
        # the groups come from the original's side, so one that vanished means its capture run crashed
        gone = [other for other in bg if bg[other]["samples"] and other not in ng]
        if gone and timed_out_short(b, f):
            # the play ran past XC_TIMEOUT and fell back to fewer turns (lib.sh xc_run_point): the kinds the base
            # showed later in the play have no result, not a crash (2026-10-31: 4 identical hand trials failed on the
            # long ai-builds/camp scenarios); the kinds of the shorter play are still compared above
            lines.append(f"full run of {f.stem}: timed out, played {load(f)['turns']} of {load(b)['turns']} turns, "
                         f"{len(gone)} later kinds not compared")
        elif gone:
            ok = False
            lines.append(f"full run of {f.stem}: {len(gone)} kinds without result after the change (crash?), e.g. {gone[0]}")
    for name in (full if worklist else [f.stem for f in sorted(Path(base, "full").glob("*.json"))]):
        if not Path(new, "full", f"{name}.json").exists():
            ok = False
            lines.append(f"full run of {name} missing after the change (crash?)")
    print("\n".join(lines + done))
    print("VERDICT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def shard(i, n, lines, weights):
    """the same split on every shard (same lines, same weights): heaviest scenario first onto the shard with the least
    seconds so far; a scenario the previous census did not time counts as the median of the timed ones"""
    if not weights:
        return [l for j, l in enumerate(lines) if j % n == i]
    known = sorted(weights.values())
    default = known[len(known) // 2]
    load, mine = [0] * n, []
    for l in sorted(lines, key=lambda l: (-weights.get(l.split()[0], default), l)):
        k = load.index(min(load))
        load[k] += weights.get(l.split()[0], default)
        if k == i:
            mine.append(l)
    return mine


def main(argv):
    cmd = argv[1]
    if cmd == "merge":
        merge(argv[2], argv[3], argv[4], argv[5] if len(argv) > 5 else None)
    elif cmd == "regressions":
        regressions(argv[2], argv[3])
    elif cmd == "pick":
        lim = [int(a.split("=")[1]) for a in argv if a.startswith("--limit=")]
        pick(argv[2], int(argv[3]), claim="--claim" in argv, limit=lim[0] if lim else None)
    elif cmd == "bundle":
        bundle(argv[2], argv[3], int(argv[4]), argv[5])
    elif cmd == "scenarios":
        scenarios(argv[2], argv[3])
    elif cmd == "failing":
        scenarios(argv[2], argv[3], only_failing=True)
    elif cmd == "check":
        return check(argv[2], argv[3], argv[4], argv[5] if len(argv) > 5 else None, argv[6:])
    elif cmd == "suspects":
        suspects(argv[2], argv[3], argv[4])
    elif cmd == "stub":
        stub(argv[2], argv[3], argv[4], argv[5:])
    elif cmd == "issue":
        issue(argv[2], argv[3], argv[4] if len(argv) > 4 else None)
    elif cmd == "shard":
        w = {k: v for k, v in json.loads(os.environ.get("XC_WEIGHTS") or "{}").items() if isinstance(v, int) and v > 0}
        print("".join(l + "\n" for l in shard(int(argv[2]), int(argv[3]), [l.rstrip("\n") for l in sys.stdin if l.strip()], w)), end="")
    elif cmd == "branch":
        print(branch(argv[2]))
    elif cmd == "trial-waiting":
        return 0 if trial_waiting() else 1
    else:
        raise SystemExit(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
