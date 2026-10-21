#!/usr/bin/env python3
"""Independent domain workers and parallel gate admission, dormant until approved.

`shadow` only reads GitHub. Every mutating entry requires FLEET_V2_ENABLED=true
and a main-branch workflow. Private state is namespaced away from the live fleet.
"""
from __future__ import annotations

import argparse
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid

import autonomy_state as core
import fleet_state as fs
import gate_contract as contract

HERE = Path(__file__).resolve().parent
POOL = "Kofi55533/siedler-agent"
REPO = Path("/opt/s5/siedler-sim-rs")
STORE = fs.Store()
OWNER = uuid.uuid4().hex
RUN = os.environ.get("GITHUB_RUN_ID", "local")
REF = os.environ.get("GITHUB_REF_NAME", "")
KEY = "autonomy_v2"


class ProviderError(RuntimeError):
    pass


def command(args, cwd=None, env=None, timeout=900, check=True):
    result = subprocess.run(list(map(str, args)), cwd=REPO if cwd is None else cwd, env=env, capture_output=True,
                            text=True, timeout=timeout)
    if check and result.returncode:
        # Do not echo commands, URLs or stderr: they can contain credentials.
        raise RuntimeError(f"{Path(str(args[0])).name} failed (exit {result.returncode})")
    return result


def git(*args, check=True):
    return command(["git", *args], cwd=REPO, check=check).stdout.strip()


def enabled():
    if os.environ.get("FLEET_V2_ENABLED") != "true" or REF != "main":
        raise RuntimeError("Review interlock: mutations require approved main rollout and FLEET_V2_ENABLED=true")


def update(operation, message, extra=None):
    enabled()
    return STORE.update(lambda state: operation(state[KEY]), "autonomy v2: " + message, extra=extra)


def read():
    return STORE.read()[0].get(KEY)


def snapshot():
    # Query each live status explicitly; never infer capacity from the last 100
    # mixed workflow runs. Failed/incomplete reads abort the scheduling cycle.
    active = []
    for status in ("queued", "in_progress", "waiting", "pending", "requested"):
        active.extend(fs.paged(f"repos/{POOL}/actions/runs?status={status}", "workflow_runs"))
    active = {str(r["id"]): r for r in active}
    prs = fs.paged(f"repos/{fs.PROJECT}/pulls?state=open&base=clean/integration")
    return {"runs": active, "prs": prs, "at": time.time()}


def bootstrap(apply=True):
    if apply:
        enabled()
    inventory = json.loads((HERE / "scoreboard/function_inventory.json").read_text())
    index = json.loads(fs.file_text("docs/subsystems/domains.json"))
    mapped = json.loads(fs.file_text("docs/subsystems/subsystems.json"))
    queue = json.loads((HERE / "subsystems.json").read_text())
    catalog = {s["id"]: dict(s) for s in queue["subsystems"]}
    for s in mapped["subsystems"]:
        catalog.setdefault(s["id"], {}).update(addresses=[core.address(f["addr"]) for f in s["functions"]])
    state = core.initialize(inventory, index, catalog, time.time())
    # Adopt open subsystem PRs rather than rediscovering them. Closed/merged
    # epochs are never resumed; the original legacy branch remains untouched.
    prs = fs.paged(f"repos/{fs.PROJECT}/pulls?state=all&base=clean/integration")
    for p in reversed(prs):
        h = p["head"]["ref"]
        match = re.fullmatch(r"agent/(?:subsystem-(.+)|unit-(.+)-e(\d+))", h)
        if not match:
            continue
        uid = match[1] or match[2]
        if uid not in state["units"]:
            continue
        u = state["units"][uid]
        epoch = int(match[3] or 0)
        u["epoch"] = max(u["epoch"], epoch + 1)
        u["branch"] = f"agent/unit-{uid}-e{u['epoch']:02d}"
        if p["state"] == "open":
            u.update(branch=h, status="repair", wip={"head": p["head"]["sha"], "pr": p["number"]})
    def install(root):
        if KEY not in root:
            root[KEY] = state
    if apply:
        STORE.update(install, "autonomy v2: bootstrap complete whole-game partition")
    return state


def dispatch(workflow, inputs):
    fs.api(f"repos/{POOL}/actions/workflows/{workflow}/dispatches", "POST", {"ref": "main", "inputs": inputs})


def wake_control(soon=False):
    # Completion events are insufficient while workers run for five hours.
    # Heartbeats coalesce one dispatch per five minutes without a sleeping job.
    def reserve(s):
        at = time.time()
        if at - s.get("control_requested_at", 0) < (60 if soon else 300):
            return False
        s["control_requested_at"] = at
        return True
    if update(reserve, "reserve controller wakeup"):
        dispatch("fleet-control.yml", {"mode": "control"})


