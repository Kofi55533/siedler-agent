#!/usr/bin/env python3
"""Scoreboard (GitHub Pages): how far the clean engine plays turn-identical with the original game.

One question only: in how many scenarios and on how many maps does s5clean play exactly like the original, at every
checked level (state, tasks, random draws, deep world fields, Lua, spatial). Everything comes from measurements:

- measurements: private releases rm-*/measure-<scenario>.txt in Kofi55533/siedler-data (written by the full measure
  workflow, one file per scenario: line 1 "fixture <name> on <branch> @<sha>", line 2 the verdict). Parsed files are
  cached in OUT/messungen.json (previous copy from gh-pages via PREV_DIR), so a build downloads only new or changed
  files. Published are only status words, turn numbers and level names; never field names, entity ids or game state.
- running measurements: in-progress workflow runs of Kofi55533/siedler-agent whose name contains "measure".
- recent merges: the last commits on clean/integration of Kofi55533/siedler-sim-rs.
- maps: scripts/scoreboard/map_list.json (the 94 official maps); a scenario belongs to a map by its name.

env: GH_TOKEN (read access to the private repos), OUT (output dir), PREV_DIR (optional: previous gh-pages files),
     SB_GH_CLI=1 (local runs: calls go through the gh CLI).
Writes OUT/data.json, OUT/messungen.json, OUT/verlauf.json. On an API failure nothing is written and OUT/SKIP_PUBLISH
holds the reason, so the last good page stays.
"""
import json, os, re, subprocess, sys, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

PRIV = "Kofi55533/siedler-sim-rs"
PUB = "Kofi55533/siedler-agent"
DATA = "Kofi55533/siedler-data"
BRANCH = "clean/integration"
BERLIN = ZoneInfo("Europe/Berlin")
HERE = os.path.dirname(os.path.abspath(__file__))
KEEP_RELEASES = 6  # newest measurement releases kept in the cache

# ---------------- GitHub access ----------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def api(path, raw=False):
    if os.environ.get("SB_GH_CLI") == "1":
        args = ["gh", "api", path] + (["-H", "Accept: application/octet-stream"] if raw else [])
        r = subprocess.run(args, capture_output=True, timeout=120)
        if r.returncode:
            raise RuntimeError(f"gh api {path}: {r.stderr.decode(errors='replace')[:200]}")
        return r.stdout.decode("utf-8", "replace") if raw else json.loads(r.stdout or b"null")
    req = urllib.request.Request(f"https://api.github.com/{path}", headers={
        "Authorization": f"Bearer {os.environ.get('GH_TOKEN', '')}", "X-GitHub-Api-Version": "2022-11-28",
        "Accept": "application/octet-stream" if raw else "application/vnd.github+json"})
    if raw:  # a release asset answers with a redirect to signed storage, which must not get the token
        try:
            with urllib.request.build_opener(_NoRedirect).open(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308):
                raise
            with urllib.request.urlopen(e.headers["Location"], timeout=60) as r:
                return r.read().decode("utf-8", "replace")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


def berlin(ts):
    if not ts:
        return None
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if isinstance(ts, str) else ts
    return dt.astimezone(BERLIN).strftime("%d.%m. %H:%M")


# ---------------- measurement files ----------------

LEVELS = {"snapshot": "Zustand", "tasks": "Tasks", "rng": "Zufall", "world": "Welt", "lua": "Lua",
          "spatial": "Räumlich"}


