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

# Base scenarios (fixed list, hand-written German descriptions). Everything else is discovered.
SCENARIOS = [
    ("bc-full", "Deine BC-Aufnahme (10.08.2026)", "dein ganzes aufgezeichnetes Spiel NETEVENT_RECORDING_2026-08-10: 1.504 Spielerbefehle über 23.900 Züge, ab Zug 121", None),
    ("build", "Wintersturm", "Baustellen anlegen, Leibeigene bauen Gebäude fertig", "build"),
    ("production", "Wintersturm", "Rohstoffabbau und Produktionskette", "production"),
    ("late-economy", "Wintersturm", "über 1 h Wirtschaft: Zahltag, Steuern, Leibeigene kaufen, Bauernhof-Upgrade, Abgaben", None),
    ("camp-crawford", "Camp Crawford (Kampagne)", "Missionsskripte laufen, KI-Wirtschaft wächst, keine Spielerbefehle", None),
    ("combat", "Kampf-Testkarte", "zwei Armeen kämpfen gegeneinander", "combat"),
    ("combat-long", "Kampf-Testkarte", "Kampf, danach lange ruhige Welt (Wetter, Tiere)", None),
    ("combat-big", "Kampf-Testkarte", "große Schlacht: 5 Formationen + Kanonen je Seite", None),
    ("combat-waves", "Kampf-Testkarte", "Kampf in 4 Angriffswellen (248 Einheiten)", None),
    ("ai-long", "KI-Testkarte", "Computergegner (Dorf-KI + Armeen) spielen lange selbst", None),
    ("economy", "Dunkelforst", "frischer Kartenstart, Rohstoff-Abbauaufträge", "economy"),
    ("economy-mapstart", "Dunkelforst", "wie economy, direkt ab Kartenstart geladen", "economy"),
    ("wsecon", "Wintersturm", "Leibeigene kaufen und erstes Gebäude bauen", "wsecon"),
    ("wintersturm", "Wintersturm", "frischer Kartenstart ohne Befehle", "wintersturm"),
    ("wintersturm-mapstart", "Wintersturm", "wie wintersturm, direkt ab Kartenstart geladen", "wintersturm"),
    ("map-winterisland", "Winterinsel", "andere Mehrspielerkarte, fast statische Welt", None),
    ("late-ai", "KI-Testkarte", "1 h KI-Dörfer ohne Spieler (36.000 Züge)", None),
]
BASE = {n for n, *_ in SCENARIOS}
ALIAS_TOTAL = {"bc-full": "bc", "economy-mapstart": "economy", "wintersturm-mapstart": "wintersturm"}
CMD_NAMES = {
    69634: "Bewegen", 69635: "Bewegen (Formation)", 69636: "Leibeigene kaufen", 69638: "Verkaufen",
    69640: "Ausbauen", 69642: "Gebäude-Befehl", 69643: "Rekrutieren", 69644: "Rekrutieren",
    69646: "Steuern", 69647: "Entlassen", 69650: "Angreifen", 69655: "Bauen", 69656: "Reparieren",
    69657: "Abbau", 69658: "Forschung", 69672: "Arbeiterlimit", 69677: "Miliz", 69678: "Miliz",
    69680: "Alarm", 69681: "Alarm", 69683: "Überstunden", 69687: "Segnen",
}
MAPS = [("dunkelforst", "Dunkelforst"), ("economy", "Dunkelforst"), ("winterisland", "Winterinsel"),
        ("wintersturm", "Wintersturm"), ("wsecon", "Wintersturm"), ("crawford", "Camp Crawford (Kampagne)"),
        ("oldkings", "Old Kings Castle (Kampagne)"), ("combat", "Kampf-Testkarte"), ("ai", "KI-Testkarte"),
        ("saveload", "Wintersturm (Speicherstand)"), ("ws", "Wintersturm"), ("bc", "Deine BC-Aufnahme (Ausschnitt)")]


def cmd_name(cid, cls=""):
    if "BuildingCreator" in (cls or ""):
        return "Bauen"
    if "ExtractResource" in (cls or ""):
        return "Abbau"
    return CMD_NAMES.get(int(cid), f"Befehl {cid}")