def control(shadow=False):
    snap = snapshot()
    state = read()
    if state is None:
        if shadow:
            state = bootstrap(apply=False)
        else:
            bootstrap()
            state = read()
    # Reclaim only after an explicit completed response, never on a timer.
    statuses = {rid: r["status"] for rid, r in snap["runs"].items()}
    tracked = {l["run"] for l in state["leases"].values() if l.get("run")}
    tracked |= {t.get("run") for t in state["tickets"].values() if t["status"] == "verifying" and t.get("run")}
    if state.get("integration") and state["integration"].get("run"):
        tracked.add(state["integration"]["run"])
    for rid in tracked - set(statuses):
        statuses[rid] = fs.api(f"repos/{POOL}/actions/runs/{rid}")["status"]
    at = snap["at"]
    latest_base = fs.api(f"repos/{fs.PROJECT}/git/ref/heads/{fs.BASE}")["object"]["sha"]
    stale_bases = set()
    for base in {t["receipt"]["base"] for t in state["tickets"].values() if t["status"] == "verified"}:
        if base == latest_base:
            continue
        comparison = fs.api(f"repos/{fs.PROJECT}/compare/{base}...{latest_base}")
        files = comparison.get("files", [])
        # GitHub caps compare files at 300. An incomplete/rewritten comparison
        # cannot establish that only coordination prose changed.
        if (comparison["status"] not in {"ahead", "identical"} or len(files) >= 300 or not files
                or any(not f["filename"].startswith("docs/") or not f["filename"].endswith(".md") for f in files)):
            stale_bases.add(base)
    legacy_units = set()
    for run in snap["runs"].values():
        if run.get("path", "").endswith("/opencode.yml"):
            match = re.match(r"Subsystem:\s*([a-z0-9-]+)", run["display_title"])
            if match:
                legacy_units.add(match[1])

    # Recover a push that succeeded immediately before the runner or state
    # write failed. Only an already tested, recorded integration intent counts.
    integration = state.get("integration")
    if not shadow and integration and integration.get("candidate"):
        candidate = integration["candidate"]
        latest = fs.api(f"repos/{fs.PROJECT}/git/ref/heads/{fs.BASE}")["object"]["sha"]
        landed = candidate == latest
        if not landed:
            comparison = fs.api(f"repos/{fs.PROJECT}/compare/{candidate}...{latest}")
            landed = comparison["status"] in {"ahead", "identical"}
        if landed:
            update(lambda s: core.accept(s, integration["tickets"], candidate, at), "recover confirmed integration push")
            state = read()

    def reconcile(s):
        core.reconcile_runs(s, statuses, at)
        for u in s["units"].values():
            pending = u.get("dispatch")
            if pending and pending["at"] < at - 1200:
                # Match workflow run-name to the nonce, not to a stale title.
                if not any(pending["nonce"] in r["display_title"] for r in snap["runs"].values()):
                    u.pop("dispatch", None)
        for t in s["tickets"].values():
            if t["status"] == "verified" and t["receipt"]["base"] in stale_bases:
                t.update(status="queued", run=None, retry_after=0)
            if t["status"] == "verifying" and statuses.get(t.get("run")) == "completed":
                t.update(status="queued", run=None)
            p = t.get("dispatch")
            if p and p["at"] < at - 1200 and not any(p["nonce"] in r["display_title"] for r in snap["runs"].values()):
                t.pop("dispatch", None)
        integration = s.get("integration")
        if integration and statuses.get(integration.get("run")) == "completed":
            s["integration"] = None
        elif integration and not integration.get("run") and integration["at"] < at - 1200:
            if not any(integration["nonce"] in r["display_title"] for r in snap["runs"].values()):
                s["integration"] = None
    if shadow:
        reconcile(state)
        print(json.dumps({"mode": "shadow", "live_jobs": len(snap["runs"]),
                          "would_start": core.select(state, len(snap["runs"]), at=at,
                                                     unavailable_units=legacy_units), **core.dashboard(state)}))
        return
    update(reconcile, "reconcile confirmed runs")
    state = read()
    room = max(0, 20 - len(snap["runs"]))
    # Batches have a short final writer and a full combined test; independent
    # preflights run on up to four runners. Do not occupy model slots waiting.
    batch = core.integration_ready(state, at)
    if batch and not state["integration"] and room:
        nonce = uuid.uuid4().hex[:12]
        def admit_batch(s):
            if s["integration"] or not all(s["tickets"][t]["status"] == "verified" for t in batch):
                return False
            s["integration"] = {"tickets": batch, "nonce": nonce, "at": at, "run": None}
            return True
        if update(admit_batch, "reserve combined integration"):
            dispatch("fleet-integrate.yml", {"nonce": nonce})
            room -= 1
    state = read()
    verifying = sum(t["status"] == "verifying" or bool(t.get("dispatch")) for t in state["tickets"].values())
    for tid, t in state["tickets"].items():
        # Do not prepare receipts against an epoch whose integration writer is
        # already testing a union. Queue work for the next epoch instead.
        if state.get("integration"):
            break
        if room <= 0 or verifying >= 4:
            break
        if t["status"] != "queued" or t.get("dispatch") or t.get("retry_after", 0) > at:
            continue
        nonce = uuid.uuid4().hex[:12]
        def admit_gate(s):
            t = s["tickets"][tid]
            if t["status"] != "queued" or t.get("dispatch"):
                return False
            t["dispatch"] = {"nonce": nonce, "at": at}
            return True
        if update(admit_gate, "reserve preflight " + tid):
            dispatch("fleet-verify.yml", {"ticket": tid, "nonce": nonce})
            room -= 1
            verifying += 1
    state = read()
    for uid in core.select(state, 20 - room, at=at, unavailable_units=legacy_units):
        if room <= 0:
            break
        nonce = uuid.uuid4().hex[:12]
        def admit_worker(s):
            u = s["units"][uid]
            if u.get("dispatch") or uid in s["leases"] or u["status"] not in {"ready", "repair"}:
                return False
            u["dispatch"] = {"nonce": nonce, "at": at}
            return True
        if update(admit_worker, "reserve domain dispatch " + uid):
            dispatch("fleet-worker.yml", {"unit": uid, "nonce": nonce})
            room -= 1
    print(json.dumps(core.dashboard(read())))


