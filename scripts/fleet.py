#!/usr/bin/env python3
"""Shared fleet controller. Never print private prompts, model output or git errors."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from zoneinfo import ZoneInfo

from fleet_state import (ApiError, BASE, CONTROL, HARD_PATHS, LEASE_SECONDS, LIVE_STATES,
                         MODEL, POOLS, PROJECT, Store, acquire, allowed, api, available,
                         claim, enqueue, event, file_text, paged, release, renew,
                         resource_conflict, safe_error, stamp)

SCRIPTS = Path(__file__).resolve().parent
POOL = os.environ.get("GITHUB_REPOSITORY", POOLS[1])
RUN_ID = os.environ.get("GITHUB_RUN_ID", "local-" + uuid.uuid4().hex[:8])
OWNER = f"{POOL}:{RUN_ID}:{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
RUN_URL = f"https://github.com/{POOL}/actions/runs/{RUN_ID}"
STORE = Store()


def command(arguments, cwd=None, env=None, timeout=1800, output=None):
    result = subprocess.run(arguments, cwd=cwd, env=env, timeout=timeout,
                            stdout=output or subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode})")
    return result.stdout or b""


def git(repo, *args):
    return command(["git", "-C", str(repo), *args]).decode().strip()


def redact(text):
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "T", "OPENCODE_API_KEY"):
        if os.environ.get(name):
            text = text.replace(os.environ[name], "[REDACTED]")
    return text


def private_log(name, content):
    STORE.update(lambda s: None, "private run diagnostics",
                 extra={f"fleet/logs/{RUN_ID}-{name}.txt": redact(content)[-100000:]})


def active_runs(pool):
    runs = []
    for status in ("in_progress", "queued", "waiting", "pending", "requested"):
        runs.extend(paged(f"repos/{pool}/actions/runs?status={status}", "workflow_runs", pages=3))
    return list({r["id"]: r for r in runs}.values())


def board_claims(board, include_legacy=False):
    paths = []
    now = datetime.now(ZoneInfo("Europe/Berlin"))
    owners = "Opus|Codex|DeepSeek|Muse" if include_legacy else "Opus|Codex"
    pattern = rf"LANE-CLAIM\s+(?:{owners})\s+(\d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}})\s+(.+)"
    for match in re.finditer(pattern, board):
        when = datetime.strptime(match[1], "%Y-%m-%d %H:%M").replace(tzinfo=now.tzinfo)
        if now - timedelta(hours=6) <= when <= now + timedelta(minutes=5):
            paths.extend(re.findall(r"(?:crates|recomp|oracle|tools|docs)/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+",
                                    match[2].split(" -- ", 1)[0]))
    return sorted(set(paths))


def observe(state):
    all_runs = {pool: active_runs(pool) for pool in POOLS}
    legacy_names = {"opencode.yml", "gate.yml", "muse-gate.yml"}
    cutover = state["config"].get("cutover_at", "9999")
    legacy = []
    for pool, runs in all_runs.items():
        for run in runs:
            name = run["path"].rsplit("/", 1)[-1].split("@", 1)[0]
            if name in legacy_names and (name != "opencode.yml" or run["created_at"] < cutover):
                legacy.append({"pool": pool, "run_id": run["id"], "url": run["html_url"]})
    previous = {p["number"]: p for p in state.get("observed", {}).get("open_prs", [])}
    prs = []
    for pr in paged(f"repos/{PROJECT}/pulls?state=open&base=" + urllib.parse.quote(BASE, safe="")):
        old = previous.get(pr["number"], {})
        if old.get("head") == pr["head"]["sha"]:
            paths = old["paths"]
        else:
            files = paged(f"repos/{PROJECT}/pulls/{pr['number']}/files")
            paths = sorted({p for f in files for p in (f["filename"], f.get("previous_filename")) if p})
        prs.append({"number": pr["number"], "head": pr["head"]["sha"],
                    "branch": pr["head"]["ref"], "title": pr["title"], "paths": paths})
    board = file_text("docs/AGENT_BOARD.md")
    observed = {"at": time.time(), "legacy_active": legacy, "open_prs": prs,
                "board_paths": board_claims(board, include_legacy=bool(legacy)),
                "pools": {pool: {"running": sum(r["status"] == "in_progress" and
                                                 r["path"].endswith("fleet-worker.yml") for r in runs),
                                  "queued": sum(r["status"] != "in_progress" and
                                                r["path"].endswith("fleet-worker.yml") for r in runs)}
                          for pool, runs in all_runs.items()}}
    return observed, all_runs


def dispatch(workflow, inputs=None, pool=POOL):
    api(f"repos/{pool}/actions/workflows/{workflow}/dispatches", "POST", {
        "ref": "main", "inputs": inputs or {}})


def reconcile(state, all_runs):
    active = {(pool, str(r["id"])) for pool, runs in all_runs.items() for r in runs}
    branches = {p["branch"]: p for p in state.get("observed", {}).get("open_prs", [])}
    for task in state["tasks"].values():
        # Recover the narrow crash window between creating a PR and recording it.
        if task.get("branch") in branches and not task.get("pr"):
            pr = branches[task["branch"]]
            task.update(pr=pr["number"], head=pr["head"],
                        pr_url=f"https://github.com/{PROJECT}/pull/{pr['number']}",
                        status="review", updated=stamp())
    for task in state["tasks"].values():
        if task["status"] not in {"running", "gating"}:
            continue
        lease = task.get("lease", {})
        if lease.get("until", 0) > time.time():
            continue
        identity = (lease.get("pool"), str(lease.get("run_id")))
        # Never recycle a reservation just because a heartbeat was delayed.
        if identity in active:
            continue
        if not identity[0] or not identity[1]:
            task.update(status="blocked", summary="Unvollständige Reservierung; manuell prüfen.", updated=stamp())
            continue
        run = api(f"repos/{identity[0]}/actions/runs/{identity[1]}")
        if run["status"] != "completed":
            continue
        if task.get("pr"):
            task.update(status="review", updated=stamp())
        elif task["attempts"] < state["config"]["max_attempts"]:
            task.update(status="queued", not_before=time.time() + 900, updated=stamp(),
                        summary="Abgebrochener Lauf wird mit neuer Reservierung wieder aufgenommen.")
        else:
            task.update(status="blocked", updated=stamp(), summary="Zwei abgebrochene Versuche; Planer muss die Aufgabe neu zerlegen.")
        event(state, "recovered", task=task["id"], status=task["status"])
    # Reconcile merges/closures made by other contributors without scheduling duplicates.
    open_numbers = {p["number"] for p in state.get("observed", {}).get("open_prs", [])}
    for task in state["tasks"].values():
        if task["status"] not in {"review", "gating"} or task.get("pr") in open_numbers or not task.get("pr"):
            continue
        pr = api(f"repos/{PROJECT}/pulls/{task['pr']}")
        if pr["merged"]:
            task.update(status="done", updated=stamp(), merged_commit=pr["merge_commit_sha"])
            event(state, "merged", task=task["id"], pr=task["pr"])
        elif pr["state"] == "closed":
            task.update(status="blocked", updated=stamp(), summary="PR wurde extern geschlossen; Auftrag wird neu eingeplant.")


def control():
    if not STORE.update(lambda s: acquire(s, "controller", OWNER, 240), "controller reservation"):
        return
    try:
        state = STORE.read()[0]
        observed, runs = observe(state)
        def refresh(s):
            s["observed"] = observed
            reconcile(s, runs)
        STORE.update(refresh, "refresh shared reservations")
        state = STORE.read()[0]
        config = state["config"]
        if not config["enabled"]:
            print("Fleet paused; private status refreshed.")
            return
        if observed["legacy_active"]:
            print("Fleet migration: code workers drain; new workers may investigate unreserved source modules read-only.")
        if state["providers"].get(POOL, {}).get("blocked_until", 0) > time.time():
            print("Model pool is cooling down after provider errors.")
            return
        # Promote only after integrated code results, never after empty/diagnostic
        # jobs. Provider failures and repeated worker failures stop promotion.
        merged = sum(t["status"] == "done" and bool(t.get("merged_commit")) for t in state["tasks"].values())
        failures = sum(t["status"] == "blocked" for t in state["tasks"].values())
        if config.get("auto_scale") and failures < 3 and not any(p.get("errors", 0) for p in state["providers"].values()):
            limit = 40 if merged >= 6 else 20 if merged >= 4 else 8 if merged >= 2 else config["rollout_limit"]
            if limit > config["rollout_limit"]:
                STORE.update(lambda s: s["config"].update(rollout_limit=limit), "promote successful rollout")
                config["rollout_limit"] = limit
        ready = len(available(state))
        planner_active = any(r["path"].endswith("fleet-plan.yml") for pool in runs.values() for r in pool)
        if (ready < config["queue_low_water"] and not planner_active and
                time.time() > state.get("next_plan_at", 0)):
            # Reserve before dispatch; a failed API request releases the reservation.
            slot = "planner-dispatch"
            if STORE.update(lambda s: acquire(s, slot, OWNER, 600), "reserve planner dispatch"):
                try:
                    dispatch("fleet-plan.yml")
                except Exception:
                    STORE.update(lambda s: release(s, slot, OWNER), "release failed planner dispatch")
                    raise
        integration_active = any(r["path"].endswith("fleet-integrate.yml") for pool in runs.values() for r in pool)
        if (any(t["status"] == "review" and t.get("not_before", 0) <= time.time()
                for t in state["tasks"].values()) and not integration_active):
            if STORE.update(lambda s: acquire(s, "integration-dispatch", OWNER, 600), "reserve integration dispatch"):
                try:
                    dispatch("fleet-integrate.yml")
                except Exception:
                    STORE.update(lambda s: release(s, "integration-dispatch", OWNER), "release failed gate dispatch")
                    raise
        pool_active = [r for r in runs[POOL] if r["path"].endswith("fleet-worker.yml")]
        total = sum(r["path"].endswith("fleet-worker.yml") for values in runs.values() for r in values)
        cap = min(config["max_active"], config["rollout_limit"])
        old_pool = sum(r["pool"] == POOL for r in observed["legacy_active"])
        count = max(0, min(config["pools"][POOL] - len(pool_active) - old_pool,
                           cap - total - len(observed["legacy_active"]), ready))
        # Rate limit the burst; completion/scheduled events fill the remaining capacity.
        for _ in range(min(count, 10)):
            dispatch("fleet-worker.yml")
        print(f"Shared queue refreshed; worker dispatches: {min(count, 10)}.")
    finally:
        STORE.update(lambda s: release(s, "controller", OWNER), "release controller")


class Heartbeat:
    def __init__(self, task=None, lease=None):
        self.task, self.lease = task, lease
        self.stop = threading.Event()
        self.lost = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self.stop.wait(300):
            try:
                ok = STORE.update(
                    (lambda s: renew(s, self.task, OWNER)) if self.task else
                    (lambda s: acquire(s, self.lease, OWNER, LEASE_SECONDS)), "renew reservation")
                if not ok:
                    self.lost.set()
                    return
            except Exception:
                # Stop the model instead of continuing beyond an unconfirmed reservation.
                self.lost.set()
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=65)


def model_run(directory, prompt, heartbeat, minutes=75):
    if not os.environ.get("OPENCODE_API_KEY", "").strip():
        raise RuntimeError("Missing provider credential")
    env = os.environ.copy()
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "T", "ACTIONS_RUNTIME_TOKEN"):
        env.pop(name, None)
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "true"
    path = Path(os.environ.get("RUNNER_TEMP", ".")) / f"model-{RUN_ID}.log"
    started = time.monotonic()
    with path.open("wb") as log:
        proc = subprocess.Popen(["opencode", "run", "--standalone", "--auto", "--model", MODEL, prompt],
                                cwd=directory, env=env, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            while proc.poll() is None:
                if heartbeat.lost.is_set() or time.monotonic() - started > minutes * 60:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                    raise RuntimeError("Model timed out or lost its reservation")
                time.sleep(5)
            output = path.read_text(errors="replace")
            private_log("model", output)
            lowered = output.lower()
            provider_error = any(x in lowered for x in
                                 ("invalid api key", "unauthorized", "insufficient quota", "rate limit exceeded",
                                  "model not found", "payment required", "authentication failed"))
            if provider_error or proc.returncode:
                def cooldown(s):
                    previous = s["providers"].get(POOL, {}).get("errors", 0)
                    s["providers"][POOL] = {"errors": previous + 1,
                        "blocked_until": time.time() + min(14400, 900 * 2 ** min(previous, 4)),
                        "last_error_run": RUN_URL}
                STORE.update(cooldown, "provider backoff")
                raise RuntimeError("Model failed; private diagnostics saved")
            STORE.update(lambda s: s["providers"].update({POOL: {"errors": 0, "blocked_until": 0,
                                                              "last_success": stamp()}}), "provider accepted run")
            return output
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()


def plan():
    def reserve(s):
        if not acquire(s, "planner", OWNER):
            return False
        s["leases"].pop("planner-dispatch", None)
        return True
    if not STORE.update(reserve, "planner reservation"):
        return
    try:
        state = STORE.read()[0]
        if not state["config"]["enabled"]:
            return
        directory = Path(os.environ["RUNNER_TEMP"]) / "private-plan"
        directory.mkdir(mode=0o700, exist_ok=True)
        paths = ["docs/AGENT_BOARD.md", "docs/PLAN.md", "docs/clean/ARCHITECTURE.md",
                 "docs/clean/OPEN_QUESTIONS.md", "docs/clean/COVERAGE.md", "AGENTS.md"]
        for path in paths:
            text = file_text(path)
            # The newest board entries are at the end. Keep a bounded current snapshot.
            (directory / path.replace("/", "_")).write_text(text[-100000:], encoding="utf-8")
        (directory / "fleet.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tree = api(f"repos/{PROJECT}/git/trees/" + urllib.parse.quote(BASE, safe="") + "?recursive=1")
        if tree.get("truncated"):
            raise RuntimeError("Incomplete source tree; planner did not run")
        source_paths = [x["path"] for x in tree["tree"] if x["type"] == "blob" and
                        x["path"].startswith(("crates/s5clean/", "tools/", "docs/clean/", "docs/agent-queue/"))]
        (directory / "source-paths.json").write_text(json.dumps(source_paths, indent=2), encoding="utf-8")
        prompt = (SCRIPTS.parent / "prompts/fleet-planner.md").read_text(encoding="utf-8")
        with Heartbeat(lease="planner") as heartbeat:
            model_run(directory, prompt, heartbeat, minutes=20)
            if heartbeat.lost.is_set():
                raise RuntimeError("Planner reservation lost")
        values = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
        if not isinstance(values, dict) or not isinstance(values.get("tasks"), list) or len(values["tasks"]) > 24:
            raise ValueError("Planner output must contain at most 24 focused tasks")
        # One malformed proposal must not stop all useful assignments.
        from fleet_state import validate_task
        valid = []
        for value in values["tasks"]:
            try:
                valid.append(validate_task(value))
            except (ValueError, TypeError, AttributeError):
                continue
        def save(s):
            if s["leases"].get("planner", {}).get("owner") != OWNER:
                raise RuntimeError("Planner lease changed")
            ids = enqueue(s, valid)
            discovered = seed_discovery(s, tree["tree"])
            # Fill a thin queue promptly instead of waiting a full planning period.
            s["next_plan_at"] = time.time() + (30 if len(available(s)) < s["config"]["rollout_limit"] and ids
                                               else s["config"]["planner_interval_seconds"])
            s["last_plan"] = {"at": stamp(), "run": RUN_URL, "accepted": len(ids),
                              "discovery_tasks": len(discovered),
                              "summary": str(values.get("summary", ""))[:4000]}
            return len(ids) + len(discovered)
        count = STORE.update(save, "publish validated private tasks")
        print(f"Private plan accepted: {count} focused tasks.")
    finally:
        STORE.update(lambda s: release(s, "planner", OWNER), "release planner")


def seed_discovery(state, tree):
    """When explicit tasks run short, inspect each unchanged source blob once.

    Discovery outputs are private actionable findings, not fabricated code progress.
    Stable blob keys prevent an endless re-audit of unchanged modules.
    """
    if not state["config"].get("discovery_enabled", True):
        return []
    target = state["config"]["rollout_limit"] + 8
    needed = max(0, target - len(available(state)))
    occupied = list(state.get("observed", {}).get("board_paths", []))
    for pr in state.get("observed", {}).get("open_prs", []):
        occupied.extend(pr["paths"])
    for task in state["tasks"].values():
        if task["status"] in {"queued", "running", "review", "gating"}:
            occupied.extend(task["paths"])
    existing = {t["key"] for t in state["tasks"].values()}
    values = []
    prefixes = ("crates/s5clean/src/", "recomp/play/src/", "recomp/audio/src/", "recomp/gfx/src/")
    for item in tree:
        path = item["path"]
        key = f"discover:{path}:{item['sha']}".lower()
        if (len(values) >= needed or item["type"] != "blob" or not path.endswith(".rs") or
                not path.startswith(prefixes) or key in existing or resource_conflict([path], occupied)):
            continue
        values.append({"key": key, "title": "Find the next missing native behavior in " + Path(path).stem,
            "kind": "diagnose", "profile": "source", "paths": [], "topics": ["source-gap:" + path],
            "depends_on": [],
            "prompt": f"Investigate exactly {path} (source blob {item['sha']}) against the native exports and current roadmap. "
                      "Find a concrete missing/stubbed/unwired native behavior or a reachable parity gap that is not already "
                      "being handled by open PRs/board/queue. Read the current board and relevant native JSON selectively. "
                      "Do not modify source, download game assets or run builds. Identify the original native producer/address, "
                      "expected behavior, current Rust gap, smallest implementation scope and a specific scenario to demonstrate it. "
                      "If this source blob has no evidenced gap, say that and give the evidence; do not invent a bug. "
                      "Write only the external private result JSON. The shared planner will turn verified gaps into code tasks.",
            "acceptance": "One evidence-backed next implementation task with native address, concrete expected/current behavior, "
                          "source function and proposed scenario, or a precise evidence-backed finding that this module has no new gap. "
                          "No repeated audit of the same unchanged source blob; no claim of live verification."})
    return enqueue(state, values)


def setup_research(directory):
    env = author_env()
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    command(["git", "clone", "--quiet", "--depth", "1", "--filter=blob:none", "--no-checkout",
             "--single-branch", "https://github.com/Kofi55533/siedler-forschung.git", str(directory)], env=env)
    command(["git", "-C", str(directory), "sparse-checkout", "set", "--no-cone", "/README.md",
             "/cloud-re/results/rust_handoff/source/functions.jsonl",
             "/cloud-re/results/rust_handoff/source/code.jsonl"], env=env)
    command(["git", "-C", str(directory), "checkout", "--quiet", "main"], env=env)


def setup_source():
    directory = Path(os.environ["RUNNER_TEMP"]) / "private-source"
    env = author_env()
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    command(["git", "clone", "--quiet", "--depth", "1", "--filter=blob:none", "--no-checkout",
             "--single-branch", "--branch", BASE, f"https://github.com/{PROJECT}.git", str(directory)], env=env)
    command(["git", "-C", str(directory), "sparse-checkout", "set", "crates", "docs", "tools",
             "recomp/play", "recomp/audio", "recomp/gfx"], env=env)
    command(["git", "-C", str(directory), "checkout", "--quiet", BASE], env=env)
    setup_research(Path(os.environ["RUNNER_TEMP"]) / "private-research")
    return directory


def setup_game(research=True):
    env = os.environ.copy()
    env["T"] = env["GH_TOKEN"]
    path = Path(os.environ["RUNNER_TEMP"]) / "private-setup.log"
    with path.open("wb") as log:
        try:
            command(["bash", str(SCRIPTS / "s5-setup.sh"), BASE, "1"], env=env, timeout=2400, output=log)
        except Exception:
            private_log("setup", path.read_text(errors="replace"))
            raise
    repo = Path("/opt/s5/siedler-sim-rs")
    git(repo, "config", "user.name", "siedler-fleet")
    git(repo, "config", "user.email", "siedler-fleet@users.noreply.github.com")
    if research:
        setup_research(Path("/opt/s5/siedler-forschung"))
    return repo


def author_env():
    # The token is available only for wrapper git operations, never in a remote URL.
    directory = Path(os.environ["RUNNER_TEMP"]) / "fleet-auth"
    directory.mkdir(mode=0o700, exist_ok=True)
    askpass = directory / "askpass.sh"
    askpass.write_text('#!/bin/sh\ncase "$1" in *Username*) printf "%s\\n" x-access-token ;; *) printf "%s\\n" "$GH_TOKEN" ;; esac\n')
    askpass.chmod(0o700)
    env = os.environ.copy()
    env["GIT_ASKPASS"] = str(askpass)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def finish_task(task_id, status, summary, **fields):
    def update(s):
        task = s["tasks"][task_id]
        if task.get("lease", {}).get("owner") != OWNER:
            raise RuntimeError("Worker no longer owns this task")
        task.update(status=status, summary=summary[:6000], updated=stamp(), **fields)
        event(s, status, task=task_id, pr=fields.get("pr"))
    STORE.update(update, "record private task result")


def worker():
    task = STORE.update(lambda s: claim(s, OWNER, POOL, RUN_ID, RUN_URL), "claim task atomically")
    if not task:
        print("No unreserved task; worker exits without using a model or downloading game data.")
        return
    try:
        with Heartbeat(task=task["id"]) as heartbeat:
            source_only = task.get("profile") == "source"
            repo = setup_source() if source_only else setup_game()
            git(repo, "config", "user.name", "siedler-fleet")
            git(repo, "config", "user.email", "siedler-fleet@users.noreply.github.com")
            branch = "agent/fleet-" + task["id"]
            git(repo, "switch", "-c", branch)
            base = git(repo, "rev-parse", "HEAD")
            result_path = Path(os.environ["RUNNER_TEMP"]) / "fleet-result.json"
            prompt = (SCRIPTS.parent / "prompts/fleet-worker.md").read_text(encoding="utf-8")
            prompt += "\n\nAssignment (private):\n" + json.dumps(task, ensure_ascii=False, indent=2)
            prompt += f"\n\nWrite your final JSON result to {result_path}."
            if source_only:
                prompt += ("\nThis assignment uses the SOURCE-ONLY profile. Game data is not installed; do not download it or build. "
                           f"Native exports are at {Path(os.environ['RUNNER_TEMP']) / 'private-research/cloud-re/results/rust_handoff/source'}. "
                           "Use source/native evidence and explicitly distinguish it from live measurements.")
            model_run(repo, prompt, heartbeat, minutes=35 if source_only else 75)
            if heartbeat.lost.is_set():
                raise RuntimeError("Worker reservation lost")
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if not isinstance(result.get("summary"), str) or not result["summary"].strip():
                raise ValueError("Worker did not supply a concrete result")
            if git(repo, "rev-parse", "HEAD") != base:
                raise ValueError("Worker committed outside the wrapper")
            git(repo, "add", "--intent-to-add", "--", ".")
            files = git(repo, "diff", "--name-only", "-z", base).split("\0")
            files = [f for f in files if f]
            if any(not allowed(f, task["paths"]) for f in files):
                raise ValueError("Worker wrote outside its reserved scope")
            if task["kind"] == "diagnose":
                if files:
                    raise ValueError("A diagnosis task changed source files")
                finish_task(task["id"], "done", result["summary"], result=result)
                return
            if not files:
                finish_task(task["id"], "blocked", result["summary"], result=result)
                return
            diff = command(["git", "-C", str(repo), "diff", "--binary", base])
            if any(os.environ.get(k, "").encode() in diff for k in
                   ("OPENCODE_API_KEY", "GH_TOKEN", "GITHUB_TOKEN") if os.environ.get(k)):
                raise ValueError("Credential detected in proposed changes")
            # Only the already validated file list can be committed by the wrapper.
            git(repo, "add", "--", *files)
            git(repo, "commit", "-m", f"fleet: {task['title']}")
            head = git(repo, "rev-parse", "HEAD")
            finish_task(task["id"], "running", result["summary"], branch=branch, base=base, head=head)
            env = author_env()
            command(["git", "-C", str(repo), "push", "origin", f"HEAD:refs/heads/{branch}"], env=env)
            body = (f"Shared fleet task: `{task['id']}`\n\n{result['summary']}\n\n"
                    f"Acceptance: {task['acceptance']}\n\nBase measured: `{base}`\nHead: `{head}`\n"
                    "\nThe shared integration queue must approve this draft; the worker cannot merge.\n")
            fixture = result.get("frontier_fixture", "")
            if fixture:
                if not re.fullmatch(r"[a-z0-9-]+", fixture):
                    raise ValueError("Invalid frontier fixture name")
                body += f"\nFrontier-fixture: {fixture}\n"
            # Recover an already-created PR if the result state write was interrupted.
            existing = api(f"repos/{PROJECT}/pulls?state=open&head=" + urllib.parse.quote(PROJECT.split('/')[0] + ':' + branch, safe=""))
            pr = existing[0] if existing else api(f"repos/{PROJECT}/pulls", "POST", {
                "title": task["title"], "head": branch, "base": BASE, "draft": True, "body": body})
            finish_task(task["id"], "review", result["summary"], result=result, branch=branch,
                        base=base, head=head, pr=pr["number"], pr_url=pr["html_url"], not_before=0)
        print("Worker result saved privately; draft queued for integration.")
    except Exception as error:
        try:
            finish_task(task["id"], "blocked", "Arbeiter angehalten: " + safe_error(error) + ". Details im privaten Laufprotokoll.")
            private_log("failure", repr(error))
        finally:
            raise


def integrate():
    task = None
    def reserve(s):
        if not s["config"]["enabled"] or s.get("observed", {}).get("legacy_active"):
            return None
        if not acquire(s, "integration", OWNER):
            return None
        s["leases"].pop("integration-dispatch", None)
        for value in s["tasks"].values():
            if value["status"] == "review" and value.get("not_before", 0) <= time.time():
                value.update(status="gating", updated=stamp(), lease={"owner": OWNER, "pool": POOL,
                    "run_id": RUN_ID, "run_url": RUN_URL, "until": time.time() + LEASE_SECONDS})
                return copy.deepcopy(value)
        release(s, "integration", OWNER)
        return None
    task = STORE.update(reserve, "reserve global integration queue")
    if not task:
        return
    try:
        with Heartbeat(task=task["id"]) as heartbeat, Heartbeat(lease="integration") as integration_heartbeat:
            repo = setup_game(research=False)
            env = author_env()
            env["S5_SCRIPTS"] = str(SCRIPTS)
            env["S5_LIVE_TIMEOUT"] = "900"
            env["S5_GATE_EXPECT_HEAD"] = task["head"]
            log_path = Path(os.environ["RUNNER_TEMP"]) / "private-gate.log"
            # The gate owns its process group so a lost global reservation stops it.
            with log_path.open("wb") as log:
                proc = subprocess.Popen(["bash", str(SCRIPTS / "s5-gate.sh"), str(task["pr"])],
                                        cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=True)
                started = time.monotonic()
                while proc.poll() is None:
                    if heartbeat.lost.is_set() or integration_heartbeat.lost.is_set() or time.monotonic() - started > 170 * 60:
                        os.killpg(proc.pid, signal.SIGTERM)
                        try:
                            proc.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            os.killpg(proc.pid, signal.SIGKILL)
                            proc.wait()
                        raise RuntimeError("Integration stopped: reservation lost or time limit")
                    time.sleep(10)
            log = log_path.read_text(errors="replace")
            private_log("gate", log)
            pr = api(f"repos/{PROJECT}/pulls/{task['pr']}")
            if pr["merged"]:
                finish_task(task["id"], "done", task["summary"], merged_commit=pr["merge_commit_sha"])
            elif proc.returncode == 75:
                finish_task(task["id"], "review", task["summary"], not_before=time.time() + 300)
            else:
                finish_task(task["id"], "blocked", task["summary"] + "\nIntegrationsgate fehlgeschlagen; privater PR enthält den Befund.")
    except Exception as error:
        if task:
            finish_task(task["id"], "review", task.get("summary", "Integration interrupted"), not_before=time.time() + 900)
            private_log("integration-failure", repr(error))
        raise
    finally:
        STORE.update(lambda s: release(s, "integration", OWNER), "release global integrator")


def admit_legacy():
    # Old planner runs may still emit issue comments during migration. Keep the
    # request for the shared planner; never start another unreserved old worker.
    number = int(os.environ["LEGACY_ISSUE"])
    key = f"{POOL}#{number}"
    def save(s):
        inbox = s.setdefault("inbox", {})
        if key not in inbox:
            inbox[key] = {"title": os.environ.get("LEGACY_TITLE", ""),
                          "body": os.environ.get("LEGACY_BODY", "")[:16000],
                          "comment": os.environ.get("LEGACY_COMMENT", "")[:16000], "at": stamp()}
            event(s, "legacy-request-imported", issue=key)
    STORE.update(save, "import legacy request into private planner inbox")
    print("Request handed to the shared planner; duplicate legacy worker prevented.")


def configure(args):
    def change(s):
        if args.enabled is not None:
            s["config"]["enabled"] = args.enabled == "true"
        if args.limit is not None:
            if not 1 <= args.limit <= 40:
                raise ValueError("Rollout limit must be between 1 and 40")
            s["config"]["rollout_limit"] = args.limit
            s["config"]["auto_scale"] = args.limit < 40
        if args.cutover:
            s["config"]["cutover_at"] = stamp().replace("+00:00", "Z")
        event(s, "configuration-updated", enabled=s["config"]["enabled"], limit=s["config"]["rollout_limit"])
    STORE.update(change, "configure shared fleet")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("init", "control", "plan", "worker", "integrate", "admit-legacy", "configure", "status"))
    parser.add_argument("--enabled", choices=("true", "false"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--cutover", action="store_true")
    args = parser.parse_args()
    if args.action == "init":
        STORE.initialize()
        print("Private control branch initialized.")
    elif args.action == "configure":
        configure(args)
    elif args.action == "status":
        state = STORE.read()[0]
        print(json.dumps({"config": state["config"],
                          "counts": {name: sum(t["status"] == name for t in state["tasks"].values())
                                     for name in ("queued", "running", "review", "gating", "done", "blocked")},
                          "last_plan": state.get("last_plan"), "observed": state.get("observed", {}).get("pools")}, indent=2))
    else:
        {"control": control, "plan": plan, "worker": worker,
         "integrate": integrate, "admit-legacy": admit_legacy}[args.action]()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Private diagnostics are saved by the operation. Do not leak task text in
        # a public Actions traceback or GitHub command error.
        print("Fleet operation stopped (" + safe_error(error) + "); see private control status.", file=sys.stderr)
        sys.exit(1)