def cmd_summary(types):
    """types: {name_or_id: count} -> 'Bewegen 1176, Leibeigene kaufen 800, …' (German, merged by name)."""
    agg = {}
    for k, n in (types or {}).items():
        cid, _, cls = str(k).partition(":")
        try:
            nm = cmd_name(int(cid), cls)
        except ValueError:
            nm = cmd_name(0, cls) if cls else str(k)
        agg[nm] = agg.get(nm, 0) + int(n)
    return ", ".join(f"{k} {v:,}".replace(",", ".") for k, v in sorted(agg.items(), key=lambda kv: -kv[1]))


def map_name(*hints):
    for h in hints:
        h = (h or "").lower()
        if not h:
            continue
        m = re.search(r"maps/\(\d\)\s*(?:ems\w*\s+)?([a-z]+)", h)
        if m:
            return m.group(1).capitalize()
        for key, nm in MAPS:
            if re.search(rf"(^|[/_\-.]){key}", h) or (len(key) > 3 and key in h):
                return nm
    return None


def describe(map_, start, turns, what):
    head = []
    if map_:
        head.append(map_)
    if start is not None:
        head.append(f"ab Zug {start:,}".replace(",", "."))
    t = " ".join(head)
    if turns:
        t += (", " if t else "") + f"{turns:,} Züge".replace(",", ".")
    return (t + ": " if t else "") + (what or "")


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


GEN_NAME = r"(?:gen-?[a-z]-[a-z0-9]+(?:-[a-z0-9]+)*|gen[a-z]-[a-z0-9]+(?:-[a-z0-9]+)*)"
SKIP_NAMES = {"genb-military", "genb-full", "genc-longmixed", "gen-a-tax-dispatch", "gena-tax-dispatch"}