def author_environment():
    env = os.environ.copy()
    # Credential is inherited only by trusted wrapper Git processes. It never
    # appears in the remote URL, command arguments or model environment.
    env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
               GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + base64.b64encode(
                   ("x-access-token:" + os.environ["GH_TOKEN"]).encode()).decode())
    return env


def private_record(path, value):
    enabled()
    STORE.update(lambda _: None, "autonomy v2: private diagnostics", extra={path: value})


class Pulse:
    def __init__(self, uid, owner):
        self.uid, self.owner = uid, owner
        self.stop = threading.Event()
        self.lost = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def loop(self):
        while not self.stop.wait(300):
            try:
                def renew(s):
                    lease = s["leases"].get(self.uid)
                    if not lease or lease["owner"] != self.owner:
                        raise ValueError("Lease lost")
                    lease["heartbeat"] = time.time()
                update(renew, "renew domain reservation")
            except Exception:
                self.lost.set()
                return
            try:
                wake_control()
            except Exception:
                # A scheduler API outage does not invalidate a renewed lease.
                pass

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=65)


def run_model(prompt, directory, pulse, minutes, checkpoint=None):
    env = os.environ.copy()
    env["PWD"] = str(directory)
    for key in tuple(env):
        if key in {"GH_TOKEN", "GITHUB_TOKEN", "T", "PAT", "OPENCODE_API_KEY", "SUBSYS_TOKEN"} or key.startswith(("GIT_CONFIG_", "ACTIONS_")):
            env.pop(key, None)
    path = Path(os.environ["RUNNER_TEMP"]) / "model-private.log"
    start = time.monotonic()
    last_checkpoint = start
    with path.open("wb") as log:
        process = subprocess.Popen(["opencode", "run", "--dir", str(directory), "-m", core.MODEL, prompt], cwd=directory,
                                   env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while process.poll() is None:
                elapsed = time.monotonic() - start
                if pulse.lost.is_set() or elapsed > minutes * 60:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                    raise RuntimeError("Model stopped at its time or reservation boundary")
                if checkpoint and time.monotonic() - last_checkpoint > 1200:
                    checkpoint()
                    last_checkpoint = time.monotonic()
                time.sleep(5)
            text = path.read_text(errors="replace")
            private_record(f"autonomy/logs/{RUN}-{uuid.uuid4().hex[:8]}.txt", text)
            lowered = text.lower()
            if "permission requested:" in lowered and "auto-rejecting" in lowered:
                raise PermissionError("OpenCode refused tool access; no automatic retry")
            if any(s in lowered for s in ("abusive traffic from this ip", "free models aren't available from your current network")):
                raise PermissionError("Provider access refusal")
            if any(s in lowered for s in ("invalid api key", "insufficient quota", "rate limit exceeded",
                                          "model not found", "payment required", "authentication failed")):
                raise ProviderError("Provider unavailable")
            if process.returncode:
                raise RuntimeError("Model process failed")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def external_resources(uid):
    # Legacy PRs are finite packages to adopt/repair; heavy-owner claims alone
    # do not reserve a domain. Participants can use the same lease registry.
    state = read()
    our_prs = {t["pr"] for t in state["tickets"].values() if t["unit"] == uid}
    u = state["units"][uid]
    if u.get("wip") and u["wip"].get("pr"):
        our_prs.add(u["wip"]["pr"])
    from fleet import board_claims
    result = board_claims(fs.file_text("docs/AGENT_BOARD.md"))
    for p in fs.paged(f"repos/{fs.PROJECT}/pulls?state=open&base=clean/integration"):
        if p["number"] not in our_prs:
            result.extend(f["filename"] for f in fs.paged(f"repos/{fs.PROJECT}/pulls/{p['number']}/files"))
    return sorted(set(result))


def setup_worker_repo(u):
    git("remote", "set-url", "origin", f"https://github.com/{fs.PROJECT}.git")
    deepen = ["--unshallow"] if git("rev-parse", "--is-shallow-repository") == "true" else []
    command(["git", "fetch", "-q", *deepen, "origin", "+refs/heads/clean/integration:refs/remotes/origin/clean/integration"],
            env=author_environment())
    remote = command(["git", "ls-remote", "origin", "refs/heads/" + u["branch"]], env=author_environment()).stdout.strip()
    if remote:
        command(["git", "fetch", "-q", "origin", u["branch"]], env=author_environment())
        git("switch", "-C", u["branch"], "FETCH_HEAD")
    else:
        git("switch", "-c", u["branch"], "origin/clean/integration")
    return git("rev-parse", "origin/clean/integration")


def setup_research():
    root = Path("/opt/s5/siedler-forschung")
    if not root.exists():
        command(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", "--depth=1",
                 "https://github.com/Kofi55533/siedler-forschung.git", root], cwd=root.parent,
                env=author_environment(), timeout=1200)
        command(["git", "sparse-checkout", "set", "cloud-re/results/rust_handoff/source"], cwd=root,
                env=author_environment())
        command(["git", "checkout", "-q"], cwd=root, env=author_environment(), timeout=1200)
    # OpenCode's existing workspace boundary stays unchanged. Materialize the
    # explicitly authorized native input inside that workspace, rather than
    # asking it to read the previously denied external runner-temp directory.
    inputs = worker_inputs()
    native = inputs / "native"
    if not native.exists():
        shutil.copytree(root / "cloud-re/results/rust_handoff/source", native)
    os.environ["S5_NATIVE_SOURCE"] = str(native)


