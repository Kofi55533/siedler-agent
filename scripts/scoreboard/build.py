#!/usr/bin/env python3
"""Scoreboard data builder for GitHub Pages.

Reads (never writes) the private repo Kofi55533/siedler-sim-rs and the public repo
Kofi55533/siedler-agent via the GitHub REST API and writes data.json (numbers/titles only)
plus an appended history.json. No code, no tokens, no full board text are published.

env: GH_TOKEN (read access to both repos), OUT (output dir), PREV (optional previous history.json)
"""
import base64, json, os, re, sys, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PRIV = "Kofi55533/siedler-sim-rs"
PUB = "Kofi55533/siedler-agent"
BRANCH = "clean/integration"
BERLIN = ZoneInfo("Europe/Berlin")
TOKEN = os.environ.get("GH_TOKEN", "")
OUT = os.environ.get("OUT", "out")
PREV = os.environ.get("PREV", "")
NOW = datetime.now(timezone.utc)
ERRORS = []

SCENARIOS = [
    ("bc-full", "Mittleres Spiel (Kampagnen-Spielstand) mit vielen Spielerbefehlen – der große Gesamttest"),
    ("build", "Bauen: Baustellen, Leibeigene bauen Gebäude fertig"),
    ("production", "Rohstoffabbau und Produktionskette (Holz, Stein, Lehm …)"),
    ("late-economy", "Lange Wirtschaftspartie bis tief ins Spiel"),
    ("camp-crawford", "Karte Camp Crawford: Siedlung mit Minen und Truppen"),
    ("combat", "Zwei Armeen kämpfen gegeneinander"),
    ("combat-long", "Kampf, sehr lang laufen gelassen (Schlacht + ruhige Welt danach)"),
    ("combat-big", "Große Schlacht mit vielen Einheiten"),
    ("combat-waves", "Kampf in mehreren Angriffswellen"),
    ("ai-long", "Computergegner (KI) spielt lange selbst"),
    ("economy", "Frischer Kartenstart Dunkelforst mit Rohstoffaufträgen"),
    ("economy-mapstart", "Wie economy, aber direkt ab Kartenstart geladen"),
    ("wsecon", "Wintersturm nach dem Start: Leibeigene kaufen und bauen"),
    ("wintersturm", "Karte Wintersturm ab Start, ohne Befehle"),
    ("wintersturm-mapstart", "Wie wintersturm, aber direkt ab Kartenstart geladen"),
    ("map-winterisland", "Karte Winterinsel, 3000 Turns"),
    ("late-ai", "KI-Partie, extrem lang (36000 Turns)"),
]
GEN_DESC = {
    "genA": "Neue generierte Fixtures A: Wirtschaft/Bauen",
    "genB": "Neue generierte Fixtures B: Militärbefehle",
    "genC": "Neue generierte Fixtures C: lange gemischte Partien",
}
ALIAS_TOTAL = {"bc-full": "bc", "economy-mapstart": "economy", "wintersturm-mapstart": "wintersturm"}


def api(path, raw=False):
    url = path if path.startswith("http") else "https://api.github.com/" + path.lstrip("/")
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "siedler-scoreboard"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if raw else json.loads(data)


def safe(fn, default, what):
    try:
        return fn()
    except Exception as e:  # never leak details beyond the error class / HTTP code
        code = getattr(e, "code", "")
        ERRORS.append(f"{what}: {type(e).__name__} {code}".strip())
        return default


def content(repo, path, ref):
    d = api(f"repos/{repo}/contents/{path}?ref={urllib.parse.quote(ref, safe='')}")
    if d.get("content"):
        return base64.b64decode(d["content"]).decode("utf-8", "replace")
    return api(d["download_url"], raw=True).decode("utf-8", "replace")


import urllib.parse  # noqa: E402


def iso(dt):
    return dt.astimezone(BERLIN).strftime("%Y-%m-%d %H:%M")


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def clean_md(s):
    return re.sub(r"[*`_]", "", s)


# ---------------- board ----------------
ENTRY_RE = re.compile(r"^(?:#+\s*)?(\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}) Berlin\s*[—–-]+\s*([A-Za-z]+)(.*)$")