def board_discover(entries):
    """Fixture names the board reports measurements for: 'live <fixture>: …', '<fixture> … first divergence',
    and generated-fixture names (gen-a-*, genb-*, genc-*) followed by a turn / IDENTICAL."""
    names = set()
    for e in entries:
        t = clean_md(e["text"])
        names.update(re.findall(r"\blive\s+([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\s*:", t))
        names.update(re.findall(r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\s+(?:DIVERGENT\s+)?first divergence", t))
        for mm in re.finditer(rf"\b({GEN_NAME})\b\s*:?\s*(?:IDENTICAL|DIVERGENT|turn|\d)", t):
            names.add(mm.group(1))
    return {n for n in names if n not in SKIP_NAMES and not n.startswith("agent")}


def board_value(entries, name, floor=0):
    """Newest board measurement for one fixture -> {turn, identical, time, line} or None.
    'live <name>: a -> N' takes N; a plain '<name> [DIVERGENT] [turn] N' / 'IDENTICAL [N]' takes the first value."""
    ne = re.escape(name)
    pats = [
        re.compile(rf"\blive\s+{ne}\s*:[^\n;]*?(?:->|→)\s*(?:(IDENTICAL)(?:\s+(\d+))?|(?:turn\s*)?(\d+))"),
        re.compile(rf"(?<![\w-]){ne}(?![\w-])\s*:?\s*(?:(IDENTICAL)(?:\s+(\d+))?|(?:DIVERGENT\s*)?(?:(?:at\s+)?turn\s*)?(\d+)(?!\s*(?:x|×|%|GB|MB|KB|min|turns|Züge)))"),
        re.compile(rf"(?<![\w-]){ne}(?![\w-])[^\n.;]{{0,40}}?first divergence(?:\s+(?:at\s+)?turn)?\s*(?:(IDENTICAL)(?:\s+(\d+))?|(\d+))"),
    ]
    for e in reversed(entries):
        t = clean_md(e["text"])
        for pi, p in enumerate(pats):
            mm = None
            for mm in p.finditer(t):
                break
            if mm:
                ident = bool(mm.group(1))
                if not ident:
                    # "economy 360, build 650 ... are all IDENTICAL" -> same sentence says IDENTICAL, none says diverg
                    a = max(t.rfind(".", 0, mm.start()), t.rfind("\n", 0, mm.start())) + 1
                    b = min([x for x in (t.find(".", mm.end()), t.find("\n", mm.end())) if x >= 0] or [len(t)])
                    seg = t[a:b]
                    if "IDENTICAL" in seg and not re.search(r"(?i)diverg|DIVERGENT", seg):
                        ident = True
                n = int(mm.group(2)) if ident and mm.group(2) else (int(mm.group(3)) if mm.group(3) else None)
                if pi > 0 and not ident and (n or 0) < floor:
                    continue  # loose mention below the known value (often another fixture's note) -> keep looking
                return {"turn": n, "identical": ident, "time": e["time"], "line": e["line"], "agent": e["agent"],
                        "explicit": pi == 0}
    return None


def count_commands(text):
    types, n = {}, 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            c = json.loads(line)
        except ValueError:
            continue
        n += 1
        k = f'{c.get("id")}:{c.get("class", "")}'
        types[k] = types.get(k, 0) + 1
    return n, types


def blob(repo, sha):
    d = api(f"repos/{repo}/git/blobs/{sha}")
    return base64.b64decode(d["content"]).decode("utf-8", "replace")


AGENT_ISSUE = re.compile(r"siedler-agent\s*#(\d+)")


def discover_new(branches_list, pulls_open, releases):
    """Discover generated scenarios from index.json / scenario.json files on clean/integration and on open
    fixture-gen PR branches (+ agent/fixtures-gen-* branches), plus fixtures-gen-* releases."""
    refs = [(BRANCH, None)]
    seen = {BRANCH}
    for p in pulls_open:
        ref = p["head"]["ref"]
        if re.search(r"fixture|gen-|genb|genc|gen[a-z]?-|mapstart", ref + " " + p["title"], re.I) and ref not in seen:
            refs.append((ref, p)); seen.add(ref)
    for b in branches_list:
        if b.startswith("agent/fixtures-gen") and b not in seen:
            refs.append((b, None)); seen.add(b)
    found = {}
    ci_sha = {}

    def put(name, origin, **kw):
        if not name or name in BASE or name in SKIP_NAMES:
            return
        f = found.setdefault(name, {"name": name, "origins": [], "issues": set()})
        if origin and f["origins"] and f["origins"][0].startswith(BRANCH) and not origin.startswith("Release"):
            origin = None  # already on clean/integration; a branch copy of the shared index is not its origin
        if origin and origin not in f["origins"]:
            f["origins"].append(origin)
        for k, v in kw.items():
            if v not in (None, "", {}, []) and f.get(k) in (None, "", {}, []):
                f[k] = v

    for ref, pr in refs:
        origin = f"PR #{pr['number']}: {pr['title'][:90]}" if pr else (f"Branch {ref}" if ref != BRANCH else f"{BRANCH} (docs/clean/new-fixtures)")
        issues = set(AGENT_ISSUE.findall((pr or {}).get("title", "") + " " + ((pr or {}).get("body") or "")))
        tree = safe(lambda: api(f"repos/{PRIV}/git/trees/{urllib.parse.quote(ref, safe='')}?recursive=1"), {}, f"tree {ref}")
        files = {t["path"]: t for t in tree.get("tree", []) if t.get("type") == "blob"}
        cand = [p for p in files if re.search(
            r"^(docs/clean/new-fixtures/.*\.json|fixtures-gen/.*\.json|(?:.*/)?gen-[^/]+/index\.json)$", p)]
        if ref == BRANCH:
            ci_sha = {p: t["sha"] for p, t in files.items()}
        for path in cand:
            if files[path].get("size", 0) > 3_000_000:
                continue
            if ref != BRANCH and ci_sha.get(path) == files[path]["sha"]:
                continue  # unchanged copy of the clean/integration file: not this branch's contribution
            j = safe(lambda: json.loads(blob(PRIV, files[path]["sha"])), None, f"json {path}")
            if not isinstance(j, dict):
                continue
            iss = issues | set(AGENT_ISSUE.findall(json.dumps({k: v for k, v in j.items() if k != "fixtures"})[:4000]))
            when = j.get("updated") or j.get("created")
            if isinstance(j.get("fixtures"), dict):
                for name, e in j["fixtures"].items():
                    if not isinstance(e, dict):
                        continue
                    sc = e.get("scenario") if isinstance(e.get("scenario"), dict) else {}
                    cmds = e.get("commands")
                    ncmd = cmds if isinstance(cmds, int) else (cmds.get("n") if isinstance(cmds, dict) else None)
                    types = e.get("command_types") or (cmds.get("per_event") if isinstance(cmds, dict) else None)
                    fd = e.get("first_divergence")
                    turn = fd.get("turn") if isinstance(fd, dict) else fd if isinstance(fd, int) else None
                    base = str(e.get("baseline") or "")
                    ident = turn is None and base.startswith("IDENTICAL")
                    put(name, origin, start=e.get("start_turn", sc.get("start_turn")), turns=e.get("turns", sc.get("turns")),
                        image=e.get("image") or sc.get("image"), commands=ncmd, types=types, kind=e.get("kind"),
                        index_value={"turn": turn if turn is not None else (int(base.split()[-1]) if ident and base.split()[-1].isdigit() else None),
                                     "identical": ident, "time": when} if (turn is not None or ident) else None)
                    if name in found and not found[name]["origins"][:1] == [f"{BRANCH} (docs/clean/new-fixtures)"]:
                        found[name]["issues"] |= iss
            elif path.endswith("scenario.json") and (j.get("name") or "").strip():
                name = j["name"]
                d = path.rsplit("/", 1)[0]
                ncmd = types = None
                cp = d + "/commands.jsonl"
                if cp in files and files[cp].get("size", 0) < 3_000_000:
                    txt = safe(lambda: blob(PRIV, files[cp]["sha"]), "", f"commands {name}")
                    if txt:
                        ncmd, types = count_commands(txt)
                put(name, origin, start=j.get("start_turn"), turns=j.get("turns"), image=j.get("image"),
                    commands=ncmd, types=types)
                if name in found:
                    found[name]["issues"] |= iss
    for r in releases:
        tag = r.get("tag_name", "")
        if not tag.startswith("fixtures-gen"):
            continue
        body = r.get("body") or ""
        names = set(re.findall(rf"\b({GEN_NAME})\b", body)) | {f for f in found if f in body}
        group = re.search(r"\bgen[-_ ]?([a-z])\b", body + " " + " ".join(a["name"] for a in r.get("assets", [])), re.I)
        for f in list(found):
            if group and re.match(rf"gen-?{group.group(1).lower()}-", f):
                names.add(f)
        for n in names:
            put(n, f"Release {tag}")
            if n in found:
                found[n]["issues"] |= set(AGENT_ISSUE.findall(body))
    return found


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


# ---------------- whole game (approximations) ----------------
HERE = os.path.dirname(os.path.abspath(__file__))
HEX6 = re.compile(r"0x([0-9A-Fa-f]{6})\b")  # same as tools/coverage_map.py
CMD_SEEN = {}  # scenario -> {event id}
MAP_ALIAS = {"winterinsel": "(4)WinterIsland", "camp crawford": "04_Crawford", "old kings castle": "15_OldKingsCastle"}


def cmd_ids(types):
    out = set()
    for k in (types or {}):
        try:
            out.add(int(str(k).partition(":")[0]))
        except ValueError:
            pass
    return out


def function_coverage(ci_files):
    """(a) cited native addresses in crates/s5clean/src that fall into a function of the static EXE inventory."""
    inv = json.load(open(os.path.join(HERE, "function_inventory.json")))
    ents = []
    for r in inv["functions"]:
        logic = r.endswith("L")
        r = r.rstrip("L")
        i = max(r.rfind("+"), r.rfind("-"))
        e = int(r[:i], 16)
        ents.append((e, e + int(r[i:], 16), logic))
    ents.sort()
    starts = [e for e, _h, _l in ents]
    cited = set()
    srcs = [(pth, t) for pth, t in ci_files.items() if pth.startswith("crates/s5clean/src/") and pth.endswith(".rs")]
    if not srcs:
        raise RuntimeError("no s5clean sources")
    for pth, t in srcs:
        for m in HEX6.finditer(blob(PRIV, t["sha"])):
            cited.add(int(m.group(1), 16))
    import bisect
    hit = set()
    for a in cited:
        i = bisect.bisect_right(starts, a) - 1
        j = i
        while j >= 0 and j > i - 64:  # bodies can overlap/interleave: look back a little
            e, hi, _l = ents[j]
            if e <= a <= hi:
                hit.add(j)
                break
            j -= 1
    logic_hit = sum(1 for j in hit if ents[j][2])
    tot, ltot = inv["total_functions"], inv["logic_functions"]
    return {"cited_addresses": len(cited), "functions_hit": len(hit), "logic_hit": logic_hit,
            "total": tot, "logic_total": ltot,
            "pct_logic": round(100 * logic_hit / ltot, 2), "pct_raw": round(100 * len(hit) / tot, 2),
            "module": inv["module"], "source": inv["source"], "source_commit": inv["source_commit"],
            "unclassified_kept_as_logic": inv["unclassified_kept_as_logic"], "static": True}


def test_coverage(rows):
    """(b) official maps and command types exercised by at least one scenario."""
    ml = json.load(open(os.path.join(HERE, "map_list.json")))
    official = ml["official"]
    norm = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
    tested, other = set(), set()
    for r in rows:
        mp = (r.get("map") or "").strip()
        if not mp:
            continue
        base = re.sub(r"\s*\(.*\)$", "", mp).lower()
        key = MAP_ALIAS.get(base)
        hit = [o for o in official if (key and o["name"] == key) or (not key and len(norm(base)) > 3 and norm(base) in norm(o["name"]))]
        if hit:
            tested.update(o["name"] for o in hit)
        else:
            other.add(mp)
    turn = content(PRIV, "docs/systems/turn.md", BRANCH)
    table = {int(m.group(1), 16): m.group(2) for m in re.finditer(r"^\|\s*0x(1[0-9a-fA-F]{4})\s*\|\s*(\w+)", turn, re.M)}
    if not table:
        raise RuntimeError("command table not found")
    seen = set().union(*CMD_SEEN.values()) if CMD_SEEN else set()
    done = sorted(c for c in table if c in seen)
    return {
        "maps": {"tested": len(tested), "total": len(official), "tested_list": sorted(tested),
                 "untested": [f'{o["kind"]}: {o["name"]}' for o in official if o["name"] not in tested],
                 "other_tested": sorted(other), "source": ml["source"], "user_map_count": ml["user_map_count"]},
        "commands": {"tested": len(done), "total": len(table),
                     "tested_list": [f"0x{c:x} {table[c]}" for c in done],
                     "untested": [f"0x{c:x} {n}" for c, n in sorted(table.items()) if c not in seen],
                     "source": "docs/systems/turn.md (Befehlstabelle aus recomp/command_table.py)"},
    }


# ---------------- main ----------------
def main():
    os.makedirs(OUT, exist_ok=True)
    board = safe(lambda: content(PRIV, "docs/AGENT_BOARD.md", BRANCH), "", "AGENT_BOARD")
    entries = board_entries(board)
    global BRANCHES
    BRANCHES = []
    for page in range(1, 10):
        br = safe(lambda: api(f"repos/{PRIV}/branches?per_page=100&page={page}"), [], "branches")
        BRANCHES += [b["name"] for b in br]
        if len(br) < 100:
            break
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
    matrix = live[1][1] if live else {}
    mtime = live[0]["time"] if live else None
    # command counts for base fixtures (committed fixtures/<dir>/commands.jsonl)
    ci_tree = safe(lambda: api(f"repos/{PRIV}/git/trees/{urllib.parse.quote(BRANCH, safe='')}?recursive=1"), {}, "tree")
    ci_files = {t["path"]: t for t in ci_tree.get("tree", []) if t.get("type") == "blob"}
    rows = []
    for name, map_, what, fdir in SCENARIOS:
        v = matrix.get(name)
        vsrc, vwhen = ("Opus Live-Matrix", mtime) if v else (None, None)
        # a board measurement written after the live matrix is newer -> it wins (matrix can be hours old)
        # a loose mention ("wsecon 229" inside another fixture's note) may only raise the value;
        # a drop needs an explicit "live <name>: a -> b" measurement
        bv = board_value(entries, name, floor=(v or {}).get("turn") or 0)
        if bv and (not live or int(bv["line"]) > int(live[0]["line"])):
            v, vsrc, vwhen = bv, f"Board ({bv['agent']}, Zeile {bv['line']})", bv["time"]
        tk = tot.get(name) or tot.get(ALIAS_TOTAL.get(name, ""))
        ncmd = summ = None
        cp = f"fixtures/{fdir}/commands.jsonl" if fdir else None
        if cp and cp in ci_files:
            txt = safe(lambda: blob(PRIV, ci_files[cp]["sha"]), "", f"commands {name}")
            if txt:
                ncmd, types = count_commands(txt)
                summ = cmd_summary(types)
                CMD_SEEN.setdefault(name, set()).update(cmd_ids(types))
        start, turns = (tk["start"], tk["turns"]) if tk else (None, None)
        if name == "bc-full":  # full recording, not the 2000-turn `bc` cut (fixtures/bc)
            start, turns, ncmd = 121, 23900, 1504
        if v and not v["identical"] and turns and v["turn"] and v["turn"] >= turns:
            v = dict(v, identical=True)  # matched up to the scenario's last turn = whole run identical
        rows.append({"name": name, "new": False, "map": map_, "turn": v["turn"] if v else None,
                     "identical": bool(v and v["identical"]), "measured": bool(v), "source": vsrc,
                     "when": vwhen, "total": turns, "start": start, "commands": ncmd,
                     "desc": describe(map_, start, turns, what + (f" · Befehle: {summ}" if summ else ""))})
    pulls_open = safe(lambda: api(f"repos/{PRIV}/pulls?state=open&per_page=100"), [], "open pulls")
    releases = safe(lambda: api(f"repos/{PRIV}/releases?per_page=50"), [], "releases")
    found = discover_new(BRANCHES, pulls_open, releases)
    for n in board_discover(entries):
        if n not in BASE and n not in found and re.match(GEN_NAME + "$", n):
            found[n] = {"name": n, "origins": ["Board"], "issues": set()}
    for name in sorted(found):
        f = found[name]
        lv = matrix.get(name)
        bv = board_value(entries, name)
        iv = f.get("index_value")
        if bv and (not lv or not live or int(bv["line"]) > int(live[0]["line"])):
            val, src, when = bv, f"Board ({bv['agent']}, Zeile {bv['line']})", bv["time"]
        elif lv:
            val, src, when = lv, "Opus Live-Matrix", mtime
        elif bv:
            val, src, when = bv, f"Board ({bv['agent']}, Zeile {bv['line']})", bv["time"]
        elif iv:
            val, src, when = iv, "index.json (Erzeugung)", iv.get("time")
        else:
            val, src, when = None, None, None
        start, turns = f.get("start"), f.get("turns")
        if turns is None and val and val["identical"] and val["turn"]:
            turns = val["turn"]
        m5 = re.search(r"-(\d{3,5})$", name)
        if turns is None and m5:
            turns = int(m5.group(1))
        map_ = map_name(f.get("image"), name)
        summ = cmd_summary(f.get("types"))
        CMD_SEEN.setdefault(name, set()).update(cmd_ids(f.get("types")))
        what = (f"Befehle: {summ}" if summ else ("ohne Spielerbefehle" if f.get("commands") == 0 else
                (f"{f['commands']} Befehle" if f.get("commands") else "Befehle unbekannt")))
        rows.append({"name": name, "new": True, "map": map_, "turn": val["turn"] if val else None,
                     "identical": bool(val and val["identical"]), "measured": bool(val), "source": src, "when": when,
                     "total": turns, "start": start, "commands": f.get("commands"),
                     "origins": f["origins"],
                     "issues": [{"n": int(i), "url": f"https://github.com/{PUB}/issues/{i}"} for i in sorted(f["issues"], key=int)],
                     "desc": describe(map_, start, turns, what)})
    data["scenarios"] = rows
    if live:
        e, (sha, _) = live
        data["matrix_source"] = {"time": e["time"], "agent": e["agent"], "line": e["line"], "sha": sha,
                                 "excerpt": clean_md(re.search(r"Live\s*@.*", e["text"], re.S).group(0))[:600]}
    else:
        data["matrix_source"] = None

    # percentages (approximations)
    num = den = 0
    used = 0
    for r in rows:
        if not r["measured"] or not r["total"]:
            continue
        if r["identical"]:
            ok = r["total"]
        elif r["turn"] is not None:
            st = r["start"] if (r["start"] is not None and r["turn"] >= r["start"]) else 0
            ok = max(0, min(r["total"], r["turn"] - st))
        else:
            continue
        num += ok; den += r["total"]; used += 1
    cov = safe(lambda: content(PRIV, "docs/clean/COVERAGE.md", BRANCH), "", "COVERAGE.md")
    mm = re.search(r"## Overall.*?distinct executed logic functions:\s*\**(\d+)\**.*?cited:\s*\**(\d+)\**", cov, re.S)
    cov_date = None
    if mm:
        cc = safe(lambda: api(f"repos/{PRIV}/commits?path=docs/clean/COVERAGE.md&sha={urllib.parse.quote(BRANCH, safe='')}&per_page=1"), [], "coverage date")
        cov_date = iso(parse_ts(cc[0]["commit"]["committer"]["date"])) if cc else None
    data["percent"] = {
        "identical_turns": {"pct": round(100 * num / den, 1) if den else None, "ok": num, "total": den, "scenarios": used},
        "coverage": {"pct": round(100 * int(mm.group(2)) / int(mm.group(1)), 1), "cited": int(mm.group(2)),
                     "executed": int(mm.group(1)), "as_of": cov_date} if mm else None,
    }

    data["percent"]["whole_game"] = safe(lambda: function_coverage(ci_files), None, "function inventory")
    data["percent"]["test_scope"] = safe(lambda: test_coverage(rows), None, "test scope")

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
        best = None
        for name in BRANCHES:
            if name.startswith("codex/"):
                c = api(f"repos/{PRIV}/commits/{urllib.parse.quote(name, safe='')}")
                t = parse_ts(c["commit"]["committer"]["date"])
                if best is None or t > best[0]:
                    best = (t, name, c)
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
    def kind(t):
        tl = t.lower()
        if t.startswith("[planner]"):
            return "Planer"
        if tl.startswith("port") or "function-backlog" in tl or "backlog" in tl or tl.startswith("funktion"):
            return "Funktionen"
        if tl.startswith("coverage") or "fixture gen" in tl or "new " in tl and "fixture" in tl or "map-start" in tl:
            return "Testszenario"
        return "Fehlersuche"
    ds = []
    for r in sorted(inprog, key=lambda r: r.get("run_started_at") or r["created_at"]):
        st = parse_ts(r.get("run_started_at") or r["created_at"])
        iss = by_title.get(r.get("display_title", ""))
        ds.append({"title": r.get("display_title", "")[:160], "run_url": r["html_url"],
                   "issue": iss["number"] if iss else None, "issue_url": iss["html_url"] if iss else None,
                   "kind": kind(r.get("display_title", "")), "started": iso(st), "minutes": int((NOW - st).total_seconds() // 60)})
    pulls = safe(lambda: api(f"repos/{PRIV}/pulls?state=closed&base={urllib.parse.quote(BRANCH, safe='')}&sort=updated&direction=desc&per_page=100"), [], "pulls")
    merged = []
    for p in pulls:
        if p.get("merged_at") and NOW - parse_ts(p["merged_at"]) <= timedelta(hours=24):
            merged.append({"number": p["number"], "title": p["title"][:160], "merged": iso(parse_ts(p["merged_at"])),
                           "author": p["user"]["login"]})
    merged.sort(key=lambda p: p["merged"], reverse=True)
    kinds = {}
    for x in ds:
        kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
    data["deepseek"] = {"running": ds, "kinds": kinds, "count": len(ds), "queued": len(queued), "target": 20, "merged_24h": merged}
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
    key = (f'{data["matrix_source"]["time"]}|{data["matrix_source"]["sha"]}' if data["matrix_source"] else "-")
    p1 = data["percent"]["identical_turns"]["pct"]
    p2 = (data["percent"]["coverage"] or {}).get("pct")
    wg = data["percent"]["whole_game"] or {}
    ts = data["percent"]["test_scope"] or {}
    p3, p4 = wg.get("pct_logic"), wg.get("pct_raw")
    p5 = f'{ts["maps"]["tested"]}/{ts["maps"]["total"]}' if ts else None
    p6 = f'{ts["commands"]["tested"]}/{ts["commands"]["total"]}' if ts else None
    last = hist[-1] if hist else {}
    if (not hist or last.get("key") != key or last.get("pct_identical") != p1 or last.get("pct_coverage") != p2
            or last.get("pct_whole_logic") != p3 or last.get("maps_tested") != p5 or last.get("commands_tested") != p6):
        hist.append({"key": key, "time": data["matrix_source"]["time"] if data["matrix_source"] else None,
                     "sha": data["matrix_source"]["sha"] if data["matrix_source"] else None,
                     "recorded": data["generated_berlin"], "pct_identical": p1, "pct_coverage": p2,
                     "pct_whole_logic": p3, "pct_whole_raw": p4, "maps_tested": p5, "commands_tested": p6,
                     "values": {r["name"]: {"turn": r["turn"], "identical": r["identical"]} for r in rows if r["measured"]}})
    data["history"] = hist[-200:]
    json.dump(hist[-200:], open(os.path.join(OUT, "history.json"), "w"), ensure_ascii=False, indent=1)
    json.dump(data, open(os.path.join(OUT, "data.json"), "w"), ensure_ascii=False, indent=1)
    print(f"scenarios={len(rows)} live={'yes' if live else 'no'} ds_running={len(ds)} merged24h={len(merged)} errors={len(ERRORS)}")


if __name__ == "__main__":
    main()