def worker_inputs():
    inputs = REPO / ".fleet-input"
    inputs.mkdir(exist_ok=True)
    exclude = Path(git("rev-parse", "--git-path", "info/exclude"))
    if not exclude.is_absolute():
        exclude = REPO / exclude
    existing = exclude.read_text() if exclude.exists() else ""
    if "/.fleet-input/" not in existing.splitlines():
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(existing + "\n/.fleet-input/\n")
    return inputs


def scope_files(base, resources):
    git("add", "-N", ".")
    files = git("diff", "--name-only", base).splitlines()
    bad = core.check_files(files, resources)
    if bad:
        raise ValueError("Write outside reserved scope")
    # Check item leases at changed-line granularity, using the existing parser.
    spec = importlib.util.spec_from_file_location("lane_claims", HERE / "lane-claims.py")
    lanes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lanes)
    diff = git("diff", "--unified=0", base)
    for file in files:
        claims = [r.partition("::")[2] for r in resources if r.partition("::")[0] == file]
        if "" in claims:
            continue
        before = git("show", base + ":" + file, check=False)
        after = (REPO / file).read_text() if (REPO / file).exists() else ""
        oldlines, newlines = lanes.changed_lines(diff, file)
        for text, lines in ((before, oldlines), (after, newlines)):
            ranges = [p for name in claims for p in lanes.item_ranges(text, name)]
            if any(not any(lo <= n <= hi for lo, hi in ranges) for n in lines):
                raise ValueError("Write outside reserved Rust item")
    return files


def checkpoint(u, base, resources, body, ready=False):
    files = scope_files(base, resources)
    if not files:
        return None
    patch = git("diff", "--binary", base)
    if any(os.environ[k] in patch for k in ("GH_TOKEN", "OPENCODE_API_KEY") if os.environ.get(k)):
        raise ValueError("Credential in proposed changes")
    git("add", "--", *files)
    if command(["git", "diff", "--cached", "--quiet"], check=False).returncode:
        git("commit", "-m", f"fleet: {u['id']} {'verified package' if ready else 'recoverable checkpoint'}")
    head = git("rev-parse", "HEAD")
    command(["git", "push", "-q", "origin", f"HEAD:refs/heads/{u['branch']}"], env=author_environment())
    existing = fs.api(f"repos/{fs.PROJECT}/pulls?state=open&head=" + urllib.parse.quote("Kofi55533:" + u["branch"], safe=""))
    title = f"Subsystem {u['id']}: {u.get('plan', {}).get('goal', u['title'])}"[:180]
    if existing:
        pr = fs.api(f"repos/{fs.PROJECT}/pulls/{existing[0]['number']}", "PATCH", {"body": body, "title": title})
    else:
        pr = fs.api(f"repos/{fs.PROJECT}/pulls", "POST", {"title": title, "head": u["branch"],
                    "base": fs.BASE, "draft": True, "body": body})
    wip = {"head": head, "pr": pr["number"], "base": base}
    def save(s):
        if s["leases"].get(u["id"], {}).get("owner") != OWNER:
            raise ValueError("Checkpoint lost reservation")
        s["units"][u["id"]]["wip"] = wip
    update(save, "checkpoint package")
    return wip


