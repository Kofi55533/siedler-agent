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

# 3. open PRs
prs = gh("pr", "list", "-R", S, "--state", "open", "--limit", "200", "--json",
         "number,title,headRefName,baseRefName,author,isDraft,updatedAt,mergeable,statusCheckRollup,body,files") or []
P(f"## Open PRs in {S}: {len(prs)}")
P("#n | head -> base | draft | mergeable | checks | updated | title | cited addrs | files")
for p in sorted(prs, key=lambda p: p["number"]):
    roll = p.get("statusCheckRollup") or []
    cs = {}
    for c in roll:
        k = (c.get("conclusion") or c.get("state") or c.get("status") or "?").lower()
        cs[k] = cs.get(k, 0) + 1
    checks = ",".join(f"{k}:{v}" for k, v in sorted(cs.items())) or "-"
    addrs = sorted({a.upper().replace("0X", "0x") for a in HEX.findall((p.get("title") or "") + " " + (p.get("body") or ""))})
    files = [f["path"] for f in (p.get("files") or [])
             if os.path.basename(f["path"]) not in (".gitignore", "Cargo.lock", "Cargo.toml")]
    fs = ", ".join(files[:5]) + (f" +{len(files) - 5}" if len(files) > 5 else "")
    P(f"- #{p['number']} | {p['headRefName']} -> {p['baseRefName']} | {'draft' if p.get('isDraft') else 'ready'} | "
      f"{(p.get('mergeable') or '?').lower()} | {checks} | {age(p.get('updatedAt'))} ago | {short(p['title'], 90)} | "
      f"{' '.join(addrs[:12])}{' …' if len(addrs) > 12 else ''} | {fs}")
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
    P(f"- #{i['number']} {i['state'].lower()} | {labels} | {short(i['title'], 110)} | {res or '(no result yet)'}")
P("")

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
