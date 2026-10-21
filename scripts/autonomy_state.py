"""Pure state transitions for the independent DeepSeek fleet.

The public repository contains policy and code only. Domain inventories, plans,
leases, checkpoints and evidence live on the private agents/control branch.
Neither a board claim nor a model's `complete` label is a completion receipt.
"""
from __future__ import annotations

import copy
import fnmatch
import re

MODEL = "opencode/deepseek-v4.1-flash-free"
SCHEMA = 2
CAPACITY = 20
VERIFY_SLOTS = 4
HARD_PATHS = ("recomp/", "oracle/", "crates/s5verify/", "crates/s5diff/", ".github/")
TERMINAL = {"accepted", "equivalent"}
OUTCOMES = {"ready", "checkpoint", "evidence", "stuck", "too_large", "too_small",
            "setup_error", "provider_error", "access_refusal"}


def address(value):
    value = str(value).lower().removeprefix("0x")
    if not re.fullmatch(r"[0-9a-f]{6,8}", value):
        raise ValueError("Invalid native address")
    return f"{int(value, 16):06x}"


def inventory_addresses(inventory):
    # The L suffix is the authoritative 29,723-entry logic inventory. The
    # smaller subsystem map is a routing hint, never an exclusion rule.
    values = {address(x.split("+", 1)[0]) for x in inventory["functions"] if x.endswith("L")}
    if len(values) != inventory["logic_functions"]:
        raise ValueError("Incomplete or duplicate whole-game inventory")
    return values


def initialize(inventory, index, catalog, at):
    addresses = inventory_addresses(inventory)
    units = {}
    for uid, entry in sorted(index["units"].items()):
        members = sorted(address(a) for a, owner in index["addr"].items()
                         if owner == uid and address(a) in addresses)
        # domains.json.addr maps addresses to DS domains, not unit ids on older
        # maps. The catalog's per-function list is preferred when available.
        members = sorted(set(catalog.get(uid, {}).get("addresses", members)) & addresses)
        if members:
            units[uid] = unit(uid, entry.get("domain", "unclassified"), members, at,
                              catalog.get(uid, {}))
    assigned = set().union(*(set(u["addresses"]) for u in units.values())) if units else set()
    # Preserve every address outside the 12,568-entry map. Bootstrap bands are
    # replaceable starting scopes; workers may regroup them by native behavior.
    remaining = sorted(addresses - assigned)
    for i in range(0, len(remaining), 512):
        uid = f"inventory-{i // 512:03d}"
        units[uid] = unit(uid, "unclassified", remaining[i:i + 512], at, {})
    # Map overlaps are not parallel ownership. First deterministic owner wins;
    # every native obligation has exactly one primary unit.
    seen = set()
    for u in units.values():
        u["addresses"] = [a for a in u["addresses"] if a not in seen]
        seen.update(u["addresses"])
    units = {k: u for k, u in units.items() if u["addresses"]}
    if seen != addresses:
        raise ValueError("Whole-game partition lost addresses")
    return {"schema": SCHEMA, "created": at, "inventory_sha256": inventory["source_sha256"],
            "total": len(addresses), "units": units, "leases": {}, "tickets": {},
            "next_ticket": 1, "provider_hold": None, "events": [], "integration": None}


def unit(uid, domain, members, at, info):
    return {"id": uid, "domain": domain, "addresses": list(members), "created": at,
            "last_served": 0, "epoch": 1, "branch": f"agent/unit-{uid}-e01",
            "overview": f"docs/subsystems/{uid}.md", "status": "ready", "attempts": 0,
            "no_gain": 0, "verified": {}, "findings": [], "wip": None,
            "fixtures": info.get("fixtures", "bc-full"), "title": info.get("title", uid)}


def outstanding(u):
    return [a for a in u["addresses"] if u["verified"].get(a, {}).get("status") not in TERMINAL]


def resource_overlap(left, right):
    """A plain file reserves the file; file::item reserves that exact item.

    Glob/directory scopes are rejected by validate_plan. Items in one file may
    run concurrently; integration batches use whole-file disjointness.
    """
    lp, _, li = left.partition("::")
    rp, _, ri = right.partition("::")
    return lp == rp and (not li or not ri or li == ri)