def worker(uid, nonce):
    enabled()
    # Include setup time, retain fifteen minutes for checkpoint/push/handover.
    job_started = float(os.environ.get("FLEET_JOB_STARTED", time.time()))
    deadline = time.monotonic() + max(0, 285 * 60 - (time.time() - job_started))
    def acquire(s):
        u = s["units"][uid]
        if u.get("dispatch", {}).get("nonce") != nonce:
            return None
        if not core.claim_unit(s, uid, OWNER, RUN, time.time()):
            return None
        u.pop("dispatch", None)
        return copy.deepcopy(u)
    u = update(acquire, "claim domain " + uid)
    failed = False
    while u:
        if time.monotonic() >= deadline:
            result = {"outcome": "checkpoint", "summary": "Job budget reached; saved work remains recoverable"}
        else:
            result = worker_package(u, deadline)
        current = u["id"]
        failed |= result["outcome"] in {"access_refusal", "provider_error", "setup_error"}
        def complete(s):
            core.finish(s, current, OWNER, result, time.time())
            if result["outcome"] in {"access_refusal", "provider_error", "setup_error"}:
                return None
            if deadline - time.monotonic() < 25 * 60:
                return None
            return core.continue_domain(s, current, OWNER, RUN, time.time())
        u = update(complete, "save package and take next in domain")
        if result["outcome"] == "ready":
            try:
                wake_control(soon=True)
            except Exception:
                # The package is durable; heartbeat/completion will retry.
                pass
    print("Domain packages saved privately; runner handover complete.")
    if failed:
        raise RuntimeError("Worker startup/provider failure recorded privately; prior packages preserved")