def parse_measure(text):
    """measure-<name>.txt -> (name, sha, public verdict). Turns are absolute game turns (the scenario's start turn
    included); "ticks" is the scenario length."""
    lines = text.splitlines()
    m = re.match(r"fixture (\S+) on \S+ @([0-9a-f]+)", lines[0] if lines else "")
    name, sha = (m.group(1), m.group(2)) if m else (None, None)
    v = lines[1].strip() if len(lines) > 1 else ""
    if re.match(r"^[A-Za-z0-9_.-]+: ", v):
        v = v.split(": ", 1)[1]
    r = {"status": "fehler", "ticks": None, "turn": None, "level": None, "levels": {}}
    if v.startswith("IDENTICAL"):
        t = re.match(r"IDENTICAL (\d+)", v)
        r.update(status="gleich", ticks=int(t.group(1)) if t else None)
    elif v.startswith("DEEP-DIVERGENT"):
        t = re.match(r"DEEP-DIVERGENT (\d+)", v)
        lv = {}
        for k, n in re.findall(r"\b(world|lua|spatial)@(\d+)", v):
            lv.setdefault(LEVELS[k], int(n))
        first = min(lv.items(), key=lambda kv: kv[1]) if lv else (None, None)
        r.update(status="tief", ticks=int(t.group(1)) if t else None, level=first[0], turn=first[1], levels=lv)
    elif v.startswith("DIVERGENT"):
        t = re.match(r"DIVERGENT turn (\d+):?\s*([a-z_]+)?", v)
        if t:
            lvl = LEVELS.get(t.group(2) or "", "Zustand")
            r.update(status="abweichend", turn=int(t.group(1)), level=lvl, levels={lvl: int(t.group(1))})
    elif v in ("", "no log") or "reference image starts" in v:
        r["status"] = "ungemessen"
    return name, sha, r


def measurements(prev):
    """{tag: {"sha", "done" (newest file time), "files" (cache), "results": {scenario: verdict}}}, newest releases."""
    rels = []
    for page in (1, 2, 3):
        part = api(f"repos/{DATA}/releases?per_page=100&page={page}") or []
        rels += part
        if len(part) < 100:
            break
    old = prev.get("releases", {}) if isinstance(prev, dict) else {}
    cands = []
    for rel in rels:
        files = [a for a in rel.get("assets", []) if re.fullmatch(r"measure-.+\.txt", a["name"])]
        if rel["tag_name"].startswith("rm-") and files:
            cands.append((max(a["updated_at"] for a in files), rel, files))
    out = {}
    for done, rel, files in sorted(cands, key=lambda c: c[0], reverse=True)[:KEEP_RELEASES]:
        tag = rel["tag_name"]
        cache = old.get(tag, {}).get("files", {})
        e = {"sha": None, "done": done, "files": {}, "results": {}}
        for a in sorted(files, key=lambda a: a["name"]):
            key = f"{a['id']}:{a['updated_at']}"
            c = cache.get(a["name"])
            if not c or c.get("key") != key:
                name, sha, r = parse_measure(api(f"repos/{DATA}/releases/assets/{a['id']}", raw=True))
                c = {"key": key, "name": a["name"][len("measure-"):-len(".txt")], "sha": sha, "r": r}
            e["files"][a["name"]] = c
            e["results"][c["name"]] = c["r"]
            e["sha"] = e["sha"] or c.get("sha")
        out[tag] = e
    return out


# ---------------- scenarios, maps ----------------

RANK = {"gleich": 3, "tief": 2, "abweichend": 1}  # fehler / ungemessen: 0
GROUPS = ["Kampagne", "Kartenstart", "Kampf", "KI", "Echte Spiele", "Wirtschaft & Befehle"]


def norm_map(s):
    s = re.sub(r"^\(\d+\)", "", s)
    s = re.sub(r"^\d\d_", "", s)
    return re.sub(r"[^a-z0-9]", "", s.lower())


MAP_ALIAS = {"campcrawford": "crawford"}
OFFICIAL = json.load(open(os.path.join(HERE, "map_list.json")))["official"]
OFFICIAL_KEYS = {norm_map(m["name"]) for m in OFFICIAL}


def scenario_map(name):
    b = re.sub(r"^(map-|campaign-|genc-|genb-)", "", name)
    b = re.sub(r"^\d\d-", "", b)
    b = re.sub(r"(-mapstart|-long|-full|-\d+)+$", "", b)
    b = re.sub(r"[^a-z0-9]", "", b)
    return MAP_ALIAS.get(b, b)


