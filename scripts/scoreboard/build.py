#!/usr/bin/env python3
"""Scoreboard data builder for GitHub Pages.

Reads (never writes) the private repo Kofi55533/siedler-sim-rs and the public repo
Kofi55533/siedler-agent via the GitHub REST API and writes data.json (numbers/titles only)
plus an appended history.json. No code, no tokens, no full board text are published.

env: GH_TOKEN (read access to both repos), OUT (output dir), PREV (optional previous history.json),
     GH_TOKEN_PUB (optional: built-in GITHUB_TOKEN, used for every call that does not touch the private repo; separate limit),
     API_CACHE (optional dir: ETag cache across runs; a 304 answer does not count against the rate limit),
     SB_MIN_REMAINING (default 400: skip the build when the PAT has fewer REST calls left).
If a fetch hits a rate limit (HTTP 403/429) or the board is unreadable, no data.json is written: OUT/SKIP_PUBLISH holds the
reason and the workflow keeps the last good gh-pages data (2026-10-20 13:04/13:18: an empty page was published, all fetches 403).
"""
import base64, json, os, re, sys, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PRIV = "Kofi55533/siedler-sim-rs"
PUB = "Kofi55533/siedler-agent"
BRANCH = "clean/integration"
BERLIN = ZoneInfo("Europe/Berlin")
TOKEN = os.environ.get("GH_TOKEN", "")
TOKEN_PUB = os.environ.get("GH_TOKEN_PUB", "") or TOKEN
API_CACHE = os.environ.get("API_CACHE", "")
STATS = {"requests": 0, "pat": 0, "pub": 0, "not_modified_pat": 0, "not_modified_pub": 0, "immutable_hits": 0}
GH_CLI = os.environ.get("SB_GH_CLI") == "1"  # local test runs: calls go through the gh CLI (its own login), no ETag cache
RATE = {"limited": False, "what": ""}
_CACHE = {}
OUT = os.environ.get("OUT", "out")
PREV = os.environ.get("PREV", "")
NOW = datetime.now(timezone.utc)
ERRORS = []

# Base scenarios (fixed list, hand-written German descriptions). Everything else is discovered.
SCENARIOS = [
    ("bc-full", "Deine BC-Aufnahme (10.08.2026)", "dein ganzes aufgezeichnetes Spiel NETEVENT_RECORDING_2026-08-10: 1.504 Spielerbefehle über 23.900 Züge, ab Zug 121", None),
    # bc-0804 (2026-10-21): Dominic's real BC game of 2026-08-04 (private release fixtures-gen-2026-10-19-bc-0804). Its fixture
    # PR #557 could not merge (scenario rule: no new code path / unmodelled marker vs bc-full), so no index.json on
    # clean/integration lists it; tracked here like bc-full. Not a gate fixture: the value comes from board lines.
    ("bc-0804", "EMS Wintersturm", "deine BC-Aufnahme vom 04.08.2026: 1.298 Spielerbefehle über 24.141 Züge, ab Zug 121", None),
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
FIXED_TOTAL = {"bc-full": (121, 23900, 1504), "bc-0804": (121, 24141, 1298)}  # (start turn, turns, player commands)
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


class RateLimited(Exception):
    code = 403


IMMUTABLE = re.compile(r"/git/blobs/[0-9a-f]{40}$|/commits/[0-9a-f]{40}$")


def cache_load():
    global _CACHE
    if API_CACHE:
        try:
            _CACHE = json.load(open(os.path.join(API_CACHE, "etags.json")))
        except Exception:
            _CACHE = {}


def cache_save():
    if API_CACHE:
        os.makedirs(API_CACHE, exist_ok=True)
        keep = {k: v for k, v in _CACHE.items() if v.get("used")}  # only entries this run used (no unbounded growth)
        for v in keep.values():
            v.pop("used", None)
        json.dump(keep, open(os.path.join(API_CACHE, "etags.json"), "w"))


def api(path, raw=False, auth=True):
    url = path if path.startswith("http") else "https://api.github.com/" + path.lstrip("/")
    is_api = url.startswith("https://api.github.com/")
    priv = PRIV in url or XC_DATA in url  # API path or raw download_url (raw.githubusercontent.com/<PRIV>/...): needs the PAT
    tok = TOKEN if priv else TOKEN_PUB
    if is_api and RATE["limited"]:
        raise RateLimited(RATE["what"])  # stop spending calls after the first rate-limit answer
    hdr = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "siedler-scoreboard"}
    if auth and tok:
        hdr["Authorization"] = "Bearer " + tok
    key = ("P " if priv else "U ") + url
    ent = _CACHE.get(key) if (is_api and not raw) else None
    if ent and IMMUTABLE.search(url.split("?")[0]):
        ent["used"] = 1; STATS["immutable_hits"] += 1
        return ent["data"]
    if ent and ent.get("etag"):
        hdr["If-None-Match"] = ent["etag"]
    if GH_CLI and is_api:
        import subprocess
        STATS["requests"] += 1; STATS["pat"] += 1
        a = ["gh", "api", url[len("https://api.github.com/"):]] + (["-H", "Accept: application/vnd.github.raw"] if raw else [])
        r = subprocess.run(a, capture_output=True)
        if r.returncode:
            err = r.stderr.decode("utf-8", "replace")
            if "rate limit" in err.lower():
                RATE["limited"], RATE["what"] = True, "gh CLI rate limit"
            raise urllib.error.HTTPError(url, 403 if ("403" in err or "rate limit" in err.lower()) else 404 if "404" in err else 500, "gh", None, None)
        return r.stdout if raw else json.loads(r.stdout)
    req = urllib.request.Request(url, headers=hdr)
    if is_api:
        STATS["requests"] += 1; STATS["pat" if tok == TOKEN and auth else "pub"] += 1
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
            etag = r.headers.get("ETag")
    except urllib.error.HTTPError as e:
        if e.code == 304 and ent:
            ent["used"] = 1; STATS["not_modified_pat" if tok == TOKEN and auth else "not_modified_pub"] += 1
            return ent["data"]
        if is_api and e.code in (403, 429) and (e.headers.get("X-RateLimit-Remaining") == "0" or e.code == 429
                                                 or "rate limit" in (e.read() or b"").decode("utf-8", "replace").lower()):
            RATE["limited"], RATE["what"] = True, f"{'PAT' if tok == TOKEN else 'GITHUB_TOKEN'} rate limit at {url.split('?')[0][23:90]}"
        raise
    if raw:
        return data
    d = json.loads(data)
    if is_api and (etag or IMMUTABLE.search(url.split("?")[0])) and len(data) < 8_000_000:
        _CACHE[key] = {"etag": etag, "data": d, "used": 1}
    return d


def safe(fn, default, what):
    try:
        return fn()
    except Exception as e:  # never leak details beyond the error class / HTTP code
        code = getattr(e, "code", "")
        ERRORS.append(f"{what}: {type(e).__name__} {code}".strip())
        return default


def content(repo, path, ref):
    if GH_CLI:
        return api(f"repos/{repo}/contents/{path}?ref={urllib.parse.quote(ref, safe='')}", raw=True).decode("utf-8", "replace")
    d = api(f"repos/{repo}/contents/{path}?ref={urllib.parse.quote(ref, safe='')}")
    if d.get("content"):
        return base64.b64decode(d["content"]).decode("utf-8", "replace")
    return api(d["download_url"], raw=True).decode("utf-8", "replace")


import urllib.parse  # noqa: E402


def api_public(path):
    """Public repo read: token first (rate limit), anonymous if the token is not allowed for that repo."""
    try:
        return api(path)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            return api(path, auth=False)
        raise


def iso(dt):
    return dt.astimezone(BERLIN).strftime("%Y-%m-%d %H:%M")


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def clean_md(s):
    return re.sub(r"[*`_]", "", s)


# ---------------- board ----------------
ENTRY_RE = re.compile(r"^(?:#+\s*)?(\d{4}-\d{2}-\d{2})(?:\s+(\d{1,2}:\d{2}))?(?:\s+Berlin)?\s*(?:[—–-]+\s*)?([A-Z][A-Za-z]+)\b(.*)$")


def board_entries(text):
    """Split the board into entries: a dated '... Berlin — Agent ...' line + following lines."""
    entries, cur = [], None
    for i, line in enumerate(text.splitlines(), 1):
        m = ENTRY_RE.match(line.strip())
        if m:
            # entries without a clock time ("2026-10-18 Berlin — Opus result") inherit the previous entry's time
            prev = entries[-1]["time"] if entries else ""
            when = f"{m.group(1)} {m.group(2)}" if m.group(2) else (prev if prev.startswith(m.group(1)) else m.group(1) + " 00:00")
            cur = {"line": i, "time": when, "agent": m.group(3), "head": line.strip(), "text": line}
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


