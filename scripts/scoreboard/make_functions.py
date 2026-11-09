#!/usr/bin/env python3
"""Function overview snapshot for the scoreboard (writes scripts/scoreboard/functions.json).

Runs on the box (needs box-only inputs: union3 coverage set, classification, fuzz results). Publishes counts only,
plus a few original addresses (failing xc functions, top uncited). build.py merges the file into data.json as
data["functions"]; index.html shows it as "Funktionsüberblick" and marks values older than 24 h as "veraltet".

Refresh (box):
  python3 scripts/scoreboard/make_functions.py && git add scripts/scoreboard/functions.json && git commit -m "scoreboard: functions snapshot" && git push
What it does:
  a) recount: HEX6 citations in crates/s5clean/src at the CURRENT clean/integration tip (git fetch in SIM_REPO), mapped to
     function_inventory.json bodies (same rule as build.py function_coverage), against the 2172 Simulation / 2944 logic
     functions executed in the scoreboard scenarios (union3.logic_hit, area from the classification).
  b) unmodelled(...): calls in s5clean/src; which of the 2172 are named in an unmodelled("...") literal.
  c) xc call samples per function: current siedler-data release xc-worklist (FN_<addr> kinds), via gh.
  d) discovery: deduplicated union of all logic functions found by the box/GitHub function search that are NOT in union3.
"""
import bisect, glob, gzip, json, os, re, subprocess, sys, tempfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
S = "/workspace/siedler"
SIM_REPO = os.environ.get("SIM_REPO", f"{S}/codex-box/wt/cover-mode")  # partial clone of Kofi55533/siedler-sim-rs
UNION3 = f"{S}/coverage-20261108/union3.json"
CLASSIFY = f"{S}/classify-20261109/multimodule/out3/functions-unified-v3.jsonl"
FUZZ = f"{S}/fuzz-20261109"
MAPSTART = "/tmp/unseen-break/prev33_new.json"
BERLIN = ZoneInfo("Europe/Berlin")
HEX6 = re.compile(r"0x([0-9A-Fa-f]{6})\b")


def now():
    return datetime.now(BERLIN).strftime("%Y-%m-%d %H:%M")


def mtime(p):
    return datetime.fromtimestamp(os.path.getmtime(p), BERLIN).strftime("%Y-%m-%d %H:%M")


def git(*a, inp=None):
    return subprocess.run(["git", "-C", SIM_REPO, *a], input=inp, capture_output=True, check=True).stdout


def inventory():
    inv = json.load(open(os.path.join(HERE, "function_inventory.json")))
    ents = []
    for r in inv["functions"]:
        r = r.rstrip("L")
        i = max(r.rfind("+"), r.rfind("-"))
        e = int(r[:i], 16)
        ents.append((e, e + int(r[i:], 16)))
    ents.sort()
    return ents


def owner(ents, starts, a):
    """function start whose body holds a (build.py rule: look back up to 64 entries)."""
    i = bisect.bisect_right(starts, a) - 1
    for j in range(i, max(i - 64, -1), -1):
        if ents[j][0] <= a <= ents[j][1]:
            return ents[j][0]
    return None


def sources(tip):
    ls = git("ls-tree", "-r", tip, "crates/s5clean/src").decode().splitlines()
    shas = [(l.split()[2], l.split("\t", 1)[1]) for l in ls if l.endswith(".rs")]
    out = git("cat-file", "--batch", inp="".join(s + "\n" for s, _ in shas).encode())
    files, pos = {}, 0
    for sha, path in shas:
        hdr_end = out.index(b"\n", pos)
        size = int(out[pos:hdr_end].split()[2])
        files[path] = out[hdr_end + 1:hdr_end + 1 + size].decode("utf-8", "replace")
        pos = hdr_end + 1 + size + 1
    return files


UNMOD = re.compile(r"\bunmodelled\(\s*((?:\"(?:[^\"\\]|\\.)*\"\s*,?\s*)*)", re.S)


