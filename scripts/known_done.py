#!/usr/bin/env python3
"""known_done.py: planner memory of evidence-only results (Dominic 2026-10-20, bottleneck fix 3: task supply).

73 of 165 runs (2026-10-19/20) ended evidence-only, and the same targets were queued again (e.g. CAStarLeader group #857 ->
#1033, evade group #868 -> #1005, #878 -> #1035, #885 -> #1048). This keeps a persistent list in release asset
known-done.json (release `planner-memory` of Kofi55533/siedler-agent):
  ports   {addr: {issue, reason, date, base}}  addresses of a finished `Port:` task that ended without a PR and whose result
                                               says they are already ported/modelled, inert, STL/CRT helpers, unreached ...
  results [{issue, date, kind, key, turn}]     every evidence-only result of the last days (Port address set or
                                               fixture+turn of a frontier task) for the do-not-repeat rule.
Entries expire after EXPIRE_D days (default 7). A task body line `Known-done-override: <relevant base change>` lifts the
guard for that task (the planner must name the change).
  update [hours]      scan task issues of the last <hours> (default 72), merge, upload; prints a one-line summary
  show                print the current list (no upload)
  guard <issue>       refill launch guard: prints "ok" or "skip: <reason>" for a queued task issue
Never prints secrets or log contents (only addresses, issue numbers and short reasons).
"""
import datetime as dt, json, os, re, subprocess, sys, tempfile

A = "Kofi55533/siedler-agent"
REL, ASSET = "planner-memory", "known-done.json"
NOW = dt.datetime.now(dt.timezone.utc)
EXPIRE_D = float(os.environ.get("KNOWN_DONE_EXPIRE_D", "7"))
ADDR = re.compile(r"0x([0-9A-Fa-f_]{6,11})(?![0-9A-Fa-f_])")
EVID = re.compile(r"evidence[- ]only|no (source|code) change|no code(,| or| and)? (branch|pr)\b|no (new )?pr\b|no port\b|"
                  r"already (fully )?(ported|modell?ed|modeled|wired|cited|covered|landed)|duplicate, not missing|grep artifact|"
                  r"not missing logic|nothing to port|stays? (inert|unreached)", re.I)
MADE_PR = re.compile(r"pull/\d+|\bdraft pr #\d+|\bopened (draft )?pr\b|\bpr: https|\bported\b(?! ?/)|\bwired the\b", re.I)
REASONS = [("STL/CRT/container helper", r"\bSTL\b|\bCRT\b|std::|container|vector|_Tree|dtor|copy loop|allocator"),
           ("grep artifact (cited in another spelling)", r"grep artifact|0x0{2}[0-9a-f]{2}_"),
           ("already ported/modelled", r"already (fully )?(ported|modell?ed|modeled|wired|cited|covered|landed)|duplicate"),
           ("inert / writes no compared state", r"inert|no compared state|write[s]? no|read-only|getter|accessor|predicate"),
           ("not executed in the fixtures", r"unreached|never execut|0 times|execute 0|not executed|dead in|zero hits"),
           ]
FRONT = re.compile(r"^(diverge|frontier|diagnos\w*|fix|wire|measure|re-?measure)\b", re.I)


def norm(h):
    h = h.replace("_", "")
    if not h or len(h) > 8:
        return None
    v = int(h, 16)
    return f"0x{v:06X}" if 0x400000 <= v < 0x5E0000 else None


def addrs(text):
    return sorted({a for a in (norm(m.group(1)) for m in ADDR.finditer(text or "")) if a})


def gh(*a, parse=True):
    r = subprocess.run(["gh", *a], capture_output=True, text=True, timeout=180)
    if r.returncode:
        return None
    return json.loads(r.stdout) if parse else r.stdout