def group(name):
    if name.startswith("bc-"):
        return "Echte Spiele"
    if name.startswith("campaign-") or re.match(r"^\d\d-", name):
        return "Kampagne"
    if name.startswith("map-") or name.endswith("-mapstart"):
        return "Kartenstart"
    if re.search(r"combat|wave|leader|attack|siege|recruit|cannon|formation|barrack|sabotage|military|soldier", name):
        return "Kampf"
    if re.search(r"(^|-)ai|camp-?crawford|oldkings", name):
        return "KI"
    if not name.startswith("gen") and scenario_map(name) in OFFICIAL_KEYS:
        return "Kartenstart"
    return "Wirtschaft & Befehle"


def score(r):
    """Higher is better: status first, then how long it stays identical."""
    return (RANK.get(r["status"], 0), r.get("turn") or 0)


def latest(rels):
    """Per scenario its newest real verdict over all releases (a "no log" in a newer run does not hide an older
    verdict), plus the verdict before it for the trend: {name: (tag, verdict, previous verdict or None)}."""
    order = sorted(rels, key=lambda t: rels[t]["done"], reverse=True)
    out = {}
    for name in sorted({n for t in order for n in rels[t]["results"]}):
        hits = [(t, rels[t]["results"][name]) for t in order if name in rels[t]["results"]]
        real = [h for h in hits if RANK.get(h[1]["status"], 0)]
        if real:
            out[name] = (real[0][0], real[0][1], real[1][1] if len(real) > 1 else None)
        else:
            out[name] = (hits[0][0], hits[0][1], None)
    return out


def scenario_rows(cur, meta, rels=None):
    """cur: {name: (tag, verdict, previous verdict)} -> table rows."""
    rows = []
    for name, (tag, r, p) in sorted(cur.items()):
        m = meta.get(name, {})
        total = r.get("ticks") or m.get("turns")
        start = m.get("start") or 0
        same = state = None
        if total and RANK.get(r["status"], 0):
            div = max(0, min(total, (r.get("turn") or start) - start))
            same = total if r["status"] == "gleich" else div
            state = total if r["status"] in ("gleich", "tief") else div
        delta = None
        if p and RANK.get(r["status"], 0) and score(r) != score(p):
            delta = "besser" if score(r) > score(p) else "schlechter"
        rel = (rels or {}).get(tag, {})
        rows.append({"name": name, "group": group(name), "status": r["status"], "turn": r.get("turn"),
                     "level": r.get("level"), "levels": r.get("levels") or {}, "total": total, "start": start,
                     "same": same, "state_same": state, "delta": delta,
                     "prev": {"status": p["status"], "turn": p.get("turn")} if delta else None,
                     "sha": rel.get("sha"), "when": berlin(rel.get("done"))})
    return rows


def summary(rows):
    c = {"gleich": 0, "tief": 0, "abweichend": 0, "ungemessen": 0}
    for r in rows:
        c[r["status"] if r["status"] in RANK else "ungemessen"] += 1
    meas = [r for r in rows if RANK.get(r["status"], 0) and r["total"]]
    tot = sum(r["total"] for r in meas)
    same, state = sum(r["same"] or 0 for r in meas), sum(r["state_same"] or 0 for r in meas)
    lv, first = {}, {}
    for r in rows:
        for k in r["levels"]:
            lv[k] = lv.get(k, 0) + 1
        if r["level"] and r["status"] in ("tief", "abweichend"):
            first[r["level"]] = first.get(r["level"], 0) + 1
    return {"total": len(rows), **c, "measured": len(rows) - c["ungemessen"], "turns_total": tot,
            "turns_same": same, "turns_pct": round(100 * same / tot, 1) if tot else None,
            "turns_state_same": state, "turns_state_pct": round(100 * state / tot, 1) if tot else None,
            "besser": sum(1 for r in rows if r["delta"] == "besser"),
            "schlechter": sum(1 for r in rows if r["delta"] == "schlechter"),
            "levels": dict(sorted(lv.items(), key=lambda kv: -kv[1])),
            "first_levels": dict(sorted(first.items(), key=lambda kv: -kv[1])),
            "groups": {g: {k: sum(1 for r in rows if r["group"] == g and (r["status"] == k or (
                k == "ungemessen" and r["status"] not in RANK))) for k in c} for g in GROUPS}}