def main():
    git("fetch", "-q", "origin", "clean/integration")
    tip = git("rev-parse", "FETCH_HEAD").decode().strip()
    tip_time = git("log", "-1", "--format=%cI", tip).decode().strip()
    tip_berlin = datetime.fromisoformat(tip_time).astimezone(BERLIN).strftime("%Y-%m-%d %H:%M")
    files = sources(tip)
    ents = inventory()
    starts = [e for e, _ in ents]

    union3 = {int(x, 16) for x in json.load(open(UNION3))["logic_hit"]}
    sim_all = set()
    for l in open(CLASSIFY):
        r = json.loads(l)
        if r["module"] == "bin/SettlersHoK.exe" and r["area"] == "Simulation":
            sim_all.add(int(r["address"], 16))
    sim = union3 & sim_all

    cited_raw, unmod_addrs, unmod_calls = set(), set(), 0
    for path, txt in files.items():
        cited_raw.update(int(m.group(1), 16) for m in HEX6.finditer(txt))
        for m in UNMOD.finditer(txt):
            if re.match(r"\s*&", txt[m.end():]):  # the definition `fn unmodelled(&mut self, ...)`
                continue
            unmod_calls += 1
            unmod_addrs.update(int(h, 16) for h in HEX6.findall(m.group(1)))
            v = re.match(r"\s*([a-z_][a-z0-9_]*)\s*\)", txt[m.end():])
            if v:  # variable message (`let what = match ... { .. => "... (0x50F016) ..." }; self.unmodelled(what)`)
                k = txt.rfind(f"let {v.group(1)}", 0, m.start())
                if k >= 0 and m.start() - k < 6000:
                    for lit in re.findall(r'"(?:[^"\\]|\\.)*"', txt[k:m.start()]):
                        unmod_addrs.update(int(h, 16) for h in HEX6.findall(lit))

    def hits(addrs):
        s = set(addrs)
        s.update(o for o in (owner(ents, starts, a) for a in addrs) if o is not None)
        return s

    cited = hits(cited_raw)
    in_unmod = hits(unmod_addrs)
    sim_c, log_c = sim & cited, union3 & cited
    sim_unmod = sim & in_unmod

    # xc: current release worklist
    xc = None
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(["gh", "release", "download", "xc-worklist", "-R", "Kofi55533/siedler-data", "-p", "worklist.json", "-D", td],
                           capture_output=True)
        if r.returncode == 0:
            wl = json.load(open(os.path.join(td, "worklist.json")))
            rel = json.loads(subprocess.run(["gh", "release", "view", "xc-worklist", "-R", "Kofi55533/siedler-data", "--json", "assets"],
                                            capture_output=True, check=True).stdout)
            upd = next(a["updatedAt"] for a in rel["assets"] if a["name"] == "worklist.json")
            fn = {int(k[3:], 16): v for k, v in wl["kinds"].items() if re.fullmatch(r"FN_[0-9A-Fa-f]+", k)}
            ok = sorted(a for a in sim if a in fn and fn[a].get("samples", 0) > 0 and not fn[a].get("failing"))
            bad = sorted(a for a in sim if a in fn and fn[a].get("failing"))
            xc = {"source": "siedler-data Release xc-worklist (worklist.json)", "worklist_sha": (wl.get("sha") or "")[:12],
                  "as_of": datetime.fromisoformat(upd.replace("Z", "+00:00")).astimezone(BERLIN).strftime("%Y-%m-%d %H:%M"),
                  "of_sim": len(sim), "with_kind": sum(1 for a in sim if a in fn), "pass": len(ok), "fail": len(bad),
                  "fail_addrs": [f"0x{a:06X}" for a in bad],
                  "zero_samples": sum(1 for a in sim if a in fn and not fn[a].get("samples")),
                  "fail_all_kinds": [f"0x{int(k[3:], 16):06X}" for k, v in wl["kinds"].items() if k.startswith("FN_") and v.get("failing")],
                  "calls": {"samples": wl["summary"]["samples"], "failing": wl["summary"]["failing"]}}

    # discovery: dedup union vs union3 (logic functions only: every source filters to the logic set before storing)
    srcs = {}
    def add(name, addrs, path):
        s = {int(a, 16) if isinstance(a, str) else int(a) for a in addrs} - union3
        srcs[name] = {"path": path, "as_of": mtime(path), "new": s}
    if os.path.exists(MAPSTART):
        add("Kartenstarts (49 Karten, je 60 Züge)", json.load(open(MAPSTART))["combined_addrs"], MAPSTART)
    p = f"{FUZZ}/statecmd/box_w1.json"
    if os.path.exists(p):
        add("Befehle aus Spielstand, Box-Welle 1", json.load(open(p))["union_logic"], p)
    p = f"{FUZZ}/gh-novelty/box_known.json"
    if os.path.exists(p):
        add("frühere Box-Suchläufe", json.load(open(p)), p)
    p = f"{FUZZ}/pilot/novelty/loop_new.jsonl"
    if os.path.exists(p):
        add("Novelty-Schleife (Box)", [json.loads(l)["addr"] for l in open(p) if l.strip()], p)
    gh_dir = os.environ.get("NOVELTY_DIR", f"{S}/sbfunc/nov")
    os.makedirs(gh_dir, exist_ok=True)
    subprocess.run(["gh", "release", "download", "novelty-cover-results", "-R", "Kofi55533/siedler-data", "-D", gh_dir, "--clobber"],
                   capture_output=True)
    gh = sorted(glob.glob(f"{gh_dir}/*.json.gz"))
    if gh:
        s = set()
        for f in gh:
            s |= {int(a, 16) for a in json.load(gzip.open(f))["union_logic"]}
        add(f"GitHub-Funktionssuche ({len(gh)} Shards)", [f"0x{a:x}" for a in s], gh[-1])
        rel = json.loads(subprocess.run(["gh", "release", "view", "novelty-cover-results", "-R", "Kofi55533/siedler-data", "--json", "assets"],
                                        capture_output=True, check=True).stdout)
        srcs[f"GitHub-Funktionssuche ({len(gh)} Shards)"]["as_of"] = max(
            datetime.fromisoformat(a["updatedAt"].replace("Z", "+00:00")).astimezone(BERLIN).strftime("%Y-%m-%d %H:%M") for a in rel["assets"])
        srcs[f"GitHub-Funktionssuche ({len(gh)} Shards)"]["path"] = "siedler-data Release novelty-cover-results"
    union_new = set().union(*(v["new"] for v in srcs.values())) if srcs else set()

    out = {
        "generated_berlin": now(),
        "generator": "scripts/scoreboard/make_functions.py (Box)",
        "rewrite": {
            "source": f"Recount: Zitate 0x...... in crates/s5clean/src @ clean/integration {tip[:9]} gegen die in den Scoreboard-Szenarien ausgeführten Funktionen (union3)",
            "tip": tip[:9], "tip_time": tip_berlin, "as_of": now(),
            "sim_total": len(sim), "sim_cited": len(sim_c), "sim_uncited": len(sim) - len(sim_c),
            "sim_unmodelled": len(sim_unmod), "unmodelled_calls": unmod_calls,
            "unmodelled_rule": "Adresse (oder deren Funktionskörper) steht in einem unmodelled(\"...\")-Text, auch über eine Text-Variable (let what = match ...)",
            "logic_total": len(union3), "logic_cited": len(log_c),
            "cited_addresses": len(cited_raw),
        },
        "xc": xc,
        "discovery": {
            "source": "deduplizierte Vereinigung aller Funktionssuch-Ergebnisse, nur Logikfunktionen, die in keinem Scoreboard-Szenario (union3) laufen",
            "as_of": max(v["as_of"] for v in srcs.values()) if srcs else None,
            "total_new": len(union_new),
            "total_new_sim": len(union_new & sim_all),
            "parts": [{"name": k, "new": len(v["new"]), "as_of": v["as_of"]} for k, v in srcs.items()],
        },
        "sim_known": {
            "source": "Funktionsklassifikation (SettlersHoK.exe, Bereich Simulation)", "as_of": mtime(CLASSIFY),
            "classified_sim": len(sim_all), "seen_in_scoreboard": len(sim),
            "seen_incl_search": len(sim | (union_new & sim_all)),
            "unseen": len(sim_all - sim - union_new),
        },
    }
    json.dump(out, open(os.path.join(HERE, "functions.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in out.items()}, ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    main()