# PR states for the "measured on clean/integration?" check (filled in main(): number -> "merged" | "open" | "closed",
# head branch -> number). A board line that reports "before X -> after Y" on an UNMERGED PR's branch measured Y on that branch,
# not on clean/integration: the scoreboard takes X (the base value) from it. (2026-10-20: bc-full jumped to 12440 from draft #594.)
PR_STATE, PR_BY_BRANCH = {}, {}
PR_INFO = {}  # number -> {author, state, head, title, merged} from the one paginated PR listing in main()
PRREF = re.compile(r"(?:\bPR\s*#|\(#|\bdraft\s+#|\bDRAFT\s+(?:PR\s+)?#)(\d{2,5})\b")
BRANCHREF = re.compile(r"\b(agent/[A-Za-z0-9._/-]+)")
BASEWORD = re.compile(r"(?i)\bbase\s+(?:origin/)?clean/integration|\bon\s+(?:current\s+)?(?:origin/)?clean/integration|"
                      r"(?:origin/)?clean/integration\s*@|\blive\s+base\b|\bbaseline\b|\(base\b|\bbase\s*@|"
                      r"\b(?:equals?|unchanged\s+from|same\s+as|identical\s+to)\s+(?:the\s+)?base\b|\bbase\s+values?\b")
# an entry that measured a PR head / branch (2026-10-20 12:22: "full live matrix re-measured on the rebased #542 head" gave
# bc-full 9525 from a pre-#594 head after #542 had merged, so the PR-state check no longer caught it). Such entries count only
# with their before-values or in a sentence that says the value is the base / clean/integration value (BASEWORD).
HEADMEAS = re.compile(r"(?i)\b(?:on|at|of|for)\s+(?:the\s+)?(?:rebased\s+|current\s+|new\s+|updated\s+)?(?:PR\s+|DRAFT\s+)?#\d{2,5}(?:'s)?\s+"
                      r"(?:PR\s+|branch\s+)?head\b|\bPR\s+head\b|\brebased\s+(?:PR\s+)?#\d{2,5}\b|"
                      r"\b(?:matrix|measured|re-?measured|live|verified|verification)\s+on\s+(?:the\s+)?(?:PR\s+|feature\s+|rebased\s+)?branch\b|"
                      r"\bon\s+(?:the\s+)?(?:branch\s+)?agent/[\w./-]+")
HEADDRAFT = re.compile(r"(?i)\bdraft\s+(?:PR\s+)?#(\d{2,5})\b")


def measures_head(e):
    """True if the entry measured a PR head or branch (by its wording), independent of the PR's current state."""
    if is_claim(e):
        return False
    t = clean_md(e["text"])
    head = clean_md(e.get("head") or t.split("\n", 1)[0])
    return bool(HEADMEAS.search(t) or HEADDRAFT.search(head))


def is_claim(e):
    """A claim entry ('... Muse claim: ...' without 'result' in its head line) is written before the work."""
    head = clean_md(e.get("head") or clean_md(e["text"]).split("\n", 1)[0])
    # only the entry kind right after the agent name counts ("— Muse claim:", "— Codex LANE-CLAIM"); a long one-line claim
    # often says "... post an evidence-only result ..." later, which must not turn it into a result
    return bool(CLAIM_HEAD.match(head))


CLAIM_HEAD = re.compile(r"(?i)^[#\s]*\d{4}-\d{2}-\d{2}(?:\s+\d{1,2}:\d{2})?(?:\s+Berlin)?\s*[—–-]*\s*[A-Za-z]+\s+(?:[\w-]+\s+){0,2}(?:lane-)?claim\b(?!\s*(?:\+|&|and|/)\s*result)")


def entry_unmerged_pr(e):
    """The unmerged PR a board entry measured on (its head line names it, or the text names its agent/* branch), else None.
    PRs that are only referenced as already merged ('after the #562 merge') do not count."""
    t = clean_md(e["text"])
    head = clean_md(e.get("head") or t.split("\n", 1)[0])
    if is_claim(e):
        return None  # a claim is written before the work: its numbers are the base values it starts from
    cands = [int(n) for n in PRREF.findall(head)]
    cands += [PR_BY_BRANCH[b.rstrip(".,;:)")] for b in BRANCHREF.findall(t) if b.rstrip(".,;:)") in PR_BY_BRANCH]
    for n in cands:
        if PR_STATE.get(n) in ("open", "closed"):
            return n
    return None


PR_SRC = {}
GATE = {}


def head_prs_docs_only(e):
    """The PRs an entry names (head line / agent/* branch, any state) are all fixture/docs-only -> its head = clean code."""
    t = clean_md(e["text"])
    head = clean_md(e.get("head") or t.split("\n", 1)[0])
    cands = {int(n) for n in PRREF.findall(head) if int(n) in PR_STATE}  # issue refs (#765 ...) are not PRs
    cands |= {PR_BY_BRANCH[b.rstrip(".,;:)")] for b in BRANCHREF.findall(t) if b.rstrip(".,;:)") in PR_BY_BRANCH}
    return bool(cands) and not any(pr_changes_source(n) for n in cands)


def pr_changes_source(n):
    """True if PR #n changes code (crates/, recomp/, tools/ ...); a fixture/docs-only PR measures clean/integration code."""
    if n not in PR_SRC:
        fs = []
        for page in range(1, 5):
            r = safe(lambda: api(f"repos/{PRIV}/pulls/{n}/files?per_page=100&page={page}"), None, f"files #{n}")
            if r is None:
                PR_SRC[n] = True  # unknown -> treat as code change (safe side)
                return True
            fs += [f["filename"] for f in r]
            if len(r) < 100:
                break
        PR_SRC[n] = any(not re.match(r"(docs/|fixtures-gen/|fixtures/)", f) for f in fs)
    return PR_SRC[n]


def board_value(entries, name, floor=0, total=None, start=0):
    """Newest board measurement for one fixture -> {turn, identical, time, line} or None.
    'live <name>: a -> N' takes N; a plain '<name> [DIVERGENT] [turn] N' / 'IDENTICAL [N]' takes the first value.
    Only clean/integration values count (Dominic 2026-10-20): on an entry about an unmerged PR the after-value is replaced by the
    before-value, and a plain value there counts only if its sentence says it is the base/clean/integration value.
    A value above the scenario total is impossible: IDENTICAL clamps to the total, a divergence turn above it is rejected."""
    ne = re.escape(name)
    pats = [
        re.compile(rf"\blive\s+{ne}\s*:\s*(?:before\s+)?(?:(?:turn\s*)?(?:IDENTICAL\s*)?(\d+))?[^\n;]*?(?:->|→)\s*(?:after\s+)?(?:(IDENTICAL)(?:\s+(\d+))?|(?:turn\s*)?(\d+))"),
        re.compile(rf"(?<![\w-]){ne}(?![\w-])\s*:?\s*(?:(IDENTICAL)(?:\s+(\d+))?|(?:DIVERGENT\s*)?(?:(?:at\s+)?turn\s*)?(\d+)(?!\d)(?!\s*(?:x|×|%|GB|MB|KB|min|turns|Züge|[-–]\s*\d)))"),
        re.compile(rf"(?<![\w-]){ne}(?![\w-])[^\n.;]{{0,40}}?first divergence(?:\s+(?:at\s+)?turn)?\s*(?:(IDENTICAL)(?:\s+(\d+))?|(\d+))"),
    ]
    if name == "bc-full":  # Codex writes "BC advances 5564 -> 7010" / "BC: 763 -> 1501" (no fixture name)
        pats.insert(0, re.compile(r"\bBC(?:\s+(?:advances|first divergence))?\s*:?\s*(\d+)\s*(?:->|→)\s*(?:(IDENTICAL)(?:\s+(\d+))?|(\d+))"))
    nexp = 2 if name == "bc-full" else 1  # patterns with a before-group
    for e in reversed(entries):
        t = clean_md(e["text"])
        claim = is_claim(e)
        unmerged = entry_unmerged_pr(e) if PR_STATE else None
        docs_only = bool(unmerged and not pr_changes_source(unmerged))
        if docs_only:
            unmerged = None  # fixture/docs-only PR: its live values are measured with clean/integration code
        if not unmerged and not docs_only and measures_head(e) and not head_prs_docs_only(e):
            unmerged = "head"  # measured on a PR head/branch (PR may have merged since): same rule as an unmerged PR
        for pi, p in enumerate(pats):
            for mm in p.finditer(t):
                explicit = pi < nexp
                g = mm.groups()
                if explicit:
                    before, ident, n_id, n_dv = g
                else:
                    before, (ident, n_id, n_dv) = None, g
                ident = bool(ident)
                n = (int(n_id) if ident and n_id else (int(n_dv) if n_dv else None))
                if not explicit and not ident:
                    # "economy 360, build 650 ... are all IDENTICAL" -> same clause says IDENTICAL, none says diverg
                    # (clause ends at ';' too: "bc-full 12440, ..., late-economy 11138; IDENTICAL: wintersturm 1021" -> only
                    # the values after "IDENTICAL:" are identical)
                    a = max(t.rfind(".", 0, mm.start()), t.rfind("\n", 0, mm.start()), t.rfind(";", 0, mm.start())) + 1
                    b = min([x for x in (t.find(".", mm.end()), t.find("\n", mm.end()), t.find(";", mm.end())) if x >= 0] or [len(t)])
                    seg = t[a:b]
                    if "IDENTICAL" in seg and not re.search(r"(?i)diverg|DIVERGENT", seg):
                        ident = True
                arrow = re.match(r"\s*(?:->|→)\s*(?:after\s+)?(?:DIVERGENT\s*)?(?:turn\s*)?(IDENTICAL)?\s*(\d+)?", t[mm.end():])
                if not explicit and arrow and (arrow.group(1) or arrow.group(2)):  # "bc-full 763 -> 1501": the after-value counts
                    before = n if not ident else before
                    ident = bool(arrow.group(1))
                    n = int(arrow.group(2)) if arrow.group(2) else n
                if claim and (explicit or (arrow and (arrow.group(1) or arrow.group(2)))):
                    # a claim measures nothing: "does not duplicate open #606 (ai-long 266 -> 283)" names another PR's result;
                    # only its before/base value is a clean/integration number
                    if before is None:
                        continue
                    n, ident = int(before), False
                if unmerged:
                    if before is not None:
                        n, ident = int(before), False  # the before-value is the clean/integration measurement
                    else:
                        a = max(t.rfind(".", 0, mm.start()), t.rfind("\n", 0, mm.start())) + 1
                        if not BASEWORD.search(t[a:mm.end() + 40]):
                            continue  # value measured on the unmerged PR branch -> not a clean/integration value
                if total and n:
                    if ident and n > total:
                        n = total  # "IDENTICAL 24000" (tick count) on a locally extended copy -> identical over the official total
                    elif not ident and n > (start or 0) + total:
                        continue  # divergence turn after the scenario's last turn: impossible (command id, other number, ...)
                    elif not ident and n == (start or 0) + total:
                        n, ident = total, True  # "turn 6200" = start 200 + 6000: ran to the last turn without divergence
                if not explicit and not ident and (n or 0) < floor:
                    continue  # loose mention below the known value (often another fixture's note) -> keep looking
                return {"turn": n, "identical": ident, "time": e["time"], "line": e["line"], "agent": e["agent"],
                        "explicit": explicit, "unmerged_pr": unmerged}
    return None