def worker_package(u, deadline):
    uid = u["id"]
    def allowance(requested):
        minutes = min(requested, (deadline - time.monotonic()) / 60)
        if minutes <= 0:
            raise TimeoutError("Job checkpoint boundary")
        return minutes
    result = {"outcome": "setup_error", "summary": "Worker did not complete setup"}
    try:
        git("config", "user.name", "siedler-fleet")
        git("config", "user.email", "Kofi55533@users.noreply.github.com")
        base = setup_worker_repo(u)
        setup_research()
        ancestor = git("merge-base", base, "HEAD")
        inherited = git("diff", "--name-only", ancestor, "HEAD").splitlines()
        premerge = command(["git", "merge-tree", "--write-tree", "--name-only", "--no-messages", base, "HEAD"], check=False)
        if premerge.returncode not in (0, 1):
            raise ValueError("Cannot inspect existing package merge")
        conflicts = [p for p in premerge.stdout.splitlines()[1:] if p.strip()] if premerge.returncode == 1 else []
        inherited = sorted(set(inherited + conflicts))
        scratch = worker_inputs() / ("domain-" + uid)
        scratch.mkdir(exist_ok=True)
        if u.get("prior_package") and u.get("parent_overview"):
            # The predecessor PR may still be gating, so its written overview
            # is not necessarily on clean/integration. Carry it as read input.
            previous = u["prior_package"]["head"]
            command(["git", "fetch", "-q", "origin", previous], env=author_environment())
            overview = git("show", previous + ":" + u["parent_overview"], check=False)
            context = scratch / "previous-overview.md"
            context.write_text(overview)
            u["previous_overview_input"] = str(context)
        (scratch / "assignment.json").write_text(json.dumps(u, indent=2))
        plan_file, result_file = scratch / "plan.json", scratch / "result.json"
        for path in (plan_file, result_file, scratch / "pr-body.md"):
            path.unlink(missing_ok=True)
        resources = external_resources(uid)
        with Pulse(uid, OWNER) as pulse:
            plan = None
            for attempt in range(3):
                prompt = (HERE.parent / "prompts/domain-plan.md").read_text()
                prompt += f"\nAssignment: {scratch / 'assignment.json'}\nWrite plan: {plan_file}\nReserved resources: {json.dumps(resources)}"
                prompt += f"\nExisting WIP files (must be retained and repaired): {json.dumps(inherited)}"
                prompt += f"\nRemaining runner minutes: {int((deadline - time.monotonic()) / 60)}. The wrapper takes another package after this one."
                run_model(prompt, REPO, pulse, allowance(15))
                if git("status", "--porcelain"):
                    raise ValueError("Planning phase wrote source before a reservation")
                plan = json.loads(plan_file.read_text())
                plan["resources"] = sorted(set(plan.get("resources", []) + inherited))
                plan = core.validate_plan(plan, u)
                resources = sorted(set(resources + external_resources(uid)))
                conflicts = update(lambda s: core.reserve(s, uid, OWNER, plan, time.time(), resources), "reserve package scope")
                if not conflicts:
                    break
                resources = sorted(set(resources + conflicts))
            else:
                result = {"outcome": "stuck", "summary": "Three plans collided; next attempt selects a different native family"}
                return result
            u["plan"] = plan
            (scratch / "plan.json").write_text(json.dumps(plan, indent=2))
            # Rebase repair is owned by the same DeepSeek run. The model gets
            # the actual conflicts after reserving all affected files; it never
            # waits for a heavy porter and never uses a blanket ours/theirs.
            merged = command(["git", "merge", "--no-edit", "origin/clean/integration"], check=False)
            if merged.returncode:
                repair = ("Resolve the current Git merge conflicts semantically against the native reference. "
                          "Do not commit, push, change remotes, use -X ours/theirs or edit outside the reserved files. "
                          "Retain both existing integration behavior and the WIP package; run its focused tests. "
                          f"Plan: {plan_file}. Original source: {os.environ['S5_NATIVE_SOURCE']}.")
                run_model(repair, REPO, pulse, allowance(35))
                files = scope_files(base, plan["resources"])
                git("add", "--", *files)
                if git("diff", "--name-only", "--diff-filter=U"):
                    raise ValueError("Merge repair still has unresolved paths")
                git("commit", "--no-edit")
            prompt = (HERE.parent / "prompts/domain-worker.md").read_text()
            prompt += f"\nAssignment: {scratch / 'assignment.json'}\nPlan: {plan_file}\nResult: {result_file}\nOverview: {u['overview']}"
            prompt += "\nFinish this coherent package promptly. The wrapper immediately submits its own PR, then replans the next package in this domain."
            body = "Recoverable domain checkpoint; not ready for integration.\n\n" + plan["goal"]
            def save():
                checkpoint(u, base, plan["resources"], body)
            result = {"outcome": "checkpoint", "summary": "Timed attempt; continue the private overview and saved branch"}
            try:
                run_model(prompt, REPO, pulse, allowance(min(170, plan["minutes"] + 20)), checkpoint=save)
            finally:
                # Preserve productive work on timeout/provider failure too.
                save()
            result = json.loads(result_file.read_text())
            if result.get("outcome") == "ready":
                body_file = scratch / "pr-body.md"
                if not body_file.exists():
                    raise ValueError("Ready result needs a reviewable private PR body")
                wip = checkpoint(u, base, plan["resources"], body_file.read_text(), ready=True)
                if not wip:
                    raise ValueError("Ready result has no code or evidence delta")
                result.update(pr=wip["pr"], head=wip["head"], checkpoint=wip)
    except PermissionError:
        result = {"outcome": "access_refusal", "summary": "Provider refused access; hold without IP rotation or refusal bypass"}
    except ProviderError:
        result = {"outcome": "provider_error", "summary": "Provider unavailable; preserve WIP and apply measured backoff"}
    except Exception as error:
        private_record(f"autonomy/errors/{RUN}.txt", type(error).__name__ + ": " + str(error))
        result["summary"] += "; " + type(error).__name__
    return result


def gate_environment(base, head):
    env = author_environment()
    env.update(S5_GATE_PIN_BASE=base, S5_GATE_EXPECT_HEAD=head, S5_GATE_COMMENT="0", S5_AUTONOMOUS="1",
               S5_GATE_BUDGET_MIN="110", S5_LIVE_TIMEOUT="1800", S5_GATE_PARALLEL="4")
    return env


def run_gate(ticket, base, extra_heads=(), extra_fixtures=()):
    env = gate_environment(base, ticket["head"])
    env["S5_GATE_EXTRA_HEADS"] = " ".join(extra_heads)
    env["S5_GATE_EXTRA_FIXTURES"] = " ".join(extra_fixtures)
    # The gate writes full output only to private diagnostics, never Actions.
    r = command(["bash", HERE / "s5-gate.sh", str(ticket["pr"]), "--dry-run"],
                env=env, timeout=7000, check=False)
    private_record(f"autonomy/gates/{RUN}-{ticket['id']}.txt", r.stdout + r.stderr)
    if r.returncode == 0:
        receipt = json.loads((Path("/opt/s5/gate") / ticket["pr"].__str__() / "receipt.json").read_text())
        receipt.update(scripts=contract.script_digest(HERE), measured_at=time.time())
        return "verified", receipt
    if r.returncode == 3:
        return "queued", {"error": "Infrastructure/no measurement; no correctness verdict"}
    return "repair", {"error": "\n".join(x for x in r.stdout.splitlines() if "GATE FAIL:" in x)[-3000:]}


