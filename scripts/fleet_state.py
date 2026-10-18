"""Private, optimistic Git-backed task queue shared by both Actions pools.

Only generic infrastructure belongs in this public repository. Tasks, reservations,
model output and status live on agents/control in the private source repository.
"""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
import fnmatch
import json
import os
from pathlib import PurePosixPath
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

PROJECT = "Kofi55533/siedler-sim-rs"
BASE = "clean/integration"
CONTROL = "agents/control"
STATE_PATH = "fleet/state.json"
POOLS = ("Kofi55533/siedler-agent", "Dominickofi00/opencode-runner")
MODEL = "opencode/deepseek-v4.1-flash-free"
LEASE_SECONDS = 1800
LIVE_STATES = {"running", "review", "gating"}
HARD_PATHS = ("recomp/", "oracle/", "crates/s5verify/", "crates/s5diff/", ".github/")


class ApiError(RuntimeError):
    def __init__(self, status, body=""):
        super().__init__(f"GitHub request failed ({status})")
        self.status = status
        self.body = body


def api(path, method="GET", data=None):
    payload = None if data is None else json.dumps(data).encode()
    request = urllib.request.Request(
        "https://api.github.com/" + path.lstrip("/"), payload, method=method,
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "Content-Type": "application/json", "User-Agent": "siedler-shared-fleet"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raise ApiError(error.code, error.read().decode(errors="replace")) from None


def paged(path, key=None, pages=10):
    result = []
    for page in range(1, pages + 1):
        value = api(path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
        items = value[key] if key else value
        result.extend(items)
        if len(items) < 100:
            return result
    raise RuntimeError("Pagination limit reached; refusing an incomplete scheduling snapshot")


def file_text(path, ref=BASE, repository=PROJECT):
    value = api(f"repos/{repository}/contents/{path}?ref=" + urllib.parse.quote(ref, safe=""))
    if value.get("encoding") != "base64":
        value = api(f"repos/{repository}/git/blobs/{value['sha']}")
    return base64.b64decode(value["content"]).decode("utf-8")


def initial():
    return {
        "schema": 1, "created": stamp(), "config": {
            "enabled": False, "project": PROJECT, "base": BASE,
            "pools": {pool: 20 for pool in POOLS}, "max_active": 40,
            "rollout_limit": 40, "queue_low_water": 48,
            "auto_scale": False, "discovery_enabled": True,
            "planner_interval_seconds": 300, "max_attempts": 2,
            "model": MODEL,
        }, "tasks": {}, "leases": {}, "providers": {}, "events": [],
        "observed": {"legacy_active": [], "open_prs": []},
    }


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def event(state, kind, **fields):
    state["events"].append({"at": stamp(), "kind": kind, **fields})
    state["events"] = state["events"][-250:]


def status_markdown(state):
    tasks = list(state["tasks"].values())
    counts = {s: sum(t["status"] == s for t in tasks)
              for s in ("queued", "running", "review", "gating", "done", "blocked", "failed")}
    config = state["config"]
    lines = ["# Gemeinsame Siedler-Agenten", "", f"Aktualisiert: {stamp()}", "",
             f"Aktiviert: **{config['enabled']}** · Ziel: **{sum(config['pools'].values())}** · "
             f"Freigegebene Obergrenze: **{config['rollout_limit']}**", "",
             "| Zustand | Anzahl |", "|---|---:|"]
    lines.extend(f"| {name} | {count} |" for name, count in counts.items())
    lines += ["", "Die Zahl der Slots ist keine Zusage gleichzeitig verfügbarer GitHub- oder Modellkapazität.", "",
              "## Laufende Arbeit und Integrationswarteschlange", "",
              "| Aufgabe | Zustand | Pool | Ergebnis |", "|---|---|---|---|"]
    for task in tasks:
        if task["status"] not in LIVE_STATES:
            continue
        title = task["title"].replace("|", "/").replace("\n", " ")
        pool = task.get("lease", {}).get("pool", "")
        result = f"[PR #{task['pr']}]({task['pr_url']})" if task.get("pr") else ""
        lines.append(f"| {title} | {task['status']} | {pool} | {result} |")
    lines += ["", "## Übergang von der bisherigen Vergabe", "",
              f"Noch laufende alte Aufträge: **{len(state.get('observed', {}).get('legacy_active', []))}**.",
              "Währenddessen ist neue Arbeit auf unabhängige Recherche beschränkt. Vorhandene PRs und aktive Board-Reservierungen bleiben gesperrt.", "",
              "## Letzte Ergebnisse", ""]
    for task in sorted(tasks, key=lambda t: t.get("updated", ""), reverse=True)[:25]:
        if task["status"] in {"done", "blocked", "failed", "review"}:
            lines.append(f"- **{task['id']} / {task['status']}**: {task.get('summary', task['title'])[:1200]}")
    lines += ["", "## Tatsächliche Runner und Modellzugriff", ""]
    for pool, values in state.get("observed", {}).get("pools", {}).items():
        lines.append(f"- {pool}: {values['running']} laufend, {values['queued']} wartend.")
    for pool, values in state.get("providers", {}).items():
        lines.append(f"- {pool}: Modellfehler in Folge {values.get('errors', 0)}; "
                     f"letzter Erfolg {values.get('last_success', 'noch keiner')}.")
    if state.get("last_plan"):
        lines += ["", "## Letzter Plan", "", state["last_plan"].get("summary", "")]
    lines += ["", "Pause: `python scripts/fleet.py configure --enabled false`. "
              "Eine Pause verhindert neue Vergaben; laufende Arbeit und ihre Reservierungen bleiben erhalten.", ""]
    return "\n".join(lines)


class Store:
    def read(self):
        ref = api(f"repos/{PROJECT}/git/ref/heads/{CONTROL}")
        head = ref["object"]["sha"]
        commit = api(f"repos/{PROJECT}/git/commits/{head}")
        state = json.loads(file_text(STATE_PATH, head))
        return state, head, commit["tree"]["sha"]

    def initialize(self):
        try:
            return self.read()[0]
        except ApiError as error:
            if error.status != 404:
                raise
        state = initial()
        tree = self._tree(state)
        commit = api(f"repos/{PROJECT}/git/commits", "POST", {
            "message": "fleet: initialize private shared control state", "tree": tree, "parents": []})
        try:
            api(f"repos/{PROJECT}/git/refs", "POST", {
                "ref": f"refs/heads/{CONTROL}", "sha": commit["sha"]})
        except ApiError as error:
            if error.status != 422:
                raise
        return self.read()[0]

    def _tree(self, state, base=None, extra=None):
        entries = [
            {"path": STATE_PATH, "mode": "100644", "type": "blob",
             "content": json.dumps(state, ensure_ascii=False, indent=2) + "\n"},
            {"path": "README.md", "mode": "100644", "type": "blob", "content": status_markdown(state)},
        ]
        for path, content in (extra or {}).items():
            entries.append({"path": path, "mode": "100644", "type": "blob", "content": content})
        payload = {"tree": entries}
        if base:
            payload["base_tree"] = base
        return api(f"repos/{PROJECT}/git/trees", "POST", payload)["sha"]

    def update(self, operation, message="update", extra=None):
        for attempt in range(18):
            state, head, tree = self.read()
            before = copy.deepcopy(state)
            result = operation(state)
            if state == before and not extra:
                return result
            new_tree = self._tree(state, tree, extra)
            commit = api(f"repos/{PROJECT}/git/commits", "POST", {
                "message": "fleet: " + message, "tree": new_tree, "parents": [head]})
            try:
                api(f"repos/{PROJECT}/git/refs/heads/{CONTROL}", "PATCH", {
                    "sha": commit["sha"], "force": False})
                return result
            except ApiError as error:
                if error.status not in (409, 422):
                    raise
                time.sleep(random.uniform(0.5, min(12, 1.5 + attempt)))
        raise RuntimeError("Shared queue contention; task was not claimed")


def resource_conflict(a, b):
    for left in a:
        for right in b:
            if left == right or fnmatch.fnmatchcase(left, right) or fnmatch.fnmatchcase(right, left):
                return True
            lp, rp = left.split("*", 1)[0].rstrip("/"), right.split("*", 1)[0].rstrip("/")
            if lp and rp and (lp.startswith(rp + "/") or rp.startswith(lp + "/")):
                return True
    return False


def allowed(path, patterns):
    return any(path == p or fnmatch.fnmatchcase(path, p) or
               (p.endswith("/") and path.startswith(p)) for p in patterns)


def validate_task(value):
    task = {key: value.get(key) for key in
            ("key", "title", "kind", "prompt", "paths", "topics", "acceptance", "depends_on", "profile")}
    for key in ("key", "title", "prompt", "acceptance"):
        if not isinstance(task[key], str) or not task[key].strip():
            raise ValueError("Task requires " + key)
    if len(task["prompt"]) > 12000 or len(task["title"]) > 180:
        raise ValueError("Task is too large for a focused assignment")
    if task["kind"] not in ("diagnose", "code", "port", "scenario", "hygiene"):
        raise ValueError("Unsupported task kind")
    task["profile"] = task["profile"] or "game"
    if task["profile"] not in ("game", "source") or (task["profile"] == "source" and task["kind"] != "diagnose"):
        raise ValueError("Source-only execution is reserved for read-only investigation")
    for key in ("paths", "topics", "depends_on"):
        task[key] = task[key] or []
        if not isinstance(task[key], list) or any(not isinstance(p, str) for p in task[key]):
            raise ValueError("Task resources must be arrays of strings")
    if not task["topics"]:
        raise ValueError("Each task needs a semantic resource, preferably native:<address>")
    if task["kind"] != "diagnose" and not task["paths"]:
        raise ValueError("Writing tasks need a concrete path allowlist")
    if len(task["paths"]) > 12:
        raise ValueError("Split large write scopes")
    for path in task["paths"]:
        if (not path or path.startswith(("/", "*")) or ".." in PurePosixPath(path).parts
                or "\\" in path or path in (".", "docs/AGENT_BOARD.md")
                or any(resource_conflict([path], [blocked]) for blocked in HARD_PATHS)):
            raise ValueError("Unsupported or protected task path")
    task["key"] = task["key"].strip().lower()
    task["topics"] = sorted({p.strip().lower() for p in task["topics"] if p.strip()})
    return task


def enqueue(state, values):
    accepted = []
    existing = list(state["tasks"].values())
    for value in values:
        task = validate_task(value)
        if any(t["key"] == task["key"] for t in existing):
            continue
        if any(set(t["topics"]) & set(task["topics"]) and t["status"] not in {"done", "failed"}
               for t in existing):
            continue
        if any(d not in state["tasks"] for d in task["depends_on"]):
            continue
        task.update(id=uuid.uuid4().hex[:12], status="queued", attempts=0,
                    created=stamp(), updated=stamp(), not_before=0)
        state["tasks"][task["id"]] = task
        existing.append(task)
        accepted.append(task["id"])
    if accepted:
        event(state, "enqueued", count=len(accepted))
    return accepted


def acquire(state, name, owner, seconds=LEASE_SECONDS):
    lease = state["leases"].get(name, {})
    if lease.get("until", 0) > time.time() and lease.get("owner") != owner:
        return False
    state["leases"][name] = {"owner": owner, "until": time.time() + seconds}
    return True


def release(state, name, owner):
    if state["leases"].get(name, {}).get("owner") == owner:
        del state["leases"][name]


def blocked_resources(state):
    paths, topics = [], []
    for task in state["tasks"].values():
        if task["status"] in LIVE_STATES:
            paths.extend(task["paths"])
            topics.extend(task["topics"])
    observed = state.get("observed", {})
    paths.extend(observed.get("board_paths", []))
    for pr in observed.get("open_prs", []):
        if not any(t.get("pr") == pr["number"] and t["status"] in LIVE_STATES for t in state["tasks"].values()):
            paths.extend(pr["paths"])
    return paths, topics


def available(state):
    paths, topics = blocked_resources(state)
    return [t for t in state["tasks"].values()
            if t["status"] == "queued" and t.get("not_before", 0) <= time.time()
            and (not state.get("observed", {}).get("legacy_active") or
                 (t.get("profile") == "source" and t["kind"] == "diagnose"))
            and all(state["tasks"].get(d, {}).get("status") == "done" for d in t["depends_on"])
            and not resource_conflict(t["paths"], paths)
            and not set(t["topics"]) & set(topics)]


def claim(state, owner, pool, run_id, run_url):
    config = state["config"]
    observed = state.get("observed", {})
    if (not config["enabled"] or pool not in config["pools"]
            or time.time() - observed.get("at", 0) > 900
            or state["providers"].get(pool, {}).get("blocked_until", 0) > time.time()):
        return None
    running = [t for t in state["tasks"].values() if t["status"] == "running"]
    legacy = observed.get("legacy_active", [])
    if len(running) + len(legacy) >= min(config["max_active"], config["rollout_limit"]):
        return None
    if sum(t["lease"]["pool"] == pool for t in running) + sum(r["pool"] == pool for r in legacy) >= config["pools"][pool]:
        return None
    for task in available(state):
        task.update(status="running", updated=stamp(), attempts=task["attempts"] + 1,
                    lease={"owner": owner, "pool": pool, "run_id": run_id, "run_url": run_url,
                           "until": time.time() + LEASE_SECONDS})
        event(state, "claimed", task=task["id"], pool=pool)
        return copy.deepcopy(task)
    return None


def renew(state, task_id, owner):
    task = state["tasks"][task_id]
    if task.get("lease", {}).get("owner") != owner or task["status"] not in LIVE_STATES:
        return False
    task["lease"]["until"] = time.time() + LEASE_SECONDS
    return True


def safe_error(error):
    # Public Actions logs must never include a response body, task text or source.
    if isinstance(error, ApiError):
        return f"github-http-{error.status}"
    return type(error).__name__