GATE_ROW = re.compile(r"^(\S+)\s+base\s+(\S+)\s+pr\s+(\S+)\s+(same|better|REGRESSION)\s*$")


def gate_values(limit=80):
    """Authoritative clean/integration values from the self-merge gate (Dominic 2026-10-20): for every PR the gate merged into
    clean/integration, its PASS comment holds the live matrix 'base' vs 'pr' (= the PR on its base = the post-merge value).
    Merges are replayed oldest -> newest. A gate whose measured base is not the parent of its merge commit (base moved
    while it ran, e.g. #611 measured before #542 merged) counts its unchanged rows only as of its base commit's time, so a
    merge after that base (#542: late-economy IDENT 36000) keeps its value; rows the PR changed count as of the merge. -> {fixture: {turn, identical, time (Berlin, merge time), pr}}"""
    out = {}
    pl = []
    for page in (1, 2, 3):
        r = safe(lambda: api(f"repos/{PRIV}/pulls?state=closed&base={urllib.parse.quote(BRANCH, safe='')}&sort=updated&direction=desc&per_page=100&page={page}"), [], "merged pulls")
        pl += r
        if len(r) < 100:
            break
    merged = sorted((p for p in pl if p.get("merged_at")), key=lambda p: p["merged_at"], reverse=True)[:limit]
    for p in reversed(merged):  # oldest first
        cs = safe(lambda: api(f"repos/{PRIV}/issues/{p['number']}/comments?per_page=100"), [], f"gate #{p['number']}")
        body = next((c["body"] for c in reversed(cs or []) if "self-merge gate: PASS" in (c.get("body") or "").split("\n", 1)[0]
                     and "merged into" in (c.get("body") or "") and "dry-run" not in (c.get("body") or "")), None)
        if not body:
            continue
        stale_since = None  # outdated base: merges after this time are not in the gate's measurement
        mb = re.search(r"\bbase=\S+@([0-9a-f]{7,40})", body)
        if mb and p.get("merge_commit_sha"):
            mc = safe(lambda: api(f"repos/{PRIV}/commits/{p['merge_commit_sha']}"), {}, f"merge #{p['number']}")
            parents = [x["sha"] for x in (mc.get("parents") or [])]
            if parents and not parents[0].startswith(mb.group(1)):
                bc = safe(lambda: api(f"repos/{PRIV}/commits/{mb.group(1)}"), {}, f"gate base #{p['number']}")
                stale_since = (bc.get("commit") or {}).get("committer", {}).get("date") or "0000"
        when = iso(parse_ts(p["merged_at"]))
        for line in body.splitlines():
            m = GATE_ROW.match(line.strip())
            if not m:
                continue
            prev = out.get(m.group(1))
            # as-of time of the measurement: the merge for a fresh gate or a row the PR changed; the measured base's commit
            # time for an unchanged row on an outdated base (it says nothing about merges after that base)
            asof = stale_since if stale_since and m.group(2) == m.group(3) else p["merged_at"]
            if prev and prev["asof"] > asof:
                continue  # an earlier merge's value is newer knowledge than this outdated base
            v = m.group(3)
            mi = re.match(r"IDENT\((\d+)\)$", v)
            if mi:
                out[m.group(1)] = {"turn": int(mi.group(1)), "identical": True}
            elif v.isdigit() and int(v) > 0:  # 0 = no live result (fixture missing / s5-live gave nothing)
                out[m.group(1)] = {"turn": int(v), "identical": False}
            else:
                continue
            out[m.group(1)].update(time=when, pr=p["number"], asof=asof)
    return out


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


# ---------------- shared fleet (both DeepSeek pools) ----------------
FLEET_BRANCH = "agents/control"
POOLS = [("Kofi55533/siedler-agent", "Kofi55533", "Dominics eigener Pool"),
         ("Dominickofi00/opencode-runner", "Dominickofi00", "zweites Konto")]


FLEET_KIND = {"port": "Funktionen", "diagnose": "Fehlersuche", "scenario": "Testszenario", "code": "Code", "hygiene": "Code"}