def map_rows(rows):
    by = {}
    for r in rows:
        by.setdefault(scenario_map(r["name"]), []).append(r)
    out = []
    for m in OFFICIAL:
        rs = by.get(norm_map(m["name"]), [])
        measured = [r for r in rs if RANK.get(r["status"], 0)]
        st = "kein Szenario" if not rs else "ungemessen" if not measured else min(measured, key=score)["status"]
        out.append({"name": re.sub(r"^\(\d+\)\s*", "", m["name"]).replace("_", " "), "kind": m["kind"],
                    "status": st, "scenarios": sorted(r["name"] for r in rs)})
    return out


# ---------------- other sources ----------------


def running():
    out, seen = [], set()
    for st in ("in_progress", "queued"):
        for run in (api(f"repos/{PUB}/actions/runs?status={st}&per_page=50") or {}).get("workflow_runs", []):
            if "measure" not in (run.get("name") or "").lower() or run["id"] in seen:
                continue
            seen.add(run["id"])
            jobs = (api(f"repos/{PUB}/actions/runs/{run['id']}/jobs?per_page=100") or {}).get("jobs", [])
            if len(jobs) < 3:  # only the gate or the build step yet: nothing is being measured
                continue
            out.append({"name": run["name"], "started": berlin(run.get("run_started_at") or run["created_at"]),
                        "jobs": len(jobs), "jobs_done": sum(1 for j in jobs if j.get("status") == "completed"),
                        "url": run["html_url"]})
    return out


def merges(n=12):
    cs = api(f"repos/{PRIV}/commits?sha={urllib.parse.quote(BRANCH, safe='')}&per_page={n}") or []
    return [{"sha": c["sha"][:9], "time": berlin(c["commit"]["committer"]["date"]),
             "title": c["commit"]["message"].split("\n", 1)[0][:160]} for c in cs]


def behind(sha):
    """(commits on clean/integration since the measured commit, time of the measured commit)."""
    try:
        c = api(f"repos/{PRIV}/compare/{sha}...{urllib.parse.quote(BRANCH, safe='')}")
        return c.get("ahead_by"), berlin(c["base_commit"]["commit"]["committer"]["date"])
    except Exception:
        return None, None


# ---------------- main ----------------