def preflight(tid, nonce):
    enabled()
    def acquire(s):
        t = s["tickets"][tid]
        if t.get("dispatch", {}).get("nonce") != nonce or t["status"] != "queued":
            return None
        t.pop("dispatch", None)
        t.update(status="verifying", run=RUN, attempts=t["attempts"] + 1)
        return copy.deepcopy(t)
    ticket = update(acquire, "claim preflight")
    if not ticket:
        return
    git("remote", "set-url", "origin", f"https://github.com/{fs.PROJECT}.git")
    base = fs.api(f"repos/{fs.PROJECT}/git/ref/heads/{fs.BASE}")["object"]["sha"]
    try:
        status, receipt = run_gate(ticket, base)
    except Exception:
        status, receipt = "queued", {"error": "Interrupted preflight; retry without a verdict"}
    def save(s):
        t = s["tickets"][tid]
        if t.get("run") != RUN or t["status"] != "verifying":
            raise ValueError("Stale preflight")
        t.update(status=status, receipt=receipt, retry_after=time.time() + 900, run=None)
        if status == "repair":
            u = s["units"][t["unit"]]
            u.update(status="repair", wip={"pr": t["pr"], "head": t["head"], "gate": receipt})
            s["leases"].pop(u["id"], None)
    update(save, "record typed preflight")
    print("Preflight saved privately: " + status)


def verify_current_claims(base, candidate):
    """Recheck the entire union against the newest board before the Git CAS.

    The first ticket's shell preflight cannot cover a newly posted claim on a
    different ticket. Omit the older-PR exception here (a conservative hold).
    """
    scratch = Path(os.environ["RUNNER_TEMP"])
    board, files, diff = (scratch / name for name in ("union-board.md", "union-files.txt", "union.diff"))
    board.write_text(git("show", base + ":docs/AGENT_BOARD.md"))
    files.write_text(git("diff", "--name-only", base, candidate) + "\n")
    diff.write_text(git("diff", "--unified=0", base, candidate))
    heartbeat = command(["bash", HERE / "heartbeat.sh", "fetch", scratch / "union-heartbeat.json"]).stdout.split()
    result = command([sys.executable, HERE / "lane-claims.py", board, files,
                      "--owners", "Opus,Codex", "--hours", "6", "--repo", REPO,
                      "--diff", diff, "--old-rev", base, "--new-rev", candidate, *heartbeat])
    if result.stdout.strip():
        raise ValueError("Fresh lane claim on combined candidate; replan other domain work")


