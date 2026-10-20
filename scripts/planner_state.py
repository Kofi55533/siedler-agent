#!/usr/bin/env python3
"""planner_state.py <W> <siedler-sim-rs checkout>: precompute $W/planner_state.md for a planner run (opencode.yml).

One compact file instead of dozens of gh calls by the model: frontiers (scoreboard data.json), DS-REQ + LANE-CLAIM
lines of docs/AGENT_BOARD.md on origin/clean/integration, open siedler-sim-rs PRs (draft, mergeable, checks, cited
native addresses, files), active opencode runs, queued tasks, issues of the last 24 h with their last result line,
and the coverage map (downloaded here, with the 3 h freshness rule applied). Never contains secrets or log contents.
"""
import datetime as dt, json, os, re, subprocess, sys, time

W, SIM = sys.argv[1], sys.argv[2]
A, S = "Kofi55533/siedler-agent", "Kofi55533/siedler-sim-rs"
NOW = dt.datetime.now(dt.timezone.utc)
HEX = re.compile(r"0x[0-9A-Fa-f]{6}\b")
errors = []


def gh(*args, parse=True, tries=4):
    for i in range(tries):
        try:
            r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                return json.loads(r.stdout) if parse else r.stdout
            err = r.stderr.strip()[:160]
        except Exception as e:  # timeout / EOF
            err = str(e)[:160]
        time.sleep(3 * (i + 1))
    errors.append(f"gh {' '.join(args[:3])}: {err}")
    return None


def ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def age(s):
    t = ts(s)
    if not t:
        return "?"
    m = int((NOW - t).total_seconds() // 60)
    return f"{m}m" if m < 120 else f"{m // 60}h{m % 60:02d}"


def git(*args):
    r = subprocess.run(["git", "-C", SIM, *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def short(s, n):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


out = []
P = out.append
git("fetch", "-q", "origin", "clean/integration")
sha = git("rev-parse", "--short", "origin/clean/integration").strip()
berlin = (NOW + dt.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
P(f"# planner_state.md (precomputed {NOW:%Y-%m-%dT%H:%MZ} = {berlin} Berlin; clean/integration {sha})")
P("Read this first; make individual gh/git calls only for details missing here (it is a snapshot, re-check before claiming).\n")

# 1. frontiers (scoreboard)
P("## Frontiers (scoreboard data.json on gh-pages; source/time of each number)")
sb = gh("api", "-H", "Accept: application/vnd.github.raw", f"repos/{A}/contents/data.json?ref=gh-pages")
if sb:
    for sc in sb.get("scenarios", []):
        v = f"IDENTICAL {sc.get('total')}" if sc.get("identical") else f"turn {sc.get('turn')}"
        P(f"- {sc.get('name')}: {v} (start {sc.get('start')}, total {sc.get('total')}; {short(sc.get('source'), 40)}, {sc.get('when')})")
    P(f"(scoreboard generated {sb.get('generated_berlin')} Berlin)")
P("")

# 2. board: DS-REQ + LANE-CLAIM
board = git("show", "origin/clean/integration:docs/AGENT_BOARD.md").splitlines()
P(f"## docs/AGENT_BOARD.md ({len(board)} lines) — DS-REQ lines (all)")
for i, l in enumerate(board, 1):
    if "DS-REQ" in l and re.match(r"\s*- \[[ ~x]\]", l):
        P(f"- L{i}: {short(l.strip(), 300)}")
P("\n## LANE-CLAIM lines (last 25)")
lc = [(i, l) for i, l in enumerate(board, 1) if "LANE-CLAIM" in l]
for i, l in lc[-25:]:
    P(f"- L{i}: {short(l.strip(), 220)}")
P("(Still read the last ~200 board lines yourself for Opus/Codex notes, claims and wiring requests.)\n")

# 3. open PRs (GraphQL: `gh pr list` returns mergeable=UNKNOWN for most PRs because GitHub computes it lazily; the GraphQL
#    query triggers the computation, so UNKNOWN ones are re-queried after a short wait). Also the last self-merge gate verdict.
Q = """query($c:String){repository(owner:"Kofi55533",name:"siedler-sim-rs"){pullRequests(states:OPEN,first:50,after:$c){
 pageInfo{hasNextPage endCursor} nodes{number title headRefName baseRefName isDraft updatedAt mergeable body
 files(first:60){nodes{path}} comments(last:30){nodes{body createdAt}}}}}}"""


def fetch_prs():
    res, cur = [], None
    for _ in range(6):
        args = ["api", "graphql", "-f", f"query={Q}"] + (["-f", f"c={cur}"] if cur else [])
        d = gh(*args)
        if not d:
            break
        pr = d["data"]["repository"]["pullRequests"]
        res += pr["nodes"]
        if not pr["pageInfo"]["hasNextPage"]:
            break
        cur = pr["pageInfo"]["endCursor"]
    return res


prs = fetch_prs()
for _ in range(3):
    if not any(p.get("mergeable") == "UNKNOWN" for p in prs):
        break
    time.sleep(8)
    again = {p["number"]: p for p in fetch_prs()}
    prs = [again.get(p["number"], p) for p in prs]
for p in prs:
    p["files"] = [{"path": f["path"]} for f in ((p.get("files") or {}).get("nodes") or [])]
    gate = ""
    for c in reversed((p.get("comments") or {}).get("nodes") or []):
        b = c.get("body") or ""
        if "self-merge gate" in b.split("\n", 1)[0]:
            v = "PASS" if "PASS" in b.split("\n", 1)[0] else "FAIL"
            why = next((l.strip() for l in b.splitlines() if "GATE FAIL" in l), "")
            gate = f"{v} {age(c['createdAt'])} ago" + (f": {short(why.replace('GATE FAIL:', ''), 150)}" if why else "")
            break
    p["gate"] = gate or "never gated"

# fresh Opus/Codex lane claims (same parser and 6 h window as the gate): which open PRs they block and until when
claims = []
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location("lane_claims", os.path.join(os.path.dirname(os.path.abspath(__file__)), "lane-claims.py"))
    lcm = importlib.util.module_from_spec(spec); spec.loader.exec_module(lcm)
    nowb = NOW.astimezone(lcm.BER)
    # Codex claims count only while the Codex box loop is active (heartbeat; same rule as the gate)
    beats = None
    try:
        hbf = "/tmp/planner-heartbeat.json"
        hbarg = subprocess.run([os.path.join(os.path.dirname(os.path.abspath(__file__)), "heartbeat.sh"), "fetch", hbf],
                               capture_output=True, text=True, timeout=60).stdout.split()
        if hbarg[:1] == ["--heartbeat"]:
            beats = lcm.load_heartbeats(hbf, NOW)
    except Exception as e:
        errors.append(f"heartbeat: {str(e)[:120]}")
    stale = {o: why for o, (live, why) in (beats or {}).items() if not live}
    for owner, t, files in lcm.parse(board):
        if owner in stale:
            continue
        if owner in ("Opus", "Codex") and dt.timedelta(0) <= nowb - t < dt.timedelta(hours=6):
            claims.append((owner, t, set(files)))
except Exception as e:
    errors.append(f"lane claims: {str(e)[:120]}")


def lane_block(p):
    fs = {f["path"] for f in p["files"]}
    hit = [(o, t) for o, t, cf in claims if fs & cf]
    if not hit:
        return ""
    until = max(t for _, t in hit) + dt.timedelta(hours=6)
    return f"{'/'.join(sorted({o for o, _ in hit}))} lane claim until {until:%H:%M} Berlin"


for p in prs:
    p["lane"] = lane_block(p)
P("## Fresh Opus/Codex lane claims (6 h): never create a task that edits these files/functions (no duplicated work)")
try:
    for o, why in stale.items():
        P(f"- {o} claims are STALE ({why}): its loop is not active, they hold no PR and do not block tasks")
    for owner, t, items in lcm.parse_items(board):
        if owner in stale:
            continue
        if owner in ("Opus", "Codex") and dt.timedelta(0) <= nowb - t < dt.timedelta(hours=6):
            P(f"- {owner} {t:%H:%M} until {t + dt.timedelta(hours=6):%H:%M} Berlin: " + " ".join(f + (f"::{fn}" if fn else "") for f, fn in items))
except Exception as e:
    errors.append(f"claim list: {str(e)[:120]}")
P("(The gate holds a PR only if it changes a claimed function, or touches a whole-file claim; see scripts/lane-claims.py.)\n")
conf = [p for p in prs if p.get("mergeable") == "CONFLICTING"]
P(f"## Open PRs in {S}: {len(prs)} ({len(conf)} CONFLICTING, "
  f"{sum(1 for p in prs if p['lane'])} blocked by a fresh Opus/Codex lane claim)")
P("#n | head -> base | draft | mergeable | updated | last gate | lane block | title | cited addrs | files")
for p in sorted(prs, key=lambda p: p["number"]):
    addrs = sorted({a.upper().replace("0X", "0x") for a in HEX.findall((p.get("title") or "") + " " + (p.get("body") or ""))})
    files = [f["path"] for f in (p.get("files") or [])
             if os.path.basename(f["path"]) not in (".gitignore", "Cargo.lock", "Cargo.toml")]
    fs = ", ".join(files[:5]) + (f" +{len(files) - 5}" if len(files) > 5 else "")
    P(f"- #{p['number']} | {p['headRefName']} -> {p['baseRefName']} | {'draft' if p.get('isDraft') else 'ready'} | "
      f"{(p.get('mergeable') or '?').lower()} | {age(p.get('updatedAt'))} ago | {p['gate']} | {p['lane'] or '-'} | "
      f"{short(p['title'], 90)} | {' '.join(addrs[:12])}{' …' if len(addrs) > 12 else ''} | {fs}")
P("")

# 4. runs + queued + recent issues
runs = gh("run", "list", "-R", A, "-w", "opencode.yml", "--limit", "100", "--json", "status,displayTitle,createdAt,databaseId") or []
act = {}
for r in runs:
    if r["status"] != "completed":
        act.setdefault(r["displayTitle"], r)
P(f"## Active opencode runs: {len(act)} distinct issues (incl. this planner)")
for t, r in sorted(act.items(), key=lambda x: x[1]["createdAt"]):
    P(f"- {r['status']} {age(r['createdAt'])} | {short(t, 140)}")
P("")
q = gh("issue", "list", "-R", A, "--label", "queued", "--state", "open", "--limit", "100", "--json", "number,title,createdAt") or []
P(f"## Queued tasks (label queued, not yet launched; refill launches them into free slots): {len(q)}")
for i in sorted(q, key=lambda i: i["createdAt"]):
    P(f"- #{i['number']} {age(i['createdAt'])} | {short(i['title'], 140)}")
P("")
iss = gh("issue", "list", "-R", A, "--state", "all", "--limit", "120", "--json", "number,title,state,createdAt,labels,comments") or []
since = NOW - dt.timedelta(hours=24)
rec = [i for i in iss if ts(i["createdAt"]) > since and not i["title"].startswith("[planner]")]
P(f"## Task issues of the last 24 h: {len(rec)} (state | labels | last result line)")
for i in sorted(rec, key=lambda i: i["number"]):
    res = ""
    for c in reversed(i.get("comments") or []):
        b = c.get("body") or ""
        if not b.startswith("/opencode"):
            res = short(b.splitlines()[0] if b else "", 160)
            break
    labels = ",".join(l["name"] for l in i.get("labels") or []) or "-"
    st = "ACTIVE" if i["title"] in act else "QUEUED" if "queued" in labels else "FINISHED" if res else "launched"
    i["_st"], i["_res"] = st, res
    P(f"- #{i['number']} {st} | {short(i['title'], 110)} | {res or '(no result yet)'}")
P("")

# planner memory (scripts/known_done.py): evidence-only results of the last 72 h, persisted across planner runs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import known_done as kd
try:
    KD = kd.update(72) if os.environ.get("KNOWN_DONE_UPDATE", "1") == "1" else kd.load()
except Exception as e:
    KD = {"ports": {}, "results": []}; errors.append(f"known_done: {str(e)[:120]}")

# 4b. frontier work map: per diverging fixture FAMILY (combat + combat-long, genc-x + genc-x-5000 share one boundary),
#     what holds it and what the planner can do (deterministic, from the data above)
scen = {sc["name"]: sc for sc in (sb or {}).get("scenarios", [])}


def fam(n):
    b = re.sub(r"-(\d{4,}|long)$", "", n)
    return b if b != n and (b in scen or re.search(r"-\d{4,}$", n)) else n


fams = sorted({fam(n) for n in scen}, key=len, reverse=True)


def fx_of(text):
    t = (text or "").lower()
    best = None  # first mentioned fixture wins; at equal position the longest (combat-big, not combat)
    for f in fams:
        m = re.search(r"(?<![\w-])" + re.escape(f) + r"(?![\w]|-(?!long\b)[a-z])", t)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), f)
    return best[1] if best else None


def needs_codex(p):
    return any("needs-codex" in (c.get("body") or "") for c in ((p.get("comments") or {}).get("nodes") or []))


groups = {}
for n, sc in scen.items():
    if not sc.get("identical") and isinstance(sc.get("turn"), int):
        groups.setdefault(fam(n), []).append(sc)
pr_by = {}
for p in prs:
    if p["headRefName"].startswith("agent/"):
        f = fx_of(p["headRefName"]) or fx_of(p["title"])
        if f:
            pr_by.setdefault(f, []).append(p)
P("## Frontier work map (diverging fixture families; holder -> what is plannable)")
P("Use this as the dedupe/selection basis: FREE rows -> bucket A, REBASE rows -> bucket D (mandatory, see planner.md 2.D),")
P("all other rows -> no task. `finished` = task issues of the last 24 h whose run has ended (they are NOT pending work).")
rebase = []
for f, scs in sorted(groups.items(), key=lambda kv: min(s["turn"] for s in kv[1])):
    sc = min(scs, key=lambda s: s["turn"])
    turn = sc["turn"]
    members = ", ".join(f"{s['name']} {s['turn']}/{s.get('total')}" for s in sorted(scs, key=lambda s: s["name"]))
    src = f"{short(sc.get('source'), 30)} {sc.get('when')}"
    ps = pr_by.get(f, [])
    ac = [i for i in rec if i["_st"] in ("ACTIVE", "QUEUED", "launched") and fx_of(i["title"]) == f]
    fin = [i for i in rec if i["_st"] == "FINISHED" and fx_of(i["title"]) == f]
    same = [i for i in fin if re.search(rf"(?<!\d){turn}(?!\d)", i["title"])]
    stale = "index.json" in (sc.get("source") or "") or (sc.get("total") and turn >= sc["total"])
    remeas = [i for i in fin if re.search(r"IDENTICAL|no (first )?divergence|stale|recording cap|fully matched", i["_res"], re.I)]
    if ac:
        verdict = "nothing (task active/queued: " + " ".join(f"#{i['number']}" for i in ac) + ")"
    elif any(p.get("mergeable") == "CONFLICTING" and not needs_codex(p) for p in ps):
        c = [p for p in ps if p.get("mergeable") == "CONFLICTING" and not needs_codex(p)]
        verdict = "REBASE task (bucket D, mandatory): " + " ".join(f"PR #{p['number']}" for p in c)
        rebase += c
    elif ps and all(p["lane"] for p in ps):
        verdict = "nothing until " + max(p["lane"][-12:] for p in ps) + " (PR held by a lane claim; its gate re-runs later)"
    elif ps:
        verdict = "nothing (open PR, WIP limit 1)"
    elif remeas and (stale or any(i in same for i in remeas)):
        verdict = f"nothing (scoreboard value is stale; re-measured by #{remeas[-1]['number']}: {short(remeas[-1]['_res'], 70)})"
    elif any(r.get("kind") == "frontier" and r.get("turn") == turn and r.get("fixture") and fam(r["fixture"]) == f for r in KD["results"]):
        r = next(r for r in KD["results"] if r.get("kind") == "frontier" and r.get("turn") == turn and r.get("fixture") and fam(r["fixture"]) == f)
        verdict = f"nothing (evidence-only result #{r['issue']} on turn {turn}; re-queue only after the turn moves) -> board handoff to Opus if not yet done"
    elif len(same) >= 2:
        verdict = f"STUCK ({len(same)} finished runs on turn {turn}): board handoff to Opus (anti-stuck), no task"
    else:
        verdict = "FREE -> diagnosis task (bucket A)" + (" — value looks stale (index.json / turn >= total): task = re-measure first" if stale else "")
    pp = "; ".join(f"#{p['number']} {(p.get('mergeable') or '?').lower()}, {p['gate'][:30]}{', ' + p['lane'] if p['lane'] else ''}" for p in ps) or "-"
    P(f"- {f}: {members} [{src}] | PRs: {pp} | active/queued: {' '.join('#%d' % i['number'] for i in ac) or '-'} | "
      f"finished 24h: {len(fin)} ({len(same)} on turn {turn}) | => {verdict}")
other = [p for p in conf if p["headRefName"].startswith("agent/") and not needs_codex(p) and p not in rebase
         and not any(fx_of(i["title"]) and f"#{p['number']}" in i["title"] for i in rec if i["_st"] in ("ACTIVE", "QUEUED", "launched"))]
P("")
P(f"## Conflicting agent/* PRs without a frontier fixture (rebase candidates, bucket D, after the mandatory ones): "
  + (" ".join(f"PR #{p['number']}" for p in other) or "none"))
P("(needs-codex drafts are excluded: they belong to Codex.)\n")

# coverage cache download (also used by section 5)
cov0 = os.path.join(W, "COVERAGE.md")
gh("release", "download", "coverage-cache", "-R", A, "-D", W, "--clobber", "-p", "COVERAGE.md", "-p", "coverage-meta.txt", parse=False)
subprocess.run(["gh", "release", "download", "coverage-cache", "-R", A, "-D", W, "--clobber", "-p", "COVERAGE-full.md"],
               capture_output=True, timeout=120)  # optional (full uncited list); absent before the first publish
_m = dict(l.strip().partition("=")[::2] for l in open(os.path.join(W, "coverage-meta.txt"))) if os.path.isfile(os.path.join(W, "coverage-meta.txt")) else {}
fresh_cov = os.path.isfile(cov0) and os.path.getsize(cov0) > 1000 and bool(_m.get("generated_utc")) and \
    NOW - ts(_m["generated_utc"]) < dt.timedelta(hours=3)

# 4c. inexhaustible task sources (Dominic 2026-10-20 10:09): when the frontier map has fewer FREE/REBASE rows than slots,
#     the planner fills the rest from these lists (already deduped against cited code, open PRs and task issues).
allissues = gh("issue", "list", "-R", A, "--state", "all", "--limit", "400", "--json", "number,title,state,createdAt") or []
prtext = " ".join((p.get("title") or "") + " " + (p.get("body") or "") for p in prs)
tasked = " ".join(i["title"] for i in allissues if not i["title"].startswith("[planner]"))
taken = {a.upper().replace("0X", "0x") for a in HEX.findall(prtext + " " + tasked)}
# cited in any spelling (0x58676B, 0x0058_676B, 0x0058676B; #879 found the 6-digit grep missed underscore forms)
cited = set(kd.addrs(git("grep", "-hoiE", r"0x[0-9a-f_]{6,11}", "origin/clean/integration", "--", "crates/s5clean/src")))
P("## Fill sources (use when FREE/REBASE rows < slots; a planner must always queue N tasks unless the rest are provable duplicates)")
covf = (os.path.join(W, "COVERAGE-full.md") if os.path.isfile(os.path.join(W, "COVERAGE-full.md")) else cov0) if fresh_cov else None
cand = {}
if covf:
    fx = None
    for l in open(covf):
        h = re.match(r"### (\S+) \(turns", l)
        if h:
            fx = h.group(1)
        if l.startswith("### Uncited registered handlers"):
            fx = "handler"
        m = re.match(r"\|\s*\d+\s*\|\s*`(0x[0-9A-Fa-f]{6})`\s*\|\s*([^|]*)\|\s*(\d+)\s*\|\s*(\d+)", l)
        if m and fx:
            a = "0x" + m.group(1)[2:].upper()
            c = cand.setdefault(a, {"role": m.group(2).strip(), "fx": [], "blocks": 0, "first": int(m.group(4))})
            c["fx"].append(fx); c["blocks"] = max(c["blocks"], int(m.group(3)))
free_ports = {a: c for a, c in cand.items() if a not in cited and a not in taken and a not in KD["ports"]}
P(f"### (a) Port candidates: executed in the reference, uncited in crates/s5clean/src, not named by any open PR or task issue, "
  f"not known done: {len(free_ports)} of {len(cand)} listed in COVERAGE.md "
  f"({sum(1 for a in cand if a in KD['ports'])} known done by evidence-only runs, see 'Known done' below)")
P("Group 5-15 related ones (same class prefix / call chain) per `Port:` task; bit-exact vs recomp/ with unit tests, cite each address,")
P("wire it where state is compared; if a function turns out read-only/inert, the run records that in one line and moves to the next.")
bycls = {}
for a, c in free_ports.items():
    k = (c["role"].split(" ")[0] if c["role"] and not c["role"].startswith("FUN_") else "(unclassified)")
    bycls.setdefault(k, []).append((a, c))
for k, lst in sorted(bycls.items(), key=lambda kv: -sum(c["blocks"] for _, c in kv[1]))[:40]:
    lst.sort(key=lambda x: -x[1]["blocks"])
    P(f"- {k} ({len(lst)}): " + ", ".join(f"{a} [{short(c['role'], 50)}; {'/'.join(sorted(set(c['fx']))[:3])}; first {c['first']}]" for a, c in lst[:15]))
if not covf:
    P("- (coverage cache stale: read docs/clean/COVERAGE.md and dedupe the same way)")
ts_ = ((sb or {}).get("percent") or {}).get("test_scope") or {}
low = (prtext + " " + tasked).lower()
cmds = [c for c in (ts_.get("commands") or {}).get("untested", []) if c.split()[0].lower() not in low and
        (len(c.split()) < 2 or c.split()[1].lower() not in low) and str(int(c.split()[0], 16)) not in low]
P(f"\n### (b) Untested commands without an open PR/task ({len(cmds)} of {len((ts_.get('commands') or {}).get('untested', []))} untested; "
  f"tested {(ts_.get('commands') or {}).get('tested')}/{(ts_.get('commands') or {}).get('total')}): " + ", ".join(cmds))
maps = [m for m in (ts_.get("maps") or {}).get("untested", []) if m.split(": ")[-1].lower() not in low]
P(f"### (b) Untested official maps without an open PR/task ({len(maps)}; tested {(ts_.get('maps') or {}).get('tested')}/"
  f"{(ts_.get('maps') or {}).get('total')}): " + ", ".join(maps[:40]) + (" …" if len(maps) > 40 else ""))
P("One `Coverage:` task per command group or map: a generator/fixture that reaches the command's native handler (name it as")
P("`New-code-path:`), reference export with s5verify, private release asset, s5clean live, first divergence on the board.")
unm = [sc for sc in (sb or {}).get("scenarios", []) if sc.get("turn") is None and not sc.get("identical")]
P(f"\n### (c) Unmeasured scenarios (scoreboard has no clean/integration value): {len(unm)}")
for sc in unm:
    n = sc["name"]
    act_ = [i for i in rec if i["_st"] in ("ACTIVE", "QUEUED", "launched") and fx_of(i["title"]) == fam(n)]
    done_ = [i for i in rec if i["_st"] == "FINISHED" and fx_of(i["title"]) == fam(n)]
    note = ("task active/queued #" + str(act_[0]["number"])) if act_ else \
        (f"measured by #{done_[-1]['number']}: {short(done_[-1]['_res'], 80)} -> board line missing; task only if the result is unclear") if done_ else "FREE -> measure task"
    P(f"- {n} (start {sc.get('start')}, total {sc.get('total')}): {note}")
P("A measure task installs the fixture (private release / fixtures-gen), runs s5-live on origin/clean/integration and writes one")
P("board line `live <fixture>: turn X` (or IDENTICAL n); if it diverges, it is a new frontier (bucket A next run).")
P("")

# 4d. planner memory (scripts/known_done.py, release planner-memory/known-done.json): never re-queue these
P(f"## Known done (evidence-only Port results, {len(KD['ports'])} addresses, kept {kd.EXPIRE_D:g} days): NEVER queue a Port task for these")
P("They are already ported/modelled, inert, STL/CRT helpers or unreached (the run proved it). refill closes a Port task whose")
P("addresses are all listed here instead of launching it, unless its body has 'Known-done-override: <relevant base change>'.")
byi = {}
for a, e in KD["ports"].items():
    byi.setdefault((e["issue"], e["reason"]), []).append(a)
for (n, why), lst in sorted(byi.items(), key=lambda kv: -kv[0][0])[:40]:
    P(f"- #{n} ({why}): {' '.join(sorted(lst))}")
P("")
P("## Do-not-repeat: evidence-only results of the last days (same target again only if the frontier/base changed relevantly)")
for r in sorted(KD["results"], key=lambda r: r["issue"], reverse=True)[:40]:
    tgt = f"{r['fixture']} {r['turn']}" if r.get("fixture") else " ".join(r.get("addrs") or [])[:80]
    P(f"- #{r['issue']} {r['kind']}: {tgt or short(r.get('title'), 80)}")
P("No Diverge task for a fixture that is IDENTICAL over its full length; no genb/genc re-measure tasks (CI job, no LLM).\n")

# 5. coverage
P("## Coverage map")
cov = os.path.join(W, "COVERAGE.md")
meta = os.path.join(W, "coverage-meta.txt")
gh("release", "download", "coverage-cache", "-R", A, "-D", W, "--clobber", "-p", "COVERAGE.md", "-p", "coverage-meta.txt", parse=False)
m = {}
if os.path.isfile(meta):
    for l in open(meta):
        k, _, v = l.strip().partition("=")
        m[k] = v
fresh = os.path.isfile(cov) and os.path.getsize(cov) > 1000 and m.get("generated_utc") and \
    NOW - ts(m["generated_utc"]) < dt.timedelta(hours=3)
if fresh:
    P(f"- USE {cov} (coverage-cache, generated {m['generated_utc']}, clean/integration {m.get('clean_integration_sha', '?')[:8]}, "
      f"fixtures: {short(m.get('fixtures'), 400)})")
else:
    P("- cached map missing or older than 3 h -> USE `git show origin/clean/integration:docs/clean/COVERAGE.md`")
    cov = None
if cov:
    lines = open(cov).read().splitlines()
    try:
        k = lines.index("## Overall")
        P("- " + " / ".join(l.lstrip("- ") for l in lines[k + 1:k + 5] if l.strip()))
    except ValueError:
        pass
P("- still re-check 'ported' via `git grep -i <addr> origin/clean/integration -- crates/s5clean/src`.\n")

if errors:
    P("## State collection errors (fetch these yourself)")
    for e in errors:
        P(f"- {e}")
open(os.path.join(W, "planner_state.md"), "w").write("\n".join(out) + "\n")
print(f"planner_state.md: {sum(len(l) + 1 for l in out)} bytes, {len(prs)} PRs, {len(act)} active, {len(q)} queued, "
      f"{len(rec)} recent issues, coverage={'cache' if fresh else 'committed'}, errors={len(errors)}")
