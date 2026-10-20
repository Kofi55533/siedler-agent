#!/usr/bin/env python3
"""Decision controller for the agent fleet (STRATEGY_20261020 s5), SHADOW mode first.

It reads one observation snapshot (runs, queued tasks, open PRs, the domain index docs/subsystems/domains.json, the
newest gate baseline), scores every eligible work unit with the auditable formula of s5.3, admits within capacity,
diversity, WIP and exploration limits (s5.4) with hysteresis, and compares the result with what the current planner
(refill queue + fixed subsystem order) is doing. In shadow mode it launches nothing and changes no task: it only logs
and, with --write, stores the decision on agents/control (fleet/state.json key decision_shadow, files
fleet/decision/shadow-latest.{json,md}).

usage: decision.py shadow [--write] [--min-interval MIN] [--snapshot FILE] [--save-snapshot FILE]
env: GH_TOKEN (needs read access to the private project and the public pool)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fleet_state as fs  # noqa: E402

POOL = "Kofi55533/siedler-agent"
HERE = Path(__file__).resolve().parent
PRIORS = json.loads((HERE / "decision_priors.json").read_text())
ACCOUNT_CAP = 20
WORKER_CAP = 18
PER_DOMAIN = 2
MIN_DOMAINS = 8
HYSTERESIS = 1.2
EVIDENCE = {"DS15", "DS16", "DS17"}
PSEUDO = {"DS15": "battle-ai-evidence", "DS16": "script-evidence", "DS18": "rescue"}


def now():
    return datetime.now(timezone.utc)


def parse_t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


# ---------------------------------------------------------------- observation
def gather():
    snap = {"at": fs.stamp(), "errors": []}

    def get(name, fn):
        try:
            snap[name] = fn()
        except Exception as e:  # a failed read is UNKNOWN, never an empty list
            snap[name] = None
            snap["errors"].append(f"{name}: {fs.safe_error(e) if hasattr(fs, 'safe_error') else type(e).__name__}")

    get("runs", lambda: [{"workflow": PurePath(r.get("path", "")), "title": r.get("display_title", ""), "status": r["status"],
                          "conclusion": r.get("conclusion"), "created": r["created_at"], "id": r["id"]}
                         for r in fs.api(f"repos/{POOL}/actions/runs?per_page=100")["workflow_runs"]])
    get("queued", lambda: [{"number": i["number"], "title": i["title"], "created": i["created_at"]}
                           for i in fs.api(f"repos/{POOL}/issues?labels=queued&state=open&per_page=100&sort=created&direction=asc")
                           if "pull_request" not in i])
    get("prs", lambda: [{"number": p["number"], "head": p["head"]["ref"], "title": p["title"], "draft": p["draft"],
                         "updated": p["updated_at"]} for p in fs.api(f"repos/{fs.PROJECT}/pulls?state=open&per_page=100")])
    get("index", lambda: json.loads(fs.file_text("docs/subsystems/domains.json")))

    def baseline():
        rel = fs.api(f"repos/{POOL}/releases/tags/gate-baseline")
        assets = sorted((a for a in rel["assets"] if re.match(r"base-[0-9a-f]+\.tsv$", a["name"])), key=lambda a: a["updated_at"])
        if not assets:
            return None
        a = assets[-1]
        import urllib.request
        req = urllib.request.Request(a["url"], headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                                                         "Accept": "application/octet-stream", "User-Agent": "siedler-decision"})
        with urllib.request.urlopen(req, timeout=60) as r:
            rows = {}
            for line in r.read().decode().splitlines():
                p = line.split()
                if len(p) >= 2 and p[1].isdigit():
                    rows[p[0]] = int(p[1])
        return {"asset": a["name"], "updated": a["updated_at"], "fixtures": rows}
    get("baseline", baseline)
    return snap


def PurePath(p):
    return p.rsplit("/", 1)[-1]


# ---------------------------------------------------------------- classification
def classify(title, idx):
    """-> (kind, domain, unit). kind: worker | infra | planner"""
    t = title or ""
    if t.startswith("[planner]"):
        return "planner", None, None
    m = re.match(r"Subsystem:\s*([a-z0-9-]+)", t)
    if m and m.group(1) in idx["units"]:
        u = m.group(1)
        return "worker", idx["units"][u]["domain"], u
    if re.match(r"(Rebase|Hygiene)\b", t):
        return "worker", "DS18", "rescue"
    if t.startswith("Coverage:"):
        d = "DS15" if re.search(PRIORS["title_domains"][0][0] + "|hero|barracks|recruit|siege", t) else "DS16"
        return "worker", d, PSEUDO[d]
    addrs = [a.lower().lstrip("0x").rjust(6, "0") for a in re.findall(r"0x([0-9A-Fa-f]{6})\b", t)]
    votes = {}
    for a in addrs:
        d = idx["addr"].get(a)
        if d:
            votes[d] = votes.get(d, 0) + 1
    if votes and t.startswith("Port"):
        d = max(votes, key=votes.get)
        return "worker", d, f"{d.lower()}-port"
    for rx, d in PRIORS["title_domains"]:
        if re.search(rx, t):
            return "worker", d, f"{d.lower()}-frontier"
    if votes:
        d = max(votes, key=votes.get)
        return "worker", d, f"{d.lower()}-port"
    return "worker", "DS17", "inventory"


# ---------------------------------------------------------------- scoring
def family_pressure(base):
    """family -> 1 - matched_prefix_fraction (0 when every fixture of the family is IDENTICAL)."""
    out = {}
    for k, f in PRIORS["families"].items():
        vals = [base["fixtures"].get(x) for x in f["fixtures"]] if base else [None]
        if base and vals and all(v is not None and v >= 1000000 for v in vals):
            out[k] = 0.0
        elif f.get("turns") and base and all(v is not None for v in vals):
            out[k] = 1 - min(1, min(v for v in vals if v < 1000000) / f["turns"])
        else:
            out[k] = PRIORS["defaults"]["unknown_prefix"]  # turns_requested unknown: provisional
    return out


def score_units(snap, mem, current):
    idx = snap["index"]
    W, Dft = PRIORS["weights"], PRIORS["defaults"]
    press = family_pressure(snap.get("baseline"))
    prs = snap.get("prs") or []
    pr_units = {}
    for p in prs:
        if not p["head"].startswith("agent/"):
            continue
        k, d, u = classify(p["title"], idx)
        m = re.match(r"agent/(?:subsystem|unit)-([a-z0-9-]+?)(?:-e\d\d)?$", p["head"])
        if m and m.group(1) in idx["units"]:
            u, d = m.group(1), idx["units"][m.group(1)]["domain"]
        pr_units.setdefault(u, []).append(p["number"])
    running = {u for (_, d, u) in current["running"]}
    cands = []
    t = now()

    def age(u):
        last = mem.get(u, {}).get("last_served") or mem.get(u, {}).get("first_seen")
        return min(1, (t - parse_t(last)).total_seconds() / 86400) if last else 0.0

    def comps_for(d, u, fixtures, used_by, executed, ex_unported, n_open, total=1):
        F = 0.0
        for k, f in PRIORS["families"].items():
            rc = f["causes"].get(d)
            if rc and (fixtures is None or set(fixtures) & set(f["fixtures"])):
                F += rc[0] * rc[1] * press[k] / 3
        dom = idx["domains"].get(d, {})
        mem_n = dom.get("members", 0)
        G = (1 - dom["counts"].get("INTEGRATED", 0) / mem_n) if mem_n else Dft["G_unknown"]
        return {"F": min(1, F), "G": G, "D": PRIORS["enablers"].get(d, Dft["D"]), "R": 1.0 if ex_unported else 0.5,
                "Y": Dft["Y"], "A": age(u), "N": 1.0 if executed < 0.05 * max(1, total) else 0.0, "C": Dft["C"],
                "Z": min(1, mem.get(u, {}).get("no_gain_attempts", 0) / 2), "Q": min(1, n_open / 2)}

    for u, x in idx["units"].items():
        c = comps_for(x["domain"], u, x["fixtures"], x["used_by"], x["executed"], x["executed_unported"], len(pr_units.get(u, [])),
                      x["functions_total"])
        cands.append({"unit": u, "domain": x["domain"], "kind": "evidence" if x["domain"] in EVIDENCE else "code",
                      "comps": c, "tie": x["executed_unported"], "open_prs": pr_units.get(u, []), "running": u in running,
                      "exploration": c["N"] == 1.0})
    # evidence / rescue lanes without primary functions (priors, s5.3a steps 3, 4 and 1)
    def lane(d, u, F, D, R, kind):
        c = {"F": F, "G": Dft["G_unknown"], "D": D, "R": R, "Y": Dft["Y"], "A": age(u), "N": 0.0, "C": Dft["C"],
             "Z": min(1, mem.get(u, {}).get("no_gain_attempts", 0) / 2), "Q": 0.0}
        cands.append({"unit": u, "domain": d, "kind": kind, "comps": c, "open_prs": [], "running": u in running, "tie": 0,
                      "exploration": False})
    dsf = {d: max([cc["comps"]["F"] for cc in cands if cc["domain"] == d] or [0]) for d in ("DS01", "DS12")}
    drafts = [p for p in prs if p["head"].startswith("agent/")]
    lane("DS15", PSEUDO["DS15"], 0.5 * dsf["DS01"], PRIORS["enablers"]["DS15"], 1.0, "evidence")
    lane("DS16", PSEUDO["DS16"], 0.5 * max(dsf["DS12"], 0.1), PRIORS["enablers"]["DS16"], 1.0, "evidence")  # Lua restore/callback contract
    lane("DS18", PSEUDO["DS18"], 0.3, PRIORS["enablers"]["DS18"], min(1, len(drafts) / 5), "rescue")
    for c in cands:
        c["score"] = round(100 * sum(W[k] * v for k, v in c["comps"].items()), 2)
    cands.sort(key=lambda c: (-c["score"], -c["tie"]))
    return cands, pr_units


def admit(cands, budget, current, mem_prev):
    """Rank `budget` slots: running work stays (no cancels), then continuation (open WIP PR), then diversity,
    exploration and score; at most PER_DOMAIN code children per domain, <=25% evidence-only."""
    per_dom = {}
    chosen = []
    for (_, d, u) in current["running"]:
        if d:
            per_dom[d] = per_dom.get(d, 0) + 1
    explore = max(1, math.ceil(0.10 * budget)) if budget else 0
    ev_cap = max(1, budget // 4) if budget else 0
    eligible = [c for c in cands if not c["running"]]
    taken = set()

    def ok(c):
        if c["unit"] in taken:
            return False
        if per_dom.get(c["domain"], 0) >= PER_DOMAIN:
            return False
        if c["kind"] == "evidence" and sum(1 for x in chosen if x["kind"] == "evidence") >= ev_cap:
            return False
        return True

    def take(c, why):
        chosen.append({**c, "why": why})
        taken.add(c["unit"])
        per_dom[c["domain"]] = per_dom.get(c["domain"], 0) + 1

    slots = max(0, budget - len(current["running"]))
    # 0) mandated packages of the current decision epoch (STRATEGY s5.3a), if not already running
    byu = {c["unit"]: c for c in eligible}
    for m in PRIORS.get("mandates", []):
        c = byu.get(m["unit"])
        if c and len(chosen) < slots and ok(c):
            take(c, "mandate: " + m["why"])
    # 1) continuation: units with an open WIP PR and no running attempt (prefer resuming)
    for c in eligible:
        if len(chosen) >= slots:
            break
        if c["open_prs"] and c["kind"] != "rescue" and ok(c):
            take(c, f"resume open PR #{c['open_prs'][0]}")
    # 2) protected exploration for unexercised families
    for c in sorted(eligible, key=lambda c: c["domain"] == "DS17"):  # known game families before unknown clusters
        if sum(1 for x in chosen if x["why"].startswith("exploration")) >= explore or len(chosen) >= slots:
            break
        if c["exploration"] and ok(c):
            take(c, "exploration slot (fixtures execute <5% of this unit)")
    # 3) diversity: best unit of each not-yet-served domain until MIN_DOMAINS domains are represented
    for c in eligible:
        if len(chosen) >= slots or len(per_dom) >= MIN_DOMAINS:
            break
        if c["domain"] not in per_dom and ok(c):
            take(c, "domain diversity")
    # 4) score order
    for c in eligible:
        if len(chosen) >= slots:
            break
        if ok(c):
            take(c, "score")
    # hysteresis: a previously chosen unit that dropped out is kept unless its replacement beat it by 20% twice
    prev = {p["unit"]: p for p in (mem_prev or {}).get("chosen", [])}
    chal = dict((mem_prev or {}).get("challengers", {}))
    byu = {c["unit"]: c for c in cands}
    new_chal = {}
    for pu, p in prev.items():
        if pu in taken or pu not in byu or byu[pu]["running"]:
            continue
        inc = byu[pu]
        weakest = min((x for x in chosen if x["why"] in ("score", "domain diversity")), key=lambda x: x["score"], default=None)
        if weakest is None:
            continue
        if weakest["score"] >= HYSTERESIS * inc["score"]:
            n = chal.get(weakest["unit"], 0) + 1
            new_chal[weakest["unit"]] = n
            if n >= 2:
                continue  # challenger confirmed over two epochs
        chosen.remove(weakest)
        taken.discard(weakest["unit"])
        chosen.append({**inc, "why": f"hysteresis: kept over {weakest['unit']} ({weakest['score']} < 1.2 x {inc['score']} or not yet 2 epochs)"})
        taken.add(pu)
    return chosen, explore, new_chal


def decide(snap, mem):
    idx = snap["index"]
    if not idx or snap.get("runs") is None or snap.get("queued") is None:
        return {"at": snap["at"], "mode": "shadow", "status": "UNKNOWN", "budget": 0, "chosen": [],
                "errors": snap["errors"] + ["required input missing: decision deferred (never decide on a failed read)"]}
    runs = snap["runs"]
    active = [r for r in runs if r["status"] != "completed"]
    infra = [r for r in active if r["workflow"] != "opencode.yml"]
    cur_running, planner = [], 0
    seen = set()
    for r in active:
        if r["workflow"] != "opencode.yml" or r["title"] in seen:
            continue
        seen.add(r["title"])
        k, d, u = classify(r["title"], idx)
        if k == "planner":
            planner += 1
        else:
            cur_running.append((r["title"], d, u))
    queued = [(q["title"], *classify(q["title"], idx)[1:]) for q in snap["queued"]]
    current = {"running": [(t, d, u) for (t, d, u) in cur_running], "queued": queued}
    budget = max(0, min(WORKER_CAP, ACCOUNT_CAP - len(infra) - planner))
    units_mem = mem.get("units", {})
    cands, pr_units = score_units(snap, units_mem, current)
    chosen, explore, chal = admit(cands, budget, current, mem.get("decision_shadow"))
    free = max(0, budget - len(cur_running))
    ideal, _, _ = admit([{**c, "running": False} for c in cands], budget, {"running": []}, None)
    # what the current planner launches next: queue order (oldest first), first `free` entries (+ fixed subsystem order)
    cur_next = queued[:max(free, len(chosen))]
    dom = lambda xs: dict(sorted(((d, sum(1 for x in xs if x == d)) for d in set(xs) if d), key=lambda kv: kv[0]))
    run_dom = dom([d for (_, d, _) in cur_running])
    next_dom_cur = dom([d for (_, d, _) in cur_next])
    next_dom_sh = dom([c["domain"] for c in chosen])
    all_doms = sorted(idx["domains"])
    alloc_cur = {d: run_dom.get(d, 0) + next_dom_cur.get(d, 0) for d in all_doms}
    alloc_sh = {d: run_dom.get(d, 0) + next_dom_sh.get(d, 0) for d in all_doms}
    comparison = {
        "running_by_domain": run_dom,
        "next_current": [{"title": t[:110], "domain": d, "unit": u} for (t, d, u) in cur_next],
        "next_shadow": [{"unit": c["unit"], "domain": c["domain"], "score": c["score"], "why": c["why"]} for c in chosen],
        "domain_allocation_current": {d: v for d, v in alloc_cur.items() if v},
        "domain_allocation_shadow": {d: v for d, v in alloc_sh.items() if v},
        "domains_served_current": sum(1 for v in alloc_cur.values() if v),
        "domains_served_shadow": sum(1 for v in alloc_sh.values() if v),
        "over_cap_current": {d: v for d, v in run_dom.items() if v > PER_DOMAIN},
        "starved_in_current": [d for d in all_doms if alloc_sh[d] and not alloc_cur[d]],
        "ideal_allocation_shadow": dom([c["domain"] for c in ideal]),
        "ideal_vs_current_running": {d: [run_dom.get(d, 0), dom([c["domain"] for c in ideal]).get(d, 0)] for d in all_doms
                                     if run_dom.get(d, 0) or dom([c["domain"] for c in ideal]).get(d, 0)},
        "domain_overlap_next": sum(min(next_dom_cur.get(d, 0), next_dom_sh.get(d, 0)) for d in all_doms),
    }
    return {"at": snap["at"], "mode": "shadow", "status": "OK", "budget": budget, "infra_jobs": len(infra),
            "planner_runs": planner, "running_workers": len(cur_running), "free_slots": free,
            "exploration_slots": explore, "baseline": (snap.get("baseline") or {}).get("asset"),
            "chosen": [{"unit": c["unit"], "domain": c["domain"], "score": c["score"], "why": c["why"],
                        "comps": {k: round(v, 3) for k, v in c["comps"].items()}} for c in chosen],
            "top": [{"unit": c["unit"], "domain": c["domain"], "score": c["score"], "running": c["running"],
                     "comps": {k: round(v, 3) for k, v in c["comps"].items()}} for c in cands[:25]],
            "runner_up": next(({"unit": c["unit"], "score": c["score"]} for c in cands
                               if not c["running"] and c["unit"] not in {x["unit"] for x in chosen}), None),
            "challengers": chal, "comparison": comparison, "errors": snap["errors"]}


def markdown(dec):
    if dec.get("status") != "OK":
        return f"# Decision shadow {dec['at']}\n\nStatus {dec.get('status')}: " + "; ".join(dec.get("errors", []))
    c = dec["comparison"]
    L = [f"# Decision controller (SHADOW) {dec['at']}", "",
         f"Budget {dec['budget']} = min(18, 20 - {dec['infra_jobs']} infra - {dec['planner_runs']} planner); running workers "
         f"{dec['running_workers']}, free {dec['free_slots']}, exploration slots {dec['exploration_slots']}; baseline {dec['baseline']}.", "",
         "## Shadow choice for the next launches", "", "| # | unit | domain | score | why |", "|---|---|---|---|---|"]
    L += [f"| {i+1} | {x['unit']} | {x['domain']} | {x['score']} | {x['why']} |" for i, x in enumerate(dec["chosen"])]
    if dec.get("runner_up"):
        L += ["", f"Runner-up: {dec['runner_up']['unit']} ({dec['runner_up']['score']})."]
    L += ["", "## Current planner next launches (queue order)", ""]
    L += [f"- {x['domain']} `{x['unit']}`: {x['title']}" for x in c["next_current"]] or ["- (queue empty)"]
    L += ["", "## Comparison", "",
          f"- domains served (running + next): current {c['domains_served_current']}, shadow {c['domains_served_shadow']}",
          f"- allocation current: {c['domain_allocation_current']}",
          f"- allocation shadow:  {c['domain_allocation_shadow']}",
          f"- running over the per-domain cap {PER_DOMAIN}: {c['over_cap_current'] or 'none'}",
          f"- domains the shadow serves but the current planner does not: {c['starved_in_current'] or 'none'}",
          f"- ideal allocation from scratch (budget {dec['budget']}, all slots free): {c['ideal_allocation_shadow']}",
          f"- per domain [running now, ideal]: {c['ideal_vs_current_running']}",
          f"- same-domain overlap of the next launches: {c['domain_overlap_next']} of {len(dec['chosen'])}", "",
          "## Top scores (inputs)", "", "| unit | domain | score | F | G | D | R | Y | A | N | C | Z | Q |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for x in dec["top"][:15]:
        k = x["comps"]
        L.append(f"| {x['unit']}{' (running)' if x['running'] else ''} | {x['domain']} | {x['score']} | " +
                 " | ".join(str(k[n]) for n in "FGDRYANCZQ") + " |")
    if dec["errors"]:
        L += ["", "Read errors (UNKNOWN inputs): " + "; ".join(dec["errors"])]
    return "\n".join(L) + "\n"


def remember(state, dec):
    units = state.setdefault("units", {})
    t = dec["at"]
    for x in dec.get("top", []):
        u = units.setdefault(x["unit"], {"domain": x["domain"], "first_seen": t})
        if x["running"]:
            u["last_served"] = t
    state["decision_shadow"] = {k: dec[k] for k in ("at", "mode", "status", "budget", "chosen", "challengers") if k in dec}
    state["decision_shadow"]["comparison"] = {k: v for k, v in dec.get("comparison", {}).items() if k != "next_current"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["shadow"])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--min-interval", type=int, default=15)
    ap.add_argument("--snapshot")
    ap.add_argument("--save-snapshot")
    a = ap.parse_args()
    store = fs.Store()
    mem = {}
    if a.write or not a.snapshot:
        try:
            mem = store.read()[0]
        except Exception as e:
            print(f"decision: control state unreadable ({type(e).__name__}) -> shadow without memory")
    last = parse_t((mem.get("decision_shadow") or {}).get("at"))
    if a.write and last and (now() - last).total_seconds() < a.min_interval * 60:
        print(f"decision: last shadow decision {mem['decision_shadow']['at']} < {a.min_interval} min ago -> skipped")
        return 0
    snap = json.loads(Path(a.snapshot).read_text()) if a.snapshot else gather()
    if a.save_snapshot:
        Path(a.save_snapshot).write_text(json.dumps(snap))
    dec = decide(snap, mem)
    md = markdown(dec)
    print(md)
    if a.write and dec.get("status") == "OK":
        store.update(lambda s: remember(s, dec), "decision shadow " + dec["at"],
                     extra={"fleet/decision/shadow-latest.json": json.dumps(dec, indent=1) + "\n",
                            "fleet/decision/shadow-latest.md": md})
        print("decision: stored on agents/control (fleet/decision/shadow-latest.*)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