def fleet_run(r, repo, account, by_run):
    st = parse_ts(r.get("run_started_at") or r["created_at"])
    t = by_run.get(str(r["id"])) or {}
    return {"url": r["html_url"], "run_id": r["id"], "status": r["status"], "started": iso(st),
            "minutes": int((NOW - st).total_seconds() // 60), "pool": repo, "account": account,
            "task_id": t.get("id"), "task_kind": t.get("kind"), "task_title": (t.get("title") or "")[:160] or None,
            "kind": FLEET_KIND.get(t.get("kind"), "Fehlersuche" if not t else "Code")}


_FLEET_STATE = []


def fleet_state():
    """agents/control fleet/state.json (legacy fleet + autonomy_v2), read once per build."""
    if not _FLEET_STATE:
        _FLEET_STATE.append(safe(lambda: json.loads(content(PRIV, "fleet/state.json", FLEET_BRANCH)), {}, "fleet state"))
    return _FLEET_STATE[0]


def fleet_pools():
    state = fleet_state()
    cfg = state.get("config") or {}
    holds = cfg.get("pool_holds") or {}
    provs = state.get("providers") or {}
    tasks = list((state.get("tasks") or {}).values())
    midnight = NOW.astimezone(BERLIN).replace(hour=0, minute=0, second=0, microsecond=0)
    since = midnight.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    by_run = {}
    for t in tasks:
        rid = str((t.get("lease") or {}).get("run_id") or "")
        if rid and (rid not in by_run or t.get("status") == "running"):
            by_run[rid] = t
    out = []
    for repo, account, label in POOLS:
        def wruns(q):
            res, page = [], 1
            while page <= 5:
                d = api_public(f"repos/{repo}/actions/workflows/fleet-worker.yml/runs?per_page=100&page={page}&{q}")
                res += d.get("workflow_runs", [])
                if len(d.get("workflow_runs", [])) < 100:
                    break
                page += 1
            return res
        today = safe(lambda: wruns("created=" + urllib.parse.quote(">=" + since)), None, f"fleet-worker runs {account}")
        active = safe(lambda: wruns("status=in_progress") + wruns("status=queued"), None, f"fleet-worker active {account}")
        conc = {}
        for r in today or []:
            k = r.get("conclusion") or r.get("status")
            conc[k] = conc.get(k, 0) + 1
        prov = provs.get(repo) or {}
        bu = prov.get("blocked_until") or 0
        blocked_until = datetime.fromtimestamp(bu, timezone.utc) if bu else None
        hold = holds.get(repo)
        mine = [t for t in tasks if (t.get("lease") or {}).get("pool") == repo]
        tstat = {}
        for t in mine:
            if (t.get("updated") or "") >= midnight.astimezone(timezone.utc).isoformat()[:19]:
                tstat[t["status"]] = tstat.get(t["status"], 0) + 1
        prs = sorted({(t["pr"], t.get("pr_url") or f"https://github.com/{PRIV}/pull/{t['pr']}", t["title"][:140], t["status"])
                      for t in mine if t.get("pr")}, reverse=True)
        ls = prov.get("last_success")
        out.append({
            "pool": repo, "account": account, "label": label,
            "configured_slots": (cfg.get("pools") or {}).get(repo),
            "active": len(active) if active is not None else None,
            "active_runs": [fleet_run(r, repo, account, by_run) for r in sorted(active or [], key=lambda r: r.get("run_started_at") or r["created_at"])][:40],
            "today": {"total": len(today) if today is not None else None, "success": conc.get("success", 0),
                      "failure": conc.get("failure", 0),
                      "other": sum(v for k, v in conc.items() if k not in ("success", "failure"))},
            "tasks_today": tstat,
            "held": bool(hold),
            "hold_reason": (re.split(r"(?<=[.!?])\s", hold.strip())[0][:160] if hold else None),
            "blocked_until": iso(blocked_until) if blocked_until and blocked_until > NOW else None,
            "provider_errors": prov.get("errors", 0),
            "last_success": iso(parse_ts(ls)) if ls else None,
            "prs": [{"number": n, "url": u, "title": ti, "status": st} for n, u, ti, st in prs][:30],
            "pr_count": len(prs),
            "runs_url": f"https://github.com/{repo}/actions/workflows/fleet-worker.yml",
        })
    return {"pools": out, "today_since": iso(midnight), "enabled": cfg.get("enabled"),
            "model": cfg.get("model"), "queued_tasks": sum(1 for t in tasks if t.get("status") == "queued"),
            "slots": sum(int(v) for v in (cfg.get("pools") or {}).values()) if cfg.get("pools") else None} if state else None


# ---------------- Fleet v2 slot view (siedler-agent Actions) ----------------
# Mirrors scripts/autonomy.py: every queued/running run in siedler-agent occupies one of CAPACITY slots; DeepSeek workers
# (fleet-worker.yml) up to FLEET_V2_WORKER_LIMIT, gate runs (fleet-verify/fleet-integrate/gate-baseline regate) up to VERIFY_SLOTS (10).
V2_CAPACITY = 20  # scripts/autonomy_state.py CAPACITY (the build checks out only scripts/scoreboard)
V2_GATE = 10  # scripts/autonomy_state.py VERIFY_SLOTS
V2_GROUPS = [  # (key, label, workflow files)
    ("worker", "DeepSeek-Worker", ("fleet-worker.yml",)),
    ("gate", "Gate", ("fleet-verify.yml", "fleet-integrate.yml", "gate-baseline.yml", "gate.yml")),
    ("control", "Steuerung/Scoreboard (kurz)", ("fleet-control.yml", "scoreboard.yml", "build-cache.yml", "coverage.yml",
                                                "stale-drafts.yml", "fleet-review.yml", "muse-gate.yml")),
    ("legacy", "Legacy (opencode/refill/Planer)", ("opencode.yml", "refill.yml")),
]
V2_ACTIVE = ("queued", "in_progress", "waiting", "pending", "requested")


def v2_group(path):
    f = (path or "").rsplit("/", 1)[-1]
    for key, _, files in V2_GROUPS:
        if f in files:
            return key
    return "control"  # any other short helper workflow


V2_SUB = {"fleet-verify.yml": "preflight", "fleet-integrate.yml": "integration", "gate-baseline.yml": "regate", "gate.yml": "regate"}
# Brother account (Dominic 2026-10-21): the public fork runs its own fleet-worker-v2 pool on the same v2 queue
# (fleet-pool-refill admits up to FLEET_POOL_WORKER_LIMIT, default 20); its leases carry pool=<fork>, its PRs are opened as
# Dominickofi00. Public repo: read with GITHUB_TOKEN / anonymously, no extra secret.
FORK = "Dominickofi00/siedler-agent"
try:
    FORK_CAPACITY = int(os.environ.get("FLEET_POOL_WORKER_LIMIT") or 20)
except ValueError:
    FORK_CAPACITY = 20
ACCOUNTS = [(PUB, "Kofi55533"), (FORK, "Dominickofi00")]
ACCOUNT_OF_REPO = {r: a for r, a in ACCOUNTS}


_ACTIVE = {}  # repo -> its queued/running runs, read once per build


def active_runs(repo):
    if repo in _ACTIVE:
        return _ACTIVE[repo]
    runs = _ACTIVE[repo] = {}
    for status in V2_ACTIVE:
        page = 1
        while page <= 3:
            d = api_public(f"repos/{repo}/actions/runs?status={status}&per_page=100&page={page}")
            for r in d.get("workflow_runs", []):
                runs[r["id"]] = r
            if len(d.get("workflow_runs", [])) < 100:
                break
            page += 1
    return runs


def v2_account_view(main_runs, fork_runs, wlimit):
    """Per-account slot use: main = workers / gate preflights / integration / regate / control; fork = workers / refill."""
    def cnt(rs, pred):
        sel = [r for r in rs if pred(r)]
        return {"used": len(sel), "running": sum(1 for r in sel if r["status"] == "in_progress"),
                "waiting": sum(1 for r in sel if r["status"] != "in_progress")}
    wf = lambda r: (r.get("path") or "").rsplit("/", 1)[-1]
    m, f = list(main_runs.values()), list((fork_runs or {}).values())
    main_parts = [dict(key="worker", label="DeepSeek-Worker", limit=wlimit, **cnt(m, lambda r: v2_group(r.get("path")) == "worker"))]
    for k, lbl in (("preflight", "Gate-Preflight"), ("integration", "Integration"), ("regate", "Regate/Baseline")):
        main_parts.append(dict(key=k, label=lbl, limit=None, **cnt(m, lambda r, k=k: V2_SUB.get(wf(r)) == k)))
    main_parts.append(dict(key="control", label="Steuerung (kurz)", limit=None,
                           **cnt(m, lambda r: v2_group(r.get("path")) in ("control", "legacy"))))
    gate = cnt(m, lambda r: v2_group(r.get("path")) == "gate")
    accts = [dict(account="Kofi55533", repo=PUB, role="Hauptkonto", capacity=V2_CAPACITY, worker_limit=wlimit,
                  gate_limit=V2_GATE, gate_used=gate["used"], parts=main_parts,
                  runs_url=f"https://github.com/{PUB}/actions", **cnt(m, lambda r: True))]
    fork_parts = [dict(key="worker", label="DeepSeek-Worker", limit=FORK_CAPACITY, **cnt(f, lambda r: wf(r) == "fleet-worker.yml")),
                  dict(key="control", label="Pool-Refill (kurz)", limit=None, **cnt(f, lambda r: wf(r) != "fleet-worker.yml"))]
    accts.append(dict(account="Dominickofi00", repo=FORK, role="Bruder-Konto (Fork)", capacity=FORK_CAPACITY,
                      worker_limit=FORK_CAPACITY, gate_limit=0, gate_used=0, parts=fork_parts, readable=fork_runs is not None,
                      runs_url=f"https://github.com/{FORK}/actions/workflows/fleet-worker.yml", **cnt(f, lambda r: True)))
    total = {k: sum(a[k] for a in accts) for k in ("used", "running", "waiting", "capacity")}
    total["workers"] = sum(a["parts"][0]["used"] for a in accts)
    total["workers_running"] = sum(a["parts"][0]["running"] for a in accts)
    total["worker_limit"] = wlimit + FORK_CAPACITY
    return accts, total


def v2_packages(av, prs):
    """Packages (sim-rs PRs of v2 workers) by origin account. Account = PR author when it is one of the two accounts,
    else the pool recorded on the unit's lease/dispatch (fork pool -> Dominickofi00), else Kofi55533."""
    units, leases, tickets = av.get("units") or {}, av.get("leases") or {}, av.get("tickets") or {}
    pool_of = {}
    for uid, u in units.items():
        if (u.get("dispatch") or {}).get("pool"):
            pool_of[uid] = u["dispatch"]["pool"]
    for uid, l in leases.items():
        if l.get("pool"):
            pool_of[uid] = l["pool"]
    by_pr = {}
    for t in tickets.values():
        if t.get("pr"):
            by_pr[int(t["pr"])] = t
    nums = set(by_pr) | {n for n, p in prs.items() if (p.get("head") or "").startswith("agent/unit-")}
    rows, acc = [], {a: {"open": 0, "merged": 0, "closed": 0, "total": 0, "tickets": {}} for _, a in ACCOUNTS}
    for n in sorted(nums, reverse=True):
        p, t = prs.get(n) or {}, by_pr.get(n) or {}
        unit = t.get("unit") or (re.sub(r"-e\d+$", "", p["head"][len("agent/unit-"):]) if (p.get("head") or "").startswith("agent/unit-") else None)
        author = p.get("author")
        if author in acc:
            account, how = author, "PR-Autor"
        elif unit and pool_of.get(unit) in ACCOUNT_OF_REPO:
            account, how = ACCOUNT_OF_REPO[pool_of[unit]], "Lease-Pool"
        else:
            account, how = "Kofi55533", "Standard"
        state = p.get("state") or ("merged" if t.get("status") == "accepted" else "open")
        a = acc[account]
        a[state if state in ("open", "merged") else "closed"] += 1
        a["total"] += 1
        if t:
            a["tickets"][t.get("status") or "?"] = a["tickets"].get(t.get("status") or "?", 0) + 1
        rows.append({"number": n, "url": f"https://github.com/{PRIV}/pull/{n}", "title": (p.get("title") or "")[:140],
                     "account": account, "identified_by": how, "author": author, "state": state, "unit": unit,
                     "ticket": t.get("status"), "merged": p.get("merged")})
    return {"by_account": acc, "prs": rows[:150], "count": len(rows),
            "rule": "PR-Autor (Dominickofi00 = Worker im Fork); sonst Lease/Dispatch-Pool der Einheit in fleet/state.json"}


def fleet_v2():
    runs = active_runs(PUB)
    fork_runs = safe(lambda: active_runs(FORK), None, "fork runs")
    av = (fleet_state() or {}).get("autonomy_v2") or {}
    units = av.get("units") or {}
    leases = av.get("leases") or {}
    lease_by_run = {str(l.get("run")): uid for uid, l in leases.items() if l.get("run")}
    enabled = os.environ.get("FLEET_V2_ENABLED", "").strip().lower()
    try:
        wlimit = int(os.environ.get("FLEET_V2_WORKER_LIMIT") or 16)
    except ValueError:
        wlimit = 16
    wlimit = min(wlimit, V2_CAPACITY - V2_GATE)  # autonomy.control clamps the rollout variable the same way
    limits = {"worker": wlimit, "gate": V2_GATE, "control": None, "legacy": 0 if enabled == "true" else None}
    groups = {k: {"key": k, "label": lbl, "used": 0, "running": 0, "waiting": 0, "limit": limits[k]} for k, lbl, _ in V2_GROUPS}
    out = []
    every = [(r, PUB) for r in runs.values()] + [(r, FORK) for r in (fork_runs or {}).values()]
    for r, repo in sorted(every, key=lambda x: x[0].get("run_started_at") or x[0]["created_at"]):
        wfile = (r.get("path") or "").rsplit("/", 1)[-1]
        g = v2_group(r.get("path")) if repo == PUB else ("worker" if wfile == "fleet-worker.yml" else "control")
        run_ = r["status"] == "in_progress"
        if repo == PUB:
            groups[g]["used"] += 1
            groups[g]["running" if run_ else "waiting"] += 1
        st = parse_ts(r.get("run_started_at") or r["created_at"])
        title = r.get("display_title") or r.get("name") or ""
        m = re.match(r"domain\s+(\S+)", title)
        uid = lease_by_run.get(str(r["id"])) or (m.group(1) if m else None)
        u = units.get(uid) if uid else None
        out.append({"group": g, "workflow": wfile, "name": r.get("name"),
                    "account": ACCOUNT_OF_REPO[repo], "repo": repo, "sub": V2_SUB.get(wfile) if repo == PUB else None,
                    "title": title[:160], "status": r["status"], "url": r["html_url"], "started": iso(st),
                    "minutes": max(0, int((datetime.now(timezone.utc) - st).total_seconds() // 60)),
                    "unit": uid if u else None, "unit_title": (u or {}).get("title"),
                    "domain": (u or {}).get("domain"),
                    "started_as": m.group(1) if m and u and m.group(1) != uid else None})  # unit split/regrouped since dispatch
    accounts, total = v2_account_view(runs, fork_runs, wlimit)
    ustat = {}
    for u in units.values():
        ustat[u.get("status") or "?"] = ustat.get(u.get("status") or "?", 0) + 1
    term = {"accepted", "equivalent"}
    verified = set()
    for u in units.values():
        v = u.get("verified")
        if isinstance(v, dict):
            verified |= {a for a, p in v.items() if isinstance(p, dict) and p.get("status") in term}
    tickets = av.get("tickets") or {}
    tstat = {}
    for t in tickets.values():
        tstat[t.get("status") or "?"] = tstat.get(t.get("status") or "?", 0) + 1
    order = ["worker", "gate", "control", "legacy"]
    out.sort(key=lambda r: (order.index(r["group"]), r["account"] != "Kofi55533", r["started"]))
    lease_pools = {}
    for l in leases.values():
        if l.get("run"):
            k = ACCOUNT_OF_REPO.get(l.get("pool") or PUB, l.get("pool"))
            lease_pools[k] = lease_pools.get(k, 0) + 1
    return {
        "enabled": (enabled == "true") if enabled else None, "capacity": V2_CAPACITY,  # used/running/capacity/groups: main account
        "used": len(runs), "running": sum(1 for r in runs.values() if r["status"] == "in_progress"),
        "groups": [groups[k] for k in order], "runs": out[:80],
        "accounts": accounts, "total": total,
        "packages": v2_packages(av, PR_INFO) if av else None,
        "runs_url": f"https://github.com/{PUB}/actions",
        "domain": {"units": len(units), "unit_status": ustat, "leases": len(leases),
                   "leases_with_run": sum(1 for l in leases.values() if l.get("run")), "leases_by_account": lease_pools,
                   "tickets": tstat, "inventory": av.get("total"), "verified_functions": len(verified),
                   "provider_hold": bool(av.get("provider_hold")) and (not av["provider_hold"].get("until") or av["provider_hold"]["until"] > datetime.now(timezone.utc).timestamp()), "integration": bool(av.get("integration"))} if av else None,
    }


# ---------------- xc per-call differential (scripts/xc, since 2026-10-23) ----------------
# The census (xc-census.yml) measures every task kind at the original's real calls; workers are the fix jobs of
# xc-worker.yml runs, one job per failing kind, in the main repo and in pool repos of other accounts that call the same
# reusable workflow. A slot is a job, not a run. Kinds are tracked by open `[xc] KIND` issues (label xc-kind) in PUB.
XC_POOLS = [(PUB, "Kofi55533", "XC_WORKERS", 10), ("Dominickofi00/opencode-runner", "Dominickofi00", "XC_POOL_WORKERS", 20)]
XC_RUNS = ("xc-worker.yml", "xc-pool.yml")
XC_DATA = "Kofi55533/siedler-data"
XC_CLAIM_H, XC_ATTEMPTS = 7, 3  # scripts/xc/xc.py CLAIM_H, ATTEMPTS


def xc_fields(body):
    return dict(line.split(" ", 1) for line in (body or "").splitlines() if " " in line)


def xc_worklist():
    """Counts of the private work list (siedler-data release xc-worklist); the file itself holds game state."""
    rel = api(f"repos/{XC_DATA}/releases/tags/xc-worklist")
    asset = next(a for a in rel["assets"] if a["name"] == "worklist.json")
    req = urllib.request.Request(asset["url"], headers={"Accept": "application/octet-stream", "Authorization": "Bearer " + TOKEN,
                                                        "User-Agent": "siedler-scoreboard"})
    with urllib.request.urlopen(req, timeout=120) as r:
        doc = json.loads(r.read())
    kinds = doc.get("kinds") or {}
    named = {k: v for k, v in kinds.items() if not k.startswith("task type ")}
    scen = {n: {"name": n, "turns": v.get("turns"), "calls": v.get("entries"), "samples": 0, "failing": 0, "kinds_failing": 0}
            for n, v in (doc.get("scenarios") or {}).items()}
    for v in kinds.values():  # per scenario: sampled ExecuteTask calls compared / differing (counts only)
        for n, x in (v.get("scenarios") or {}).items():
            sc = scen.setdefault(n, {"name": n, "turns": None, "calls": None, "samples": 0, "failing": 0, "kinds_failing": 0})
            sc["samples"] += x.get("samples", 0)
            sc["failing"] += x.get("failing", 0)
            sc["kinds_failing"] += bool(x.get("failing"))
    sm = doc.get("summary") or {}
    samples, failing = sm.get("samples", 0), sm.get("failing", 0)
    return {"sha": (doc.get("sha") or "")[:12], "updated": asset.get("updated_at"), "kinds": len(named),
            "failing": sum(1 for v in named.values() if v.get("failing")), "unnamed": len(kinds) - len(named),
            "partial": bool(doc.get("partial")),
            "calls": {"samples": samples, "identical": samples - failing,
                      "pct": round(100 * (samples - failing) / samples, 1) if samples else None},
            "scenarios": sorted(scen.values(), key=lambda x: (-x["failing"], x["name"]))}, named


def xc_pipeline(main_runs):
    now = NOW.timestamp()
    pools, jobs = [], []
    for repo, account, var, default in XC_POOLS:
        runs = main_runs if repo == PUB else safe(lambda repo=repo: active_runs(repo), None, f"xc pool {repo}")
        try:
            limit = int(os.environ.get(var) or default)
        except ValueError:
            limit = default
        p = {"account": account, "repo": repo, "limit": limit, "running": 0, "waiting": 0, "readable": runs is not None,
             "runs_url": f"https://github.com/{repo}/actions"}
        for r in (runs or {}).values():
            if (r.get("path") or "").rsplit("/", 1)[-1] not in XC_RUNS:
                continue
            for j in api(f"repos/{repo}/actions/runs/{r['id']}/jobs?per_page=100").get("jobs", []):
                m = re.search(r"\bfix \((.+)\)$", j.get("name") or "")
                if not m or j["status"] == "completed":
                    continue
                run_ = j["status"] == "in_progress"
                p["running" if run_ else "waiting"] += 1
                st = parse_ts(j.get("started_at") or r["created_at"])
                jobs.append({"kind": m.group(1), "account": account, "status": j["status"], "url": j.get("html_url") or r["html_url"],
                             "started": iso(st), "minutes": max(0, int((NOW - st).total_seconds() // 60)) if run_ else None})
        pools.append(p)
    issues = []
    for page in range(1, 6):
        got = api(f"repos/{PUB}/issues?state=open&labels=xc-kind&per_page=100&page={page}")
        issues += got
        if len(got) < 100:
            break
    try:
        wl, named = xc_worklist()
    except Exception as e:  # a missing grant must not read as a rate limit (403 skips the publish)
        wl, named = None, {}
        ERRORS.append(f"xc worklist: {type(e).__name__}")
    sha = (wl or {}).get("sha")
    track = {i["title"].removeprefix("[xc] "): xc_fields(i.get("body")) for i in issues}
    claimed = merged = exhausted = 0
    for f in track.values():
        c = f.get("claimed", "").split()
        claimed += bool(c) and now - int(c[0]) < XC_CLAIM_H * 3600
        merged += "merged" in f
        exhausted += bool(sha) and f.get("census") == sha and int(f.get("attempts", "0")) >= XC_ATTEMPTS
    open_ = None
    if wl:  # failing kinds a worker may still pick (pick(): not merged since this census, not exhausted)
        open_ = sum(1 for k, v in named.items() if v.get("failing")
                    and track.get(k, {}).get("merged") != sha
                    and not (track.get(k, {}).get("census") == sha and int(track.get(k, {}).get("attempts", "0")) >= XC_ATTEMPTS))
    census = None
    cr = (api(f"repos/{PUB}/actions/workflows/xc-census.yml/runs?per_page=5").get("workflow_runs") or [])
    if cr:
        done = next((r for r in cr if r["status"] == "completed" and r["conclusion"] != "cancelled"), None)  # superseded
        census = {"running": any(r["status"] != "completed" for r in cr),
                  "last": utc(parse_ts(done["updated_at"])) if done else None,
                  "conclusion": done and done["conclusion"], "url": (done or cr[0])["html_url"]}
    jobs.sort(key=lambda j: (j["status"] != "in_progress", j["account"] != "Kofi55533", j["started"]))
    return {"pools": pools, "jobs": jobs, "worklist": wl, "census": census,
            "kinds": {"tracked": len(track), "in_work": claimed, "merged": merged, "exhausted": exhausted, "open": open_},
            "running": sum(p["running"] for p in pools), "waiting": sum(p["waiting"] for p in pools),
            "limit": sum(p["limit"] for p in pools)}


def values_as_of(rows, matrix_source):
    """Real last update of the identical-turns value: the newest per-scenario measurement (gate merge / board entry)."""
    newest = max((r for r in rows if r.get("measured") and r.get("when")), key=lambda r: r["when"], default=None)
    if newest:
        return {"as_of": newest["when"], "as_of_source": newest["source"], "as_of_scenario": newest["name"]}
    return {"as_of": (matrix_source or {}).get("time"), "as_of_source": "Opus Live-Matrix" if matrix_source else None,
            "as_of_scenario": None}


# ---------------- overview (what the page leads with) ----------------
# scripts/autonomy_state.py is the controller's own bookkeeping (scope classes, stall rule); the workflow checks it out next
# to scripts/scoreboard so the page shows exactly what the controller counts.
sys.path.insert(0, os.path.dirname(HERE))
try:
    import autonomy_state as AS
except Exception:  # an old checkout without it: the overview falls back to the raw ticket counts
    AS = None

REPAIR_CAUSES = [  # (pattern in the gate/review error, label, what fixes it)
    (r"no fixture executes|differential test|Differential-tests", "Differential-Test fehlt",
     "Worker muss pro zitierter Adresse einen s5xcheck-Test mit Referenzaufruf liefern."),
    (r"live before/after", "Live-Zahlen fehlen", "Frontier-PR braucht Zeilen 'live <fixture>: before N -> M'."),
    (r"fixture declaration|could not be installed", "Fixture-Angabe ungültig", "Frontier-fixture-Zeile korrigieren."),
    (r"claimed area|lane claim", "Gesperrter Bereich", "Datei ist durch einen (alten) Board-Claim belegt."),
    (r"head differs", "PR seit Prüfung geändert", "Wird beim nächsten Durchlauf automatisch neu geprüft."),
    (r"review", "Nachweis-Review abgelehnt", "Inhaltlicher Mangel: Worker muss nachbessern."),
    (r"regress|diverg|worse", "Regression im Live-Test", "Änderung verschlechtert einen Szenario-Wert."),
    (r"receipt", "Prüfbeleg unvollständig", "Gate-Lauf abgebrochen; wird neu angestoßen."),
]
SCOPE_LABELS = {"A": ("Simulation", "wird jetzt nachgebaut: Spiellogik, Zug für Zug identisch"),
                "B": ("Darstellung", "später: Grafik, Sound, UI (eigener Vergleichstest nötig)"),
                "C": ("Bibliotheken", "nicht nachbauen: Rust-Standard/Crates ersetzen sie"),
                "D": ("Online/veraltet", "entfällt: alte Online-/Plattformdienste")}


def utc(ts):
    if ts is None:
        return None
    t = ts if isinstance(ts, datetime) else datetime.fromtimestamp(float(ts), timezone.utc)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


SQUASH = re.compile(r"\(#(\d+)\)$")


def integration_commits(days=14):
    """PR merges on clean/integration (2-parent commits), newest first, with the merged PR when it is known."""
    since = (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    by_head = {p["head_sha"]: (n, p) for n, p in PR_INFO.items() if p.get("head_sha")}
    out = []
    for page in range(1, 6):
        cs = api(f"repos/{PRIV}/commits?sha={urllib.parse.quote(BRANCH, safe='')}&since={since}&per_page=100&page={page}")
        for c in cs:
            squash = SQUASH.search(c["commit"]["message"].split("\n", 1)[0])  # xc workers squash-merge (one parent)
            if len(c.get("parents") or []) != 2 and not squash:
                continue
            if squash:
                n = int(squash.group(1))
                p = PR_INFO.get(n) or {"title": c["commit"]["message"].split("\n", 1)[0]}
            else:
                n, p = by_head.get(c["parents"][1]["sha"], (None, {}))
            out.append({"time": utc(parse_ts(c["commit"]["committer"]["date"])), "pr": n,
                        "title": (p.get("title") or "")[:140] or None,
                        "url": f"https://github.com/{PRIV}/pull/{n}" if n else None})
        if len(cs) < 100:
            break
    return out


def repair_cause(t):
    err = (t.get("receipt") or {}).get("error") or ""
    for pat, label, fix in REPAIR_CAUSES:
        if re.search(pat, err, re.I):
            return label, fix
    return "Sonstiges", "Einzelfall: Ticket ansehen."


def overview(data, merges):
    av = (fleet_state() or {}).get("autonomy_v2") or {}
    tickets = list((av.get("tickets") or {}).values())
    now = NOW.timestamp()
    v2 = data.get("fleet_v2") or {}
    dash = AS.dashboard(av) if AS and av.get("units") else None
    # goal: verified + integrated functions per scope class
    scope = []
    for k, (label, what) in SCOPE_LABELS.items():
        sp = ((dash or {}).get("scope") or {}).get(k) or {}
        scope.append({"key": k, "label": label, "what": what, "total": sp.get("obligations"),
                      "done": sp.get("integrated"), "now": sp.get("ported_now", k == "A")})
    # pipeline
    status = {}
    for t in tickets:
        status[t.get("status") or "?"] = status.get(t.get("status") or "?", 0) + 1
    integ = av.get("integration") or None
    queued = [t for t in tickets if t.get("status") == "queued"]
    oldest = min((float(t["created"]) for t in queued if t.get("created")), default=None)
    causes = {}
    for t in tickets:
        if t.get("status") == "repair":
            label, fix = repair_cause(t)
            c = causes.setdefault(label, {"label": label, "fix": fix, "count": 0})
            c["count"] += 1
    total = v2.get("total") or {}
    gate = next((g for g in v2.get("groups") or [] if g["key"] == "gate"), {})
    units = ((v2.get("domain") or {}).get("unit_status")) or {}
    # integrations per Berlin day (last 7) and the last 24 h vs. the 24 h before
    days = {}
    for d in range(6, -1, -1):
        days[(NOW.astimezone(BERLIN) - timedelta(days=d)).strftime("%Y-%m-%d")] = 0
    for m in merges:
        k = parse_ts(m["time"]).astimezone(BERLIN).strftime("%Y-%m-%d")
        if k in days:
            days[k] += 1
    age = lambda m: (NOW - parse_ts(m["time"])).total_seconds()
    last24 = sum(1 for m in merges if age(m) <= 86400)
    prev24 = sum(1 for m in merges if 86400 < age(m) <= 2 * 86400)
    head_t = data.get("head") and datetime.strptime(data["head"]["time"], "%Y-%m-%d %H:%M").replace(tzinfo=BERLIN)
    last_merge = merges[0]["time"] if merges else utc(head_t) if head_t else None
    pending = sum(status.get(s, 0) for s in ("queued", "verifying", "verified"))
    # attention list: (severity, title, detail, what to do)
    att = []
    def add(sev, title, detail, todo):
        att.append({"severity": sev, "title": title, "detail": detail, "todo": todo})
    xc = data.get("xc")
    old = not xc  # the domain-package fleet (tickets, gate, integration) retired 2026-10-23; its warnings only without xc
    hold = (v2.get("domain") or {}).get("provider_hold")
    if hold and old:
        add("critical", "DeepSeek pausiert (Provider-Sperre)", "Der Anbieter hat Anfragen abgelehnt; keine neuen Worker.",
            "Abwarten bis die Sperre abläuft; bei Dauer: Modell/Anbieter in autonomy_state.py prüfen.")
    if old and v2.get("enabled") is False:
        add("critical", "Flotte ausgeschaltet", "Repo-Variable FLEET_V2_ENABLED ist nicht 'true'.",
            "Variable in siedler-agent → Settings → Variables auf true setzen.")
    if xc:
        k = xc["kinds"]
        if k.get("open") and xc["running"] < min(xc["limit"], k["open"]) // 2:
            idle = [p for p in xc["pools"] if p["running"] < p["limit"]]
            add("warning", "Wenige xc-Worker aktiv", f"{xc['running']} von {xc['limit']} Plätzen arbeiten ({xc['waiting']} warten), "
                f"{k['open']} Aufgaben-Arten offen. " + ", ".join(f"{p['account']}: {p['running']}/{p['limit']}" for p in idle) + ".",
                "Nächste Welle startet automatisch (Watchdog alle 20 min, Pool alle 20 min); belegen andere Läufe die Konto-Plätze?")
        c = xc.get("census") or {}
        if c.get("conclusion") and c["conclusion"] != "success" and not c.get("running"):
            add("warning", "Letzte Messung (Census) fehlgeschlagen", "Die Arbeitsliste ist nicht auf dem neuesten Stand.",
                "xc-census-Lauf ansehen; der Watchdog startet ihn neu.")
        if not (xc.get("worklist") or {}).get("kinds"):
            add("info", "Arbeitsliste nicht lesbar", "Zahlen zu offenen Aufgaben-Arten fehlen.", "PRIVATE_REPO_TOKEN braucht Lesezugriff auf siedler-data.")
    if last_merge and old:
        idle_h = (NOW - parse_ts(last_merge)).total_seconds() / 3600
        if pending and idle_h >= 3:
            add("critical" if idle_h >= 12 else "warning", f"Seit {idle_h:.0f} h nichts Neues integriert",
                f"{pending} Tickets warten auf Prüfung/Integration.",
                "Integration läuft gerade – abwarten." if integ else "Gate-/Integrationsläufe in siedler-agent prüfen.")
    if old and integ and integ.get("at"):
        mins = (now - float(integ["at"])) / 60
        if mins >= 60:
            add("warning", f"Integration läuft seit {mins:.0f} min", f"Tickets {', '.join(integ.get('tickets') or [])}.",
                "Normal sind 10–40 min; länger: Lauf ansehen.")
    if old and status.get("queued") and not gate.get("used"):
        add("warning", "Gate leer, obwohl Tickets warten", f"{status['queued']} Tickets in der Warteschlange, kein Prüflauf aktiv.",
            "fleet-control-Läufe prüfen (rot?).")
    if old and total.get("worker_limit") and total.get("workers", 0) < total["worker_limit"] // 2 and not hold:
        add("warning", "Wenige Worker aktiv", f"{total.get('workers', 0)} von {total['worker_limit']} Worker-Plätzen belegt.",
            "Gibt es noch freie Einheiten? Sonst fleet-control prüfen.")
    rep, open_ = status.get("repair", 0), sum(v for k, v in status.items() if k != "accepted")
    if old and open_ and rep / open_ >= 0.3:
        top = max(causes.values(), key=lambda c: c["count"])
        add("warning", f"{rep} von {open_} offenen Tickets brauchen Nacharbeit",
            f"Häufigster Grund: {top['label']} ({top['count']}×).", top["fix"])
    asof = (data["percent"]["identical_turns"] or {}).get("as_of")
    last = ((xc or {}).get("census") or {}).get("last")
    if last:  # census cron every 6 h
        h = (NOW - parse_ts(last)).total_seconds() / 3600
        if h >= 8:
            add("warning", f"Letzte Messung {h:.0f} h alt", "Die Census-Messung läuft sonst alle 6 Stunden.",
                "xc-census in Actions prüfen.")
    if old and asof:
        h = (NOW - datetime.strptime(asof, "%Y-%m-%d %H:%M").replace(tzinfo=BERLIN)).total_seconds() / 3600
        if h >= 24:
            add("info", f"Szenario-Werte {h:.0f} h alt", "Die Zug-Werte ändern sich nur, wenn ein Gate-Merge neu misst.",
                "Kommt mit der nächsten Integration von selbst.")
    if data.get("errors"):
        add("info", f"{len(data['errors'])} Datenquelle(n) nicht lesbar", "; ".join(data["errors"][:2])[:200],
            "Nur Anzeige betroffen; build.py-Log ansehen.")
    order = {"critical": 0, "warning": 1, "info": 2}
    att.sort(key=lambda a: order[a["severity"]])
    health = "critical" if any(a["severity"] == "critical" for a in att) else (
        "warning" if any(a["severity"] == "warning" for a in att) else "good")
    base = [r for r in data["scenarios"] if not r["new"]]
    gen = [r for r in data["scenarios"] if r["new"]]
    return {
        "health": health, "attention": att,
        "goal": {"inventory": (dash or {}).get("inventory") or av.get("total"),
                 "done": (dash or {}).get("reported_reference_verified_and_integrated"), "scope": scope},
        "scenarios": {"base_total": len(base), "base_identical": sum(1 for r in base if r["identical"]),
                      "gen_total": len(gen), "gen_identical": sum(1 for r in gen if r["identical"]),
                      "gen_measured": sum(1 for r in gen if r["measured"])},
        "pipeline": {"tickets": status, "pending": pending, "oldest_queued": utc(oldest),
                     "integration": {"since": utc(integ.get("at")), "tickets": integ.get("tickets"),
                                     "run": f"https://github.com/{PUB}/actions/runs/{integ['run']}" if integ.get("run") else None}
                     if integ else None,
                     "units": units, "workers": total.get("workers"), "worker_limit": total.get("worker_limit"),
                     "gate": gate.get("used"), "gate_limit": gate.get("limit"),
                     "repair_causes": sorted(causes.values(), key=lambda c: -c["count"]), "xc": xc},
        "integrations": {"per_day": [{"day": k, "count": v} for k, v in days.items()], "last_24h": last24,
                         "prev_24h": prev24, "last": last_merge, "recent": merges[:12]},
    }


# ---------------- main ----------------
def rate_remaining(tok):
    try:
        req = urllib.request.Request("https://api.github.com/rate_limit", headers={"Authorization": "Bearer " + tok, "User-Agent": "siedler-scoreboard"})
        with urllib.request.urlopen(req, timeout=30) as r:  # /rate_limit itself does not count
            c = json.loads(r.read())["resources"]["core"]
        return c["remaining"], c["reset"]
    except Exception:
        return None, None


def skip_publish(reason):
    """No data.json: the publish step keeps the last good gh-pages data."""
    cache_save()
    open(os.path.join(OUT, "SKIP_PUBLISH"), "w").write(reason + "\n")
    print(f"SKIP_PUBLISH: {reason}; api={STATS}")
    sys.exit(0)


def main():
    os.makedirs(OUT, exist_ok=True)
    cache_load()
    if TOKEN:
        rem, rst = rate_remaining(TOKEN)
        if rem is not None and rem < int(os.environ.get("SB_MIN_REMAINING", "400")):
            skip_publish(f"PAT has only {rem} REST calls left (reset {datetime.fromtimestamp(rst, BERLIN):%H:%M} Berlin); build skipped")
    board = safe(lambda: content(PRIV, "docs/AGENT_BOARD.md", BRANCH), "", "AGENT_BOARD")
    if not board:
        skip_publish("AGENT_BOARD unreadable (" + "; ".join(ERRORS[:3]) + ")")
    entries = board_entries(board)
    for page in range(1, 15):  # PR states: after-values of unmerged PRs are not clean/integration values
        pl = safe(lambda: api(f"repos/{PRIV}/pulls?state=all&per_page=100&page={page}"), [], "pr states")
        for pr in pl:
            PR_STATE[pr["number"]] = "merged" if pr.get("merged_at") else pr["state"]
            PR_INFO[pr["number"]] = {"author": (pr.get("user") or {}).get("login"), "state": PR_STATE[pr["number"]],
                                     "head": pr["head"]["ref"], "head_sha": pr["head"]["sha"], "title": pr.get("title") or "",
                                     "merged": iso(parse_ts(pr["merged_at"])) if pr.get("merged_at") else None}
            if pr["state"] == "open" or not pr.get("merged_at"):
                PR_BY_BRANCH.setdefault(pr["head"]["ref"], pr["number"])
            else:
                PR_BY_BRANCH[pr["head"]["ref"]] = pr["number"] if pr["head"]["ref"] not in PR_BY_BRANCH else PR_BY_BRANCH[pr["head"]["ref"]]
        if len(pl) < 100:
            break
    global BRANCHES
    BRANCHES = []
    for page in range(1, 10):
        br = safe(lambda: api(f"repos/{PRIV}/branches?per_page=100&page={page}"), [], "branches")
        BRANCHES += [b["name"] for b in br]
        if len(br) < 100:
            break
    global GATE
    GATE = gate_values()
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
        tk = tot.get(name) or tot.get(ALIAS_TOTAL.get(name, ""))
        fixed = FIXED_TOTAL.get(name)  # (start, turns, commands) of a full recording not in TEST_VECTORS/index.json
        tturns = fixed[1] if fixed else (tk["turns"] if tk else None)
        bv = board_value(entries, name, floor=(v or {}).get("turn") or 0, total=tturns,
                         start=fixed[0] if fixed else (tk["start"] if tk else 0))
        if bv and (not live or int(bv["line"]) > int(live[0]["line"])):
            v, vsrc, vwhen = bv, f"Board ({bv['agent']}, Zeile {bv['line']})", bv["time"]
        gv = GATE.get(name)
        if gv and (not v or not vwhen or gv["time"] >= vwhen):  # gate merge matrix = authoritative post-merge value
            v, vsrc, vwhen = gv, f"Gate #{gv['pr']} (Merge {gv['time'][11:]})", gv["time"]
        ncmd = summ = None
        cp = f"fixtures/{fdir}/commands.jsonl" if fdir else None
        if cp and cp in ci_files:
            txt = safe(lambda: blob(PRIV, ci_files[cp]["sha"]), "", f"commands {name}")
            if txt:
                ncmd, types = count_commands(txt)
                summ = cmd_summary(types)
                CMD_SEEN.setdefault(name, set()).update(cmd_ids(types))
        start, turns = (tk["start"], tk["turns"]) if tk else (None, None)
        if fixed:  # full recording (bc-full: not the 2000-turn `bc` cut in fixtures/bc)
            start, turns, ncmd = fixed
        if v and not v["identical"] and turns and v["turn"] and v["turn"] >= turns:
            v = dict(v, identical=True)  # matched up to the scenario's last turn = whole run identical
        if v and v["identical"] and turns and v["turn"] and v["turn"] < turns:
            v = dict(v, identical=False)  # a turn before the scenario's end cannot be "whole run identical"
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
        ft = f.get("turns") or (int(re.search(r"-(\d{3,5})$", name).group(1)) if re.search(r"-(\d{3,5})$", name) else None)
        bv = board_value(entries, name, total=ft, start=f.get("start") or 0)
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
        gv = GATE.get(name)
        if gv and (not val or not when or gv["time"] >= when):
            val, src, when = gv, f"Gate #{gv['pr']} (Merge {gv['time'][11:]})", gv["time"]
        start, turns = f.get("start"), f.get("turns")
        if turns is None and val and val["identical"] and val["turn"]:
            turns = val["turn"]
        m5 = re.search(r"-(\d{3,5})$", name)
        if turns is None and m5:
            turns = int(m5.group(1))
        if val and val["identical"] and turns and val["turn"] and val["turn"] < turns:
            val = dict(val, identical=False)  # turn before the scenario's end -> not whole-run identical
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
    # The value changes with every gate merge / newer board measurement, not only with the Opus "Live @" matrix
    # (matrix_source.time is that board entry, 2026-10-17 18:50): as_of = newest per-scenario measurement time.
    data["percent"] = {
        "identical_turns": dict({"pct": round(100 * num / den, 1) if den else None, "ok": num, "total": den, "scenarios": used},
                                **values_as_of(rows, data["matrix_source"])),
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
        ds.append({"title": r.get("display_title", "")[:160], "run_url": r["html_url"], "source": "opencode.yml",
                   "pool": PUB, "account": "Kofi55533",
                   "issue": iss["number"] if iss else None, "issue_url": iss["html_url"] if iss else None,
                   "kind": kind(r.get("display_title", "")), "started": iso(st), "minutes": int((NOW - st).total_seconds() // 60)})
    pulls = safe(lambda: api(f"repos/{PRIV}/pulls?state=closed&base={urllib.parse.quote(BRANCH, safe='')}&sort=updated&direction=desc&per_page=100"), [], "pulls")
    merged = []
    for p in pulls:
        if p.get("merged_at") and NOW - parse_ts(p["merged_at"]) <= timedelta(hours=24):
            merged.append({"number": p["number"], "title": p["title"][:160], "merged": iso(parse_ts(p["merged_at"])),
                           "author": p["user"]["login"]})
    merged.sort(key=lambda p: p["merged"], reverse=True)
    data["fleet"] = fleet_pools()  # legacy shared fleet (fleet/state.json tasks); card hidden on the page
    data["fleet_v2"] = v2 = safe(fleet_v2, None, "fleet v2 runs")
    data["xc"] = safe(lambda: xc_pipeline(active_runs(PUB)), None, "xc pipeline")
    # Fleet v2: DeepSeek workers are fleet-worker.yml runs in siedler-agent (each plans its own domain package; there is
    # no separate planner). Still-running legacy opencode.yml runs are listed as "Legacy" until they finish.
    fleet_ds = []
    for r in (v2 or {}).get("runs", []):
        if r["group"] == "worker":
            fleet_ds.append({"title": (f"{r['unit']}: {r['unit_title']}" if r.get("unit_title") else r["title"]),
                             "run_url": r["url"], "issue": None, "issue_url": None, "kind": "Domäne",
                             "task_kind": r.get("domain"), "started": r["started"], "minutes": r["minutes"],
                             "source": "fleet-worker-v2", "pool": r.get("repo") or PUB, "account": r.get("account") or "Kofi55533",
                             "status": r["status"]})
    fleet_queued = sum(1 for x in fleet_ds if x["status"] != "in_progress")
    fleet_ds = [x for x in fleet_ds if x["status"] == "in_progress"]
    for x in ds:
        x["kind"] = "Legacy " + x["kind"]
    ds = sorted(ds + fleet_ds, key=lambda x: x["started"])
    kinds = {}
    for x in ds:
        kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
    wg = next((g for g in (v2 or {}).get("groups", []) if g["key"] == "worker"), None)
    by_acct = {}
    for x in fleet_ds:
        by_acct[x["account"]] = by_acct.get(x["account"], 0) + 1
    data["deepseek"] = {"running": ds, "kinds": kinds, "count": len(ds), "queued": len(queued) + fleet_queued,
                        "target": ((v2 or {}).get("total") or {}).get("worker_limit") or (wg or {}).get("limit") or 16,
                        "target_source": "FLEET_V2_WORKER_LIMIT (Kofi55533) + FLEET_POOL_WORKER_LIMIT (Dominickofi00)",
                        "fleet_count": len(fleet_ds), "fleet_by_account": by_acct,
                        "legacy_count": len(ds) - len(fleet_ds), "merged_24h": merged}
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
    merges = safe(integration_commits, [], "integration commits")
    data["overview"] = ov = safe(lambda: overview(data, merges), None, "overview")
    # any rate-limited / forbidden fetch makes the page partial (12:49: 198 errors, 35 of 86 scenarios) -> keep the last good page
    bad = [e for e in ERRORS if re.search(r"\b(403|429)\b|RateLimited", e)]
    if RATE["limited"] or bad:
        skip_publish(f"{RATE['what'] or 'HTTP 403/429'}: {len(bad)} of {len(ERRORS)} fetches failed ({'; '.join(bad[:3])})")

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
    goal = (ov or {}).get("goal") or {}
    p7 = goal.get("done")
    p8 = next((c["done"] for c in goal.get("scope") or [] if c["key"] == "A"), None)
    xwl = (data.get("xc") or {}).get("worklist") or {}
    p9, p10 = (xwl.get("calls") or {}).get("pct"), xwl.get("sha")
    last = hist[-1] if hist else {}
    if (not hist or last.get("key") != key or last.get("pct_identical") != p1 or last.get("pct_coverage") != p2
            or last.get("pct_whole_logic") != p3 or last.get("maps_tested") != p5 or last.get("commands_tested") != p6
            or (p7 is not None and (last.get("integrated") != p7 or last.get("integrated_a") != p8))
            or (p10 and (last.get("xc_sha") != p10 or last.get("xc_pct") != p9))):
        hist.append({"key": key, "time": data["percent"]["identical_turns"].get("as_of"),
                     "matrix_time": data["matrix_source"]["time"] if data["matrix_source"] else None,
                     "sha": data["matrix_source"]["sha"] if data["matrix_source"] else None,
                     "recorded": data["generated_berlin"], "pct_identical": p1, "pct_coverage": p2,
                     "pct_whole_logic": p3, "pct_whole_raw": p4, "maps_tested": p5, "commands_tested": p6,
                     "integrated": p7, "integrated_a": p8, "xc_pct": p9, "xc_sha": p10,
                     "xc_scenarios": {x["name"]: [x["samples"], x["failing"]] for x in xwl.get("scenarios") or []},
                     "values": {r["name"]: {"turn": r["turn"], "identical": r["identical"]} for r in rows if r["measured"]}})
    data["history"] = hist[-200:]
    json.dump(hist[-200:], open(os.path.join(OUT, "history.json"), "w"), ensure_ascii=False, indent=1)
    json.dump(data, open(os.path.join(OUT, "data.json"), "w"), ensure_ascii=False, indent=1)
    cache_save()
    print(f"api requests={STATS['requests']} (PAT {STATS['pat']}, GITHUB_TOKEN {STATS['pub']}; 304 not modified PAT {STATS['not_modified_pat']} "
          f"/ GITHUB_TOKEN {STATS['not_modified_pub']}, immutable cache hits {STATS['immutable_hits']}; counted: PAT "
          f"{STATS['pat'] - STATS['not_modified_pat']}, GITHUB_TOKEN {STATS['pub'] - STATS['not_modified_pub']})")
    print(f"scenarios={len(rows)} live={'yes' if live else 'no'} ds_running={len(ds)} merged24h={len(merged)} slots={[(g['key'], g['used'], g['limit']) for g in (data['fleet_v2'] or {}).get('groups', [])]} main={(data['fleet_v2'] or {}).get('used')}/{V2_CAPACITY} accounts={[(a['account'], a['used'], a['capacity']) for a in (data['fleet_v2'] or {}).get('accounts', [])]} total={((data['fleet_v2'] or {}).get('total') or {}).get('used')} errors={len(ERRORS)}")


if __name__ == "__main__":
    main()