def fetch_url(url):
    """Public file without a REST API call (release asset download URL / raw gh-pages): the account's 5000/h API limit was
    exhausted 2026-10-20 13:00-13:26 and the guard then read nothing and let every task through ("ok")."""
    r = subprocess.run(["curl", "-fsSL", "-m", "60", url], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def load():
    m, ok = {}, False
    raw = fetch_url(f"https://github.com/{A}/releases/download/{REL}/{ASSET}")
    if raw is None:
        d = tempfile.mkdtemp()
        if subprocess.run(["gh", "release", "download", REL, "-R", A, "-p", ASSET, "-D", d, "--clobber"], capture_output=True).returncode == 0:
            raw = open(os.path.join(d, ASSET), "rb").read()
    if raw is not None:
        try:
            m, ok = json.loads(raw), True
        except Exception:
            m = {}
    m.setdefault("ports", {}); m.setdefault("results", [])
    m["_loaded"] = ok  # False: memory unreadable (not "empty"); save() refuses to overwrite it, guard() defers
    cut = (NOW - dt.timedelta(days=EXPIRE_D)).isoformat()
    m["ports"] = {a: e for a, e in m["ports"].items() if e.get("date", "") >= cut}
    m["results"] = [r for r in m["results"] if r.get("date", "") >= cut]
    return m


def save(m):
    if not m.pop("_loaded", True):
        return False  # never replace an unreadable memory with a partial one
    p = os.path.join(tempfile.mkdtemp(), ASSET)
    json.dump(m, open(p, "w"), indent=0, sort_keys=True)
    if subprocess.run(["gh", "release", "view", REL, "-R", A], capture_output=True).returncode:
        subprocess.run(["gh", "release", "create", REL, "-R", A, "--target", "main", "--prerelease", "-t", REL, "-n",
                        "Planner memory (scripts/known_done.py): native functions found already ported/modelled/inert by evidence-only "
                        "Port runs, and evidence-only results per target (do-not-repeat). Overwritten by planner runs."],
                       capture_output=True)
    return subprocess.run(["gh", "release", "upload", REL, "-R", A, "--clobber", p], capture_output=True).returncode == 0


def result_of(issue):
    """last run result comment (the opencode summary; skips /opencode commands and gate/refill notes)."""
    for c in reversed(issue.get("comments") or []):
        b = (c.get("body") or "").strip()
        if b and not b.startswith("/opencode") and "closed by refill" not in b and not b.startswith("Skipped by refill"):
            return b, c.get("createdAt", "")
    return "", ""


def evidence_only(title, res):
    return bool(res) and bool(EVID.search(res)) and not MADE_PR.search(res)


NAMES = []


def frontier_key(title):
    """(fixture, turn) of a frontier task title like 'Diverge combat 315: ...' or '... at the combat-315 window'."""
    tl = title.lower()
    for n in NAMES:  # scoreboard names, longest first (genc-wsecon-5000 before genc-wsecon)
        m = re.search(r"(?<![\w-])" + re.escape(n.lower()) + r"(?![\w]|-[a-z0-9])[^0-9#]{0,14}(\d{2,6})\b", tl)
        if m:
            return (n, int(m.group(1)))
    m = re.search(r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+)*?)[- ](?:turn |at |@ ?)?(\d{2,6})\b", title)
    return (m.group(1), int(m.group(2))) if m else None


def update(hours=72):
    m = load()
    NAMES[:] = sorted(scoreboard() or {}, key=len, reverse=True)
    iss = gh("issue", "list", "-R", A, "--state", "all", "--limit", "400", "--search",
             f"created:>={(NOW - dt.timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')} -label:planner-run",
             "--json", "number,title,createdAt,comments") or []
    seen = {r["issue"] for r in m["results"]}
    nports = nres = 0
    for i in iss:
        t = i.get("title") or ""
        if t.startswith("[planner]"):
            continue
        res, when = result_of(i)
        if not evidence_only(t, res):
            continue
        base = (re.search(r"clean/integration @ ?([0-9a-f]{7,8})", res) or re.search(r"@ ?([0-9a-f]{7,8})\b", res) or [None, ""])[1]
        if t.lower().startswith("port"):
            why = next((r for r, rx in REASONS if re.search(rx, res, re.I)), "evidence-only")
            for a in addrs(t):
                if a not in m["ports"]:
                    nports += 1
                m["ports"][a] = {"issue": i["number"], "reason": why, "date": when or i["createdAt"], "base": base}
        if i["number"] not in seen:
            fk = frontier_key(t)
            m["results"].append({"issue": i["number"], "date": when or i["createdAt"], "base": base,
                                 "kind": "port" if t.lower().startswith("port") else "frontier" if fk and FRONT.match(t) else "other",
                                 "addrs": addrs(t), "fixture": fk[0] if fk else None, "turn": fk[1] if fk else None,
                                 "title": t[:120]})
            nres += 1
    m["updated_utc"] = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    ok = save(m)
    print(f"known_done: {len(m['ports'])} known-done addresses (+{nports}), {len(m['results'])} evidence-only results (+{nres}), "
          f"scanned {len(iss)} issues, upload={'ok' if ok else 'FAILED'}")
    return m