def build(rels, meta, hist, run=(), mg=(), ahead=(None, None), now=None):
    """Pure part: measurements -> data.json dict (+ updated history). Testable without network."""
    now = now or datetime.now(timezone.utc)
    order = sorted(rels, key=lambda t: rels[t]["done"], reverse=True)
    rows = scenario_rows(latest(rels), meta, rels)
    summ = summary(rows)
    used = {}
    for r in rows:
        if r["sha"]:
            used[r["sha"]] = used.get(r["sha"], 0) + 1
    newest = rels[order[0]]
    maps = map_rows(rows)
    mcount = {k: sum(1 for m in maps if m["status"] == k) for k in ("gleich", "tief", "abweichend", "ungemessen",
                                                                       "kein Szenario")}
    # history: one point per measurement release (the state as of that release), the newest one updated while
    # later files of it come in; older releases still in the cache fill in missing points
    hist = [h for h in hist if isinstance(h, dict) and h.get("tag") and h["tag"] != order[0]]
    known = {h["tag"] for h in hist}
    for i, t in enumerate(order):
        if t in known:
            continue
        upto = {u: rels[u] for u in order[i:]}
        r_ = rows if i == 0 else scenario_rows(latest(upto), meta, upto)
        s_ = summ if i == 0 else summary(r_)
        if not s_["measured"]:  # a release without a single verdict (broken run) is no point of the curve
            continue
        mg_ = sum(1 for m in (maps if i == 0 else map_rows(r_)) if m["status"] == "gleich")
        hist.append({"tag": t, "sha": rels[t]["sha"], "time": berlin(rels[t]["done"]), "done": rels[t]["done"],
                     **{k: s_[k] for k in ("total", "gleich", "tief", "abweichend", "ungemessen", "turns_pct",
                                           "turns_state_pct")}, "maps_gleich": mg_})
    hist.sort(key=lambda h: h.get("done") or "")
    data = {
        "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_berlin": now.astimezone(BERLIN).strftime("%d.%m.%Y %H:%M"),
        "measurement": {"tag": order[0], "sha": newest["sha"], "finished": berlin(newest["done"]),
                        "commits_since": ahead[0], "commit_time": ahead[1],
                        "runs": [{"sha": rels[t]["sha"], "when": berlin(rels[t]["done"]),
                                  "files": len(rels[t]["results"]), "used": used.get(rels[t]["sha"], 0)}
                                 for t in order]},
        "summary": summ,
        "maps": {"total": len(maps), **mcount, "list": maps},
        "running": list(run),
        "merges": list(mg),
        "history": hist[-40:],
        # also read by scripts/planner_state.py, known_done.py, s5-remeasure.sh: name, turn, total, identical
        "scenarios": [{**r, "identical": r["status"] == "gleich", "measured": bool(RANK.get(r["status"], 0)),
                       "turn": r["total"] if r["status"] == "gleich" else r["turn"],
                       "source": f"measure @{r['sha']}" if r["sha"] else None} for r in rows],
        "percent": {"test_scope": {"maps": {
            "tested": len(maps) - mcount["kein Szenario"] - mcount["ungemessen"], "total": len(maps),
            "untested": [f"{m['kind']}: {m['name']}" for m in maps if m["status"] in ("kein Szenario", "ungemessen")]}}},
    }
    return data, hist


def load_prev(prev_dir, name, default):
    try:
        return json.load(open(os.path.join(prev_dir, name))) if prev_dir else default
    except Exception:
        return default


def main():
    out, prev_dir = os.environ.get("OUT", "out"), os.environ.get("PREV_DIR", "")
    os.makedirs(out, exist_ok=True)
    meta = json.load(open(os.path.join(HERE, "scenarios.json")))["scenarios"]
    try:
        rels = measurements(load_prev(prev_dir, "messungen.json", {}))
        if not rels:
            raise RuntimeError("no measurement release found")
        run, mg = running(), merges()
    except Exception as e:  # rate limit, network: keep the last good page
        open(os.path.join(out, "SKIP_PUBLISH"), "w").write(f"{type(e).__name__}: {e}"[:300])
        print("skip:", e, file=sys.stderr)
        return
    main_sha = rels[max(rels, key=lambda t: rels[t]["done"])]["sha"]
    data, hist = build(rels, meta, load_prev(prev_dir, "verlauf.json", []), run, mg,
                       behind(main_sha) if main_sha else (None, None))
    json.dump(data, open(os.path.join(out, "data.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump({"releases": {t: {"files": rels[t]["files"]} for t in rels}},
              open(os.path.join(out, "messungen.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump(hist, open(os.path.join(out, "verlauf.json"), "w"), ensure_ascii=False, indent=0)
    s, m = data["summary"], data["maps"]
    print(f"{data['measurement']['tag']}: {s['gleich']}/{s['total']} gleich, {s['tief']} tief, {s['abweichend']} "
          f"abweichend, {s['ungemessen']} ungemessen, {s['turns_pct']}/{s['turns_state_pct']} % Züge; Karten gleich {m['gleich']}/{m['total']}"
          f"; laufend {len(data['running'])}; besser {s['besser']}, schlechter {s['schlechter']}")


if __name__ == "__main__":
    main()