def validate_plan(plan, u):
    result = copy.deepcopy(plan)
    for k in ("goal", "native_family", "acceptance", "next_step"):
        if not isinstance(result.get(k), str) or not result[k].strip():
            raise ValueError("Plan requires " + k)
    addrs = [address(a) for a in result.get("addresses", [])]
    if not addrs or not set(addrs) <= set(outstanding(u)):
        raise ValueError("Plan must select outstanding addresses owned by this unit")
    if len(addrs) != len(set(addrs)):
        raise ValueError("Duplicate native obligations")
    resources = result.get("resources", [])
    if not 1 <= len(resources) <= 16:
        raise ValueError("Plan requires 1..16 concrete files/items")
    for resource in resources:
        path, _, item = resource.partition("::")
        if (not path.startswith(("crates/s5clean/", "crates/s5xcheck/tests/", "docs/subsystems/",
                                 "docs/logic/tools/", "docs/clean/new-fixtures/", "fixtures-gen/"))
                or path.endswith("/") or any(x in path for x in ("..", "*", "?", "\\"))
                or (item and not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", item))):
            raise ValueError("Unsafe or broad resource")
        if path == "docs/AGENT_BOARD.md" or path.startswith(HARD_PATHS):
            raise ValueError("Hard-blocked resource")
    minutes = result.get("minutes")
    if not isinstance(minutes, int) or not 10 <= minutes <= 150:
        raise ValueError("Plan estimate must be 10..150 minutes; split larger work")
    result["addresses"] = addrs
    return result


def event(state, kind, at, **fields):
    state["events"].append({"at": at, "kind": kind, **fields})
    state["events"] = state["events"][-500:]


def claim_unit(state, uid, owner, run, at):
    u = state["units"][uid]
    if state["provider_hold"] or u["status"] not in {"ready", "repair"} or uid in state["leases"]:
        return False
    state["leases"][uid] = {"owner": owner, "run": str(run), "heartbeat": at, "resources": []}
    u.update(status="running", last_served=at)
    u["attempts"] += 1
    event(state, "claim", at, unit=uid, run=str(run))
    return True


def reserve(state, uid, owner, plan, at, external=()):
    lease = state["leases"].get(uid)
    if not lease or lease["owner"] != owner:
        raise ValueError("Reservation lost")
    plan = validate_plan(plan, state["units"][uid])
    occupied = [r for k, v in state["leases"].items() if k != uid for r in v["resources"]] + list(external)
    conflicts = sorted({p for p in plan["resources"] for r in occupied if resource_overlap(p, r)})
    if conflicts:
        return conflicts
    lease.update(resources=plan["resources"], heartbeat=at)
    state["units"][uid]["plan"] = plan
    event(state, "reserve", at, unit=uid, addresses=len(plan["addresses"]))
    return []


def select(state, active_jobs, legacy_jobs=0, limit=CAPACITY, at=None, unavailable_units=()):
    """No LLM planner, fixed domain quotas or dependency on a heavy owner.

    Existing jobs count even when their heartbeat expires. Repairs and never
    served domains come first, then fair continuing ownership. A thin domain
    joins adjacent work within its run; a stuck unit is split on completion.
    """
    if state["provider_hold"] or (at is not None and state.get("provider_retry_after", 0) > at):
        return []
    room = max(0, min(CAPACITY, limit) - active_jobs - legacy_jobs)
    candidates = [u for u in state["units"].values()
                  if u["status"] in {"ready", "repair"} and not u.get("dispatch")
                  and u["id"] not in unavailable_units
                  and u["id"] not in state["leases"] and outstanding(u)
                  and (at is None or u.get("retry_after", 0) <= at)]
    candidates.sort(key=lambda u: (u["status"] != "repair", u["last_served"], u["no_gain"], u["id"]))
    # Keep four runner slots for gates even before the first package arrives.
    # Otherwise long workers can occupy every slot and starve their own PRs.
    gates = sum(t["status"] == "verifying" or bool(t.get("dispatch")) for t in state["tickets"].values())
    gates += bool(state.get("integration"))
    reserve = max(0, VERIFY_SLOTS - gates)
    return [u["id"] for u in candidates[:max(0, room - reserve)]]


def continue_domain(state, uid, owner, run, at):
    """Start another independent package without releasing this runner slot.

    A submitted/checkpointed cut retains its own branch, gate lease and native
    obligations. Move its unattempted remainder to a successor in the same
    domain, so neither a slow nor failing gate strands a five-hour worker.
    """
    u = state["units"][uid]
    if state["provider_hold"] or state.get("provider_retry_after", 0) > at:
        return None
    selected = set(u.get("plan", {}).get("addresses", []))
    remaining = [a for a in outstanding(u) if a not in selected]
    if selected and remaining and (u.get("wip") or u["status"] == "gating"):
        root = u.get("continuation_root", uid)
        n = 1
        while f"{root}-next{n}" in state["units"]:
            n += 1
        child = f"{root}-next{n}"
        successor = unit(child, u["domain"], remaining, at, u)
        successor.update(parent=uid, parent_overview=u["overview"], continuation_root=root,
                         prior_package=copy.deepcopy(u.get("wip")), findings=copy.deepcopy(u["findings"]))
        state["units"][child] = successor
        u["addresses"] = [a for a in u["addresses"] if a not in set(remaining)]
    candidates = [v for v in state["units"].values()
                  if v["domain"] == u["domain"] and v["id"] != uid
                  and v["status"] in {"ready", "repair"} and not v.get("dispatch")
                  and v["id"] not in state["leases"] and outstanding(v)
                  and v.get("retry_after", 0) <= at]
    candidates.sort(key=lambda v: (v["status"] != "repair", v["last_served"], v["id"]))
    for v in candidates:
        if claim_unit(state, v["id"], owner, run, at):
            event(state, "continue_domain", at, previous=uid, unit=v["id"], run=str(run))
            return copy.deepcopy(v)
    # An evidence-only attempt can re-plan its unchanged domain immediately;
    # no-gain accounting in finish() splits a repeatedly stuck assignment.
    if u["status"] in {"ready", "repair"} and claim_unit(state, uid, owner, run, at):
        return copy.deepcopy(u)
    return None


def finish(state, uid, owner, result, at):
    lease = state["leases"].get(uid)
    if not lease or lease["owner"] != owner:
        raise ValueError("Stale worker result")
    outcome = result.get("outcome")
    if outcome not in OUTCOMES:
        raise ValueError("Unknown worker outcome")
    u = state["units"][uid]
    if result.get("checkpoint"):
        u["wip"] = result["checkpoint"]
    u["findings"].append({"at": at, "outcome": outcome, "summary": str(result.get("summary", ""))[:6000]})
    u["findings"] = u["findings"][-12:]
    if outcome == "ready":
        if not result.get("pr") or not re.fullmatch(r"[0-9a-f]{40}", result.get("head", "")):
            raise ValueError("Ready package needs an immutable PR head")
        tid = str(state["next_ticket"])
        state["next_ticket"] += 1
        state["tickets"][tid] = {"id": tid, "unit": uid, "pr": int(result["pr"]), "head": result["head"],
                                  "branch": u["branch"], "resources": lease["resources"], "status": "queued",
                                  "created": at, "proofs": result.get("proofs", []), "attempts": 0}
        u["status"] = "gating"
        # Keep resources reserved until the receipt lands or returns for repair.
        lease.update(owner="gate:" + tid, run=None, heartbeat=at)
    else:
        del state["leases"][uid]
        u["status"] = "repair" if u["wip"] else "ready"
        if outcome in {"evidence", "stuck", "too_large"}:
            u["no_gain"] += 1
        if outcome == "access_refusal":
            state["provider_hold"] = {"at": at, "kind": result.get("refusal_kind", "provider_access"),
                                      "reason": result.get("summary", "Access refused; requires an approved configuration")}
        if outcome == "provider_error":
            state["provider_errors"] = state.get("provider_errors", 0) + 1
            state["provider_retry_after"] = at + min(14400, 900 * 2 ** min(state["provider_errors"] - 1, 4))
        elif outcome == "setup_error":
            u["retry_after"] = at + 900
        # A WIP branch is never thrown away to resize a unit. Continue its
        # failing transition with a smaller plan; otherwise split remaining work.
        if not u["wip"] and (outcome == "too_large" or u["no_gain"] >= 2):
            split(state, uid, at)
    event(state, "finish", at, unit=uid, outcome=outcome)


def split(state, uid, at):
    u = state["units"][uid]
    members = outstanding(u)
    if len(members) < 2:
        u["status"] = "ready"
        return
    u["status"] = "split"
    cut = (len(members) + 1) // 2
    for suffix, group in (("a", members[:cut]), ("b", members[cut:])):
        child = uid + "-" + suffix
        state["units"][child] = unit(child, u["domain"], group, at, u)
        state["units"][child]["parent"] = uid
        state["units"][child]["parent_overview"] = u["overview"]
        state["units"][child]["findings"] = copy.deepcopy(u["findings"])
        if u.get("recovery_patch"):
            state["units"][child]["recovery_patch"] = u["recovery_patch"]
    event(state, "split", at, unit=uid, remaining=len(members))


def reconcile_runs(state, run_status, at, grace=1200):
    """Timeout is insufficient: only a confirmed completed run frees a lease.

    Missing API data/unknown runs retain their reservations. Dispatches which
    never started are recoverable only after the controller has confirmed all
    active runs and the dispatch grace expired (handled by the runtime).
    """
    for uid, lease in list(state["leases"].items()):
        if lease["run"] and run_status.get(lease["run"]) == "completed":
            u = state["units"][uid]
            u["status"] = "repair" if u.get("wip") else "ready"
            del state["leases"][uid]
            event(state, "recover_completed_run", at, unit=uid, run=lease["run"])


def disjoint_batch(state, maximum=4):
    tickets = sorted((t for t in state["tickets"].values() if t["status"] == "verified"),
                     key=lambda t: (t["created"], int(t["id"])))
    batch, paths, base = [], set(), None
    for t in tickets:
        newpaths = {r.split("::", 1)[0] for r in t["resources"]}
        if (base and t["receipt"]["base"] != base) or paths & newpaths:
            continue
        if any(set(pair) <= {t["id"], *batch} for pair in state.get("incompatible_batches", [])):
            continue
        batch.append(t["id"])
        paths |= newpaths
        base = t["receipt"]["base"]
        if len(batch) == maximum:
            break
    return batch


def integration_ready(state, at, coalesce_seconds=180):
    batch = disjoint_batch(state)
    if not batch:
        return []
    oldest = min(state["tickets"][t]["receipt"].get("measured_at", 0) for t in batch)
    # Give parallel preflights of the current epoch a bounded chance to join.
    # Workers continue running while this short admission delay elapses.
    if len(batch) < 4 and at - oldest < coalesce_seconds and any(
            t["status"] == "verifying" for t in state["tickets"].values()):
        return []
    return batch


def accept(state, tids, commit, at):
    for tid in tids:
        t = state["tickets"][tid]
        if t["status"] != "verified":
            raise ValueError("Cannot accept a package without a gate receipt")
        t.update(status="accepted", commit=commit)
        u = state["units"][t["unit"]]
        # Count only individually reported proof obligations; citations alone
        # and an overview's Status: complete never exhaust the inventory.
        for proof in t["proofs"]:
            a = address(proof["address"])
            if a not in u["addresses"] or not proof.get("reference_test") or not proof.get("clean_entry"):
                continue
            u["verified"][a] = {**proof, "status": "accepted", "commit": commit}
        u["epoch"] += 1
        u["branch"] = f"agent/unit-{u['id']}-e{u['epoch']:02d}"
        u.update(status="ready", wip=None, no_gain=0)
        state["leases"].pop(u["id"], None)
    state["integration"] = None
    event(state, "accept", at, tickets=tids, commit=commit)


def check_files(paths, resources):
    allowed = {r.split("::", 1)[0] for r in resources}
    return sorted(p for p in paths if p not in allowed or p.startswith(HARD_PATHS))


def dashboard(state):
    unique = {a for u in state["units"].values() for a, p in u["verified"].items()
              if p.get("status") in TERMINAL}
    return {"inventory": state["total"], "reported_reference_verified_and_integrated": len(unique),
            "remaining": state["total"] - len(unique), "leases": len(state["leases"]),
            "units": len(state["units"]), "provider_hold": bool(state["provider_hold"]),
            "tickets": {s: sum(t["status"] == s for t in state["tickets"].values())
                        for s in ("queued", "verifying", "verified", "accepted", "repair")}}