def scoreboard():
    raw = fetch_url(f"https://raw.githubusercontent.com/{A}/gh-pages/data.json")  # no API call (CDN, <= 5 min old)
    try:
        d = json.loads(raw) if raw else gh("api", "-H", "Accept: application/vnd.github.raw", f"repos/{A}/contents/data.json?ref=gh-pages")
    except Exception:
        d = None
    return {s["name"]: s for s in (d or {}).get("scenarios", [])} if d else None


def guard(n):
    i = gh("issue", "view", str(n), "-R", A, "--json", "title,body")
    if i is None:
        return "defer: issue unreadable"
    t, body = i.get("title") or "", i.get("body") or ""
    if re.search(r"^\s*known-done-override:\s*\S", body, re.I | re.M):
        return "ok (override)"
    tl = t.lower()
    # genb/genc re-measures are not DeepSeek tasks (gate-baseline mode=remeasure does them without an LLM)
    if re.search(r"\bre-?measure", tl) and re.search(r"\bgen[bc]-", tl) and \
            not re.search(r"root-?cause|\bfix|\bwire|\bport\b|advance|diagnos|name its|next (first )?divergence", tl):
        return "skip: genb/genc re-measure (done by the remeasure CI job, gate-baseline mode=remeasure, no LLM)"
    sb = scoreboard()
    if sb is None:
        return "defer: scoreboard data.json unreadable"
    NAMES[:] = sorted(sb, key=len, reverse=True)
    if tl.startswith("diverge"):
        for name in sorted(sb, key=len, reverse=True):
            if re.search(r"(?<![\w-])" + re.escape(name.lower()) + r"(?![\w-])", tl):
                s = sb[name]
                if s.get("identical") and s.get("total"):
                    return f"skip: {name} already IDENTICAL over its full length ({s['total']}) on the scoreboard; no diverge task"
                break
    m = load()
    if not m["_loaded"]:
        return "defer: planner-memory known-done.json unreadable"
    if tl.startswith("port"):
        a = addrs(t)
        kd = [x for x in a if x in m["ports"]]
        if a and len(kd) == len(a):
            iss = sorted({m["ports"][x]["issue"] for x in kd})
            return f"skip: all {len(a)} addresses already known done ({m['ports'][kd[0]]['reason']}; evidence-only " + \
                   " ".join(f"#{x}" for x in iss) + ")"
    fk = frontier_key(t)
    if fk and FRONT.match(t):
        cur = sb.get(fk[0])
        for r in m["results"]:
            if r.get("kind") == "frontier" and r.get("fixture") == fk[0] and r.get("turn") == fk[1] and (not cur or cur.get("turn") in (None, fk[1])):
                return f"skip: identical target {fk[0]} {fk[1]} already ended evidence-only in #{r['issue']} and the frontier has not moved"
    return "ok"


if __name__ == "__main__":
    a = sys.argv[1:] or ["show"]
    if a[0] == "update":
        update(int(a[1]) if len(a) > 1 else 72)
    elif a[0] == "guard":
        print(guard(a[1]))
    else:
        m = load()
        print(json.dumps({k: m[k] for k in ("ports", "results")}, indent=1)[:20000])