def board_entries(text):
    """Split the board into entries: a dated '... Berlin — Agent ...' line + following lines."""
    entries, cur = [], None
    for i, line in enumerate(text.splitlines(), 1):
        m = ENTRY_RE.match(line.strip())
        if m:
            cur = {"line": i, "time": m.group(1), "agent": m.group(2), "head": line.strip(), "text": line}
            entries.append(cur)
        elif cur is not None:
            if line.startswith("## ") or line.startswith("# "):
                cur = None
            else:
                cur["text"] += "\n" + line
    return entries


NAME = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:/-[a-z0-9]+)*"


def parse_matrix(text):
    """Parse '... Live @sha: a 1, b/-x 2; IDENTICAL c 3, d 4. Next: ...' robustly."""
    m = re.search(r"Live\s*@\s*`?([0-9a-f]{6,40})`?\**\s*:?\**\s*(.*)", text, re.S)
    if not m:
        return None
    sha, rest = m.group(1), clean_md(m.group(2))
    rest = re.split(r"\bNext\b|\n\s*\n", rest)[0]
    res = {}
    # split at IDENTICAL markers; segments after a marker are identical until ';' or end-of-sentence
    parts = re.split(r"(\bIDENTICAL\b:?)", rest)
    identical = False
    for p in parts:
        if p.startswith("IDENTICAL"):
            identical = True
            continue
        for seg in re.split(r";|\.\s", p):
            for item in seg.split(","):
                item = re.sub(r"\([^)]*\)", "", item).strip()
                mm = re.match(rf"^({NAME})\s*:?\s*(?:IDENTICAL\s*)?(\d+)(?:\s*(?:->|→)\s*(\d+))?", item)
                if not mm:
                    continue
                name, n = mm.group(1), int(mm.group(3) or mm.group(2))
                ident = identical or "IDENTICAL" in item
                names = name.split("/")
                base = names[0]
                full = [base]
                for suf in names[1:]:
                    full.append(re.sub(r"-[^-]*$", "", base) + suf if suf.startswith("-") else suf)
                for f in full:
                    res[f] = {"turn": n, "identical": ident}
        # an IDENTICAL group ends with a ';' – reset for the next part
        if identical and ";" in p:
            identical = False
    return sha, res


def gen_values(text):
    """Latest board value per generated fixture (gen-a-*, genb-*, genc-*), baseline number."""
    out = {}
    pat = re.compile(r"\b(gen-a-[a-z]+(?:-[a-z0-9]+)*|genb-[a-z]+(?:-[a-z0-9]+)*|genc-[a-z]+-\d+)\b\s*:?\s*"
                     r"(?:(IDENTICAL)(?:\s+(\d+))?|(?:DIVERGENT\s*)?(?:(?:at\s+)?turn\s*)?(\d+))")
    lines = clean_md(text).splitlines()
    names = set()
    for i, line in enumerate(lines, 1):
        for mm in pat.finditer(line):
            name = mm.group(1)
            ident = bool(mm.group(2))
            n = int(mm.group(3)) if ident and mm.group(3) else (int(mm.group(4)) if mm.group(4) else None)
            if name in ("genb-military", "genb-full", "genc-longmixed"):
                continue
            names.add(name)
            out[name] = {"turn": n, "identical": ident, "board_line": i}
    return out


def gen_group(name):
    return "genA" if name.startswith("gen-a") else "genB" if name.startswith("genb") else "genC"


# ---------------- totals ----------------
def totals():
    t = {}
    tv = safe(lambda: content(PRIV, "docs/clean/TEST_VECTORS.md", BRANCH), "", "TEST_VECTORS")
    cur = None
    for line in tv.splitlines():
        m = re.match(r"^###\s+`fixtures/([a-z0-9-]+)/`", line)
        if m:
            cur = m.group(1)
        m = re.search(r"start_turn:(\d+)`?,\s*`?turns:(\d+)", line)
        if m and cur and cur not in t:
            t[cur] = {"start": int(m.group(1)), "turns": int(m.group(2))}
    nf = safe(lambda: json.loads(content(PRIV, "docs/clean/new-fixtures/index.json", BRANCH)), {}, "new-fixtures index")
    for k, v in (nf.get("fixtures") or {}).items():
        sc = v.get("scenario") or {}
        if "turns" in sc:
            t[k] = {"start": sc.get("start_turn"), "turns": sc.get("turns")}
    return t