def integrate(nonce):
    enabled()
    def acquire(s):
        lease = s["integration"]
        if not lease or lease["nonce"] != nonce or lease["run"]:
            return None
        lease["run"] = RUN
        return [copy.deepcopy(s["tickets"][tid]) for tid in lease["tickets"]]
    tickets = update(acquire, "claim integration batch")
    if not tickets:
        return
    tids = [t["id"] for t in tickets]
    union_failed = False
    single_failure = None
    pushed = False
    try:
        git("remote", "set-url", "origin", f"https://github.com/{fs.PROJECT}.git")
        base = tickets[0]["receipt"]["base"]
        fixtures = sorted({f for t in tickets for f in t["receipt"]["fixtures"]})
        for t in tickets:
            p = fs.api(f"repos/{fs.PROJECT}/pulls/{t['pr']}")
            if (p["state"], p["base"]["ref"], p["head"]["sha"]) != ("open", fs.BASE, t["head"]):
                raise ValueError("Batch PR head or target changed")
            if t["receipt"]["scripts"] != contract.script_digest(HERE) or t["receipt"]["base"] != base:
                raise ValueError("Batch verification inputs differ")
            command(["git", "fetch", "-q", "origin", t["head"], base], env=author_environment())
            # Install every required private fixture on this new runner before
            # the combined matrix. No missing fixture can become a skipped row.
            body_file = Path(os.environ["RUNNER_TEMP"]) / f"pr-{t['pr']}.txt"
            body_file.write_text(p["body"] or "")
            for f in contract.fixtures(p["body"] or ""):
                command(["bash", HERE / "s5-fixture-install.sh", f, body_file, t["head"]], env=author_environment())
        # A single package still re-measures on this runner: fixtures and the
        # compiled native toolchain must be the same as its signed-off inputs.
        status, combined = run_gate(tickets[0], base, [t["head"] for t in tickets[1:]], fixtures)
        if status != "verified":
            union_failed = status == "repair" and len(tickets) > 1
            single_failure = combined if status == "repair" and len(tickets) == 1 else None
            raise ValueError("Combined candidate did not pass every gate")
        if not combined.get("data_release") or any(
                t["receipt"].get("data_release") != combined["data_release"] for t in tickets):
            raise ValueError("Private reference data changed since individual preflights")
        # All selected native differential suites are repeated on the union.
        git("checkout", "--detach", combined["commit"])
        for t in tickets:
            args = t["receipt"].get("differential_tests", "").split()
            if args:
                if any(not re.fullmatch(r"[A-Za-z0-9_:\-]+", a) for a in args):
                    raise ValueError("Invalid recorded differential-test selection")
                lock = REPO / "crates/s5xcheck/Cargo.lock"
                copied = not lock.exists()
                if copied:
                    lock.write_bytes((REPO / "recomp/Cargo.lock").read_bytes())
                try:
                    r = command(["cargo", "test", "--release", "-j", "2", "--manifest-path",
                                 "crates/s5xcheck/Cargo.toml", *args], env={**author_environment(),
                                 "CARGO_TARGET_DIR": str(REPO / "recomp/target")}, timeout=3600)
                    if not re.search(r"test result: ok\. [1-9][0-9]* passed; 0 failed", r.stdout):
                        raise ValueError("Combined differential suite has no passing tests")
                finally:
                    if copied:
                        lock.unlink(missing_ok=True)
        # Exact Git fast-forward is the base compare-and-swap GitHub's squash
        # API lacks. The commit retains every tested PR head as an ancestor;
        # GitHub can mark those PRs merged, and attribution stays recoverable.
        latest = fs.api(f"repos/{fs.PROJECT}/git/ref/heads/{fs.BASE}")["object"]["sha"]
        command(["git", "fetch", "-q", "origin", latest], env=author_environment())
        candidate = combined["commit"]
        if latest != base:
            if contract.input_tree(REPO, latest) != contract.input_tree(REPO, base):
                raise ValueError("Base code changed; requeue exact preflights")
            git("checkout", "--detach", latest)
            git("-c", "user.name=siedler-fleet", "-c", "user.email=fleet@users.noreply.github.com",
                "merge", "--no-edit", "--no-ff", candidate)
            if contract.input_tree(REPO, "HEAD") != contract.input_tree(REPO, candidate):
                raise ValueError("Rebuilt documentation merge changed verification input")
            candidate = git("rev-parse", "HEAD")
        verify_current_claims(latest, candidate)
        def intent(s):
            lease = s["integration"]
            if not lease or lease["nonce"] != nonce or lease["run"] != RUN:
                raise ValueError("Integration reservation lost")
            lease.update(candidate=candidate, combined=combined)
        update(intent, "record tested integration intent before Git CAS")
        command(["git", "push", "-q", "origin", candidate + ":refs/heads/" + fs.BASE], env=author_environment())
        pushed = True
        update(lambda s: core.accept(s, tids, candidate, time.time()), "accept exact combined tree")
        private_record(f"autonomy/accepted/{candidate}.json", json.dumps({"tickets": tids, "combined": combined}))
        print(f"Accepted {len(tickets)} packages; combined full matrix green.")
    except Exception as error:
        if pushed:
            # Preserve the durable intent. A later controller confirms the
            # commit's ancestry and records acceptance without reapplying it.
            print("Integration push succeeded; private acceptance will be reconciled.")
            return
        private_record(f"autonomy/integration/{RUN}.txt", type(error).__name__ + ": " + str(error))
        def retry(s):
            if union_failed:
                # A measured interaction failure is not a reason to retry the
                # same union forever. Integrate its independently green parts
                # separately, then let the remaining owner repair the partner.
                import itertools
                s.setdefault("incompatible_batches", []).extend(list(p) for p in itertools.combinations(tids, 2))
            for tid in tids:
                if s["tickets"][tid]["status"] != "accepted":
                    t = s["tickets"][tid]
                    t.update(status="verified" if union_failed else "queued", retry_after=time.time() + 300)
                    if single_failure:
                        t.update(status="repair", receipt=single_failure)
                        u = s["units"][t["unit"]]
                        u.update(status="repair", wip={"pr": t["pr"], "head": t["head"], "gate": single_failure})
                        s["leases"].pop(u["id"], None)
            s["integration"] = None
        update(retry, "requeue batch without a merge verdict")
        print("Batch deferred; branches and evidence preserved.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("shadow", "control", "worker", "verify", "integrate"))
    parser.add_argument("--unit")
    parser.add_argument("--ticket")
    parser.add_argument("--nonce")
    args = parser.parse_args()
    if args.mode == "shadow":
        control(shadow=True)
    else:
        enabled()
        if args.mode == "control":
            control()
        elif args.mode == "worker":
            worker(args.unit, args.nonce)
        elif args.mode == "verify":
            preflight(args.ticket, args.nonce)
        else:
            integrate(args.nonce)


if __name__ == "__main__":
    main()