# ---------------- main ----------------
def main():
    os.makedirs(OUT, exist_ok=True)
    board = safe(lambda: content(PRIV, "docs/AGENT_BOARD.md", BRANCH), "", "AGENT_BOARD")
    entries = board_entries(board)
    data = {"generated_utc": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "generated_berlin": iso(NOW),
            "branch": BRANCH}

    # 1. live matrix
    live = None
    for e in reversed(entries):
        if e["agent"].lower() in ("opus", "agent") and re.search(r"Live\s*@", e["text"]):
            pm = parse_matrix(e["text"])
            if pm and pm[1]:
                live = (e, pm)
                break
    tot = totals()
    rows = []
    matrix = live[1][1] if live else {}
    for name, desc in SCENARIOS:
        v = matrix.get(name)
        tk = tot.get(name) or tot.get(ALIAS_TOTAL.get(name, ""))
        rows.append({"name": name, "desc": desc, "group": "", "turn": v["turn"] if v else None,
                     "identical": bool(v and v["identical"]), "measured": bool(v), "source": "live" if v else None,
                     "total": tk["turns"] if tk else None, "start": tk["start"] if tk else None})
    gv = gen_values(board)
    for name, v in sorted(gv.items(), key=lambda kv: (gen_group(kv[0]), kv[0])):
        lv = matrix.get(name)
        src = "live" if lv else "board"
        v = lv or v
        m5 = re.search(r"-(\d{3,5})$", name)
        tk = tot.get(name)
        rows.append({"name": name, "desc": GEN_DESC[gen_group(name)], "group": gen_group(name),
                     "turn": v["turn"], "identical": v["identical"], "measured": True, "source": src,
                     "board_line": v.get("board_line"),
                     "total": tk["turns"] if tk else (v["turn"] if v["identical"] and v["turn"] else (int(m5.group(1)) if m5 else None)),
                     "start": tk["start"] if tk else None})
    for g in ("genA", "genB", "genC"):
        if not any(r["group"] == g for r in rows):
            rows.append({"name": g, "desc": GEN_DESC[g], "group": g, "turn": None, "identical": False,
                         "measured": False, "source": None, "total": None, "start": None})
    data["scenarios"] = rows
    if live:
        e, (sha, _) = live
        data["matrix_source"] = {"time": e["time"], "agent": e["agent"], "line": e["line"], "sha": sha,
                                 "excerpt": clean_md(re.search(r"Live\s*@.*", e["text"], re.S).group(0))[:600]}
    else:
        data["matrix_source"] = None

    # 2. agents
    commits = safe(lambda: api(f"repos/{PRIV}/commits?sha={urllib.parse.quote(BRANCH, safe='')}&per_page=100"), [], "commits")
    head = commits[0] if commits else None
    data["head"] = {"sha": head["sha"][:7], "time": iso(parse_ts(head["commit"]["committer"]["date"])),
                    "subject": head["commit"]["message"].split("\n")[0][:140]} if head else None

    def last_board(agent):
        for e in reversed(entries):
            if e["agent"].lower() == agent:
                return {"time": e["time"], "line": e["line"],
                        "summary": clean_md(re.sub(r"^#+\s*", "", e["head"]))[:220]}
        return None

    opus_c = next((c for c in commits if "Claude-Session:" in c["commit"]["message"]), None)
    data["opus"] = {
        "last_commit": {"sha": opus_c["sha"][:7], "time": iso(parse_ts(opus_c["commit"]["committer"]["date"])),
                        "subject": opus_c["commit"]["message"].split("\n")[0][:140]} if opus_c else None,
        "last_board": last_board("opus")}

    # Codex: newest head among codex/* branches + last Codex board entry
    def codex_commit():
        best, page = None, 1
        while page < 10:
            br = api(f"repos/{PRIV}/branches?per_page=100&page={page}")
            for b in br:
                if b["name"].startswith("codex/"):
                    c = api(f"repos/{PRIV}/commits/{urllib.parse.quote(b['name'], safe='')}")
                    t = parse_ts(c["commit"]["committer"]["date"])
                    if best is None or t > best[0]:
                        best = (t, b["name"], c)
            if len(br) < 100:
                break
            page += 1
        if not best:
            return None
        t, name, c = best
        return {"sha": c["sha"][:7], "time": iso(t), "branch": name, "subject": c["commit"]["message"].split("\n")[0][:140]}
    data["codex"] = {"last_commit": safe(codex_commit, None, "codex branches"), "last_board": last_board("codex"),
                     "note": "Limit bis 22.10. 15:57"}

    # DeepSeek
    def runs(status):
        return api(f"repos/{PUB}/actions/workflows/opencode.yml/runs?status={status}&per_page=100").get("workflow_runs", [])
    inprog = safe(lambda: runs("in_progress"), [], "opencode runs")
    queued = safe(lambda: runs("queued"), [], "opencode queued")
    issues = safe(lambda: api(f"repos/{PUB}/issues?state=all&sort=created&direction=desc&per_page=100"), [], "issues")
    by_title = {}
    for i in issues:
        if "pull_request" not in i:
            by_title.setdefault(i["title"], i)
    ds = []
    for r in sorted(inprog, key=lambda r: r.get("run_started_at") or r["created_at"]):
        st = parse_ts(r.get("run_started_at") or r["created_at"])
        iss = by_title.get(r.get("display_title", ""))
        ds.append({"title": r.get("display_title", "")[:160], "run_url": r["html_url"],
                   "issue": iss["number"] if iss else None, "issue_url": iss["html_url"] if iss else None,
                   "started": iso(st), "minutes": int((NOW - st).total_seconds() // 60)})
    pulls = safe(lambda: api(f"repos/{PRIV}/pulls?state=closed&base={urllib.parse.quote(BRANCH, safe='')}&sort=updated&direction=desc&per_page=100"), [], "pulls")
    merged = []
    for p in pulls:
        if p.get("merged_at") and NOW - parse_ts(p["merged_at"]) <= timedelta(hours=24):
            merged.append({"number": p["number"], "title": p["title"][:160], "merged": iso(parse_ts(p["merged_at"])),
                           "author": p["user"]["login"]})
    merged.sort(key=lambda p: p["merged"], reverse=True)
    data["deepseek"] = {"running": ds, "count": len(ds), "queued": len(queued), "target": 15, "merged_24h": merged}
    # activity timestamps for the status badges (UTC ISO; age is computed client-side)
    def board_utc(b):
        if not b:
            return None
        try:
            t = datetime.strptime(b["time"], "%Y-%m-%d %H:%M").replace(tzinfo=BERLIN).astimezone(timezone.utc)
        except ValueError:
            return None
        return None if t > NOW + timedelta(minutes=5) else t  # ignore future-dated board entries

    def activity(agent, commit_utc):
        cands = []
        if commit_utc:
            cands.append((commit_utc, "Commit"))
        bt = board_utc(data[agent].get("last_board"))
        if bt:
            cands.append((bt, "Board-Eintrag"))
        if not cands:
            return None
        t, src = max(cands)
        return {"utc": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "berlin": iso(t), "source": src}

    data["opus"]["activity"] = activity("opus", parse_ts(opus_c["commit"]["committer"]["date"]) if opus_c else None)
    cc = data["codex"]["last_commit"]
    data["codex"]["activity"] = activity("codex", cc and datetime.strptime(cc["time"], "%Y-%m-%d %H:%M").replace(tzinfo=BERLIN).astimezone(timezone.utc))
    data["codex"]["paused_until_utc"] = datetime(2026, 10, 22, 15, 57, tzinfo=BERLIN).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["errors"] = ERRORS

    # history (one point per new live-matrix source)
    hist = []
    if PREV and os.path.exists(PREV):
        try:
            hist = json.load(open(PREV))
        except Exception:
            hist = []
    if data["matrix_source"]:
        key = f'{data["matrix_source"]["time"]}|{data["matrix_source"]["sha"]}'
        if not hist or hist[-1].get("key") != key:
            hist.append({"key": key, "time": data["matrix_source"]["time"], "sha": data["matrix_source"]["sha"],
                         "values": {r["name"]: {"turn": r["turn"], "identical": r["identical"]}
                                    for r in rows if r["source"] == "live"}})
    data["history"] = hist[-200:]
    json.dump(hist[-200:], open(os.path.join(OUT, "history.json"), "w"), ensure_ascii=False, indent=1)
    json.dump(data, open(os.path.join(OUT, "data.json"), "w"), ensure_ascii=False, indent=1)
    print(f"scenarios={len(rows)} live={'yes' if live else 'no'} ds_running={len(ds)} merged24h={len(merged)} errors={len(ERRORS)}")


if __name__ == "__main__":
    main()
