"""Admit stranded fixture packages to the unchanged full legacy gate.

Admission is not a correctness verdict. State, PR metadata and retry history stay
in the private control repository. No model or heavy-agent session is required.
"""
from __future__ import annotations

import hashlib
import json
import re

import fleet_state as fs

GATE_PATHS = ("/gate-baseline.yml",)


def packages():
    owner, name = fs.PROJECT.split("/")
    query = """query($owner:String!,$name:String!,$base:String!,$after:String){
      repository(owner:$owner,name:$name){pullRequests(states:OPEN,first:100,
        baseRefName:$base,after:$after){nodes{number title body headRefName headRefOid
          files(first:100){nodes{path} pageInfo{hasNextPage}}
          comments(last:20){nodes{body createdAt}}}
        pageInfo{hasNextPage endCursor}}}}"""
    result, after = [], None
    for _ in range(10):
        value = fs.api("graphql", "POST", {"query": query, "variables": {
            "owner": owner, "name": name, "base": fs.BASE, "after": after}})
        if value.get("errors") or not value.get("data", {}).get("repository"):
            raise RuntimeError("Cannot read complete backlog metadata")
        connection = value["data"]["repository"]["pullRequests"]
        for pr in connection["nodes"]:
            files = pr["files"]
            pr["files"] = ([f["filename"] for f in fs.paged(f"repos/{fs.PROJECT}/pulls/{pr['number']}/files")]
                           if files["pageInfo"]["hasNextPage"] else [f["path"] for f in files["nodes"]])
            pr["comments"] = pr["comments"]["nodes"]
            result.append(pr)
        if not connection["pageInfo"]["hasNextPage"]:
            return result
        cursor = connection["pageInfo"].get("endCursor")
        if not cursor or cursor == after:
            raise RuntimeError("Incomplete backlog pagination")
        after = cursor
    raise RuntimeError("Backlog pagination limit reached")


def epoch(previous, latest, comparison=None):
    if not previous:
        return {"head": latest, "input": latest}
    if previous["head"] == latest:
        return dict(previous)
    files = (comparison or {}).get("files", [])
    prose_only = ((comparison or {}).get("status") in {"ahead", "identical"} and 0 < len(files) < 300
                  and all(f["filename"].startswith("docs/") and f["filename"].endswith(".md") for f in files))
    return {"head": latest, "input": previous["input"] if prose_only else latest}


def regates(runs):
    return {int(m[1]) for r in runs.values()
            if r.get("path", "").endswith("/gate-baseline.yml")
            and (m := re.fullmatch(r"gate-baseline regate (\d+)", r.get("display_title", "")))}


def gate_count(runs):
    return sum(r.get("path", "").endswith(GATE_PATHS) for r in runs.values())


def pending(state, runs, at):
    active = regates(runs)
    return sum(int(n) not in active and at - a["at"] < 1200
               for n, a in state.get("backlog_attempts", {}).items())


def key(pr, input_epoch, scripts):
    return hashlib.sha256(json.dumps([pr["headRefOid"], pr["body"], input_epoch, scripts]).encode()).hexdigest()


def eligible(pr):
    # Source packages remain with their domain owner and typed-ticket pipeline.
    if not pr["headRefName"].startswith("agent/") or not pr["files"]:
        return False
    if any(not p.startswith(("fixtures-gen/", "docs/clean/new-fixtures/"))
           and p != "docs/AGENT_BOARD.md" for p in pr["files"]):
        return False
    body = pr["body"] or ""
    if not re.search(r"(?im)^\s*New-(?:code-path|unmodelled):\s*\S", body):
        return False
    if not re.search(r"(?im)^\s*Frontier-fixture:\s*\S", body):
        return False
    if not re.search(r"(?i)live\s+\S+.*before.*after", body):
        return False
    gates = [c["body"] for c in pr["comments"] if "self-merge gate:" in c["body"]]
    if not gates:
        return True
    last = gates[-1]
    # An unchanged correctness rejection needs an author repair, not more CPU.
    if any(marker in last for marker in ("DEFERRED", "fresh lane claim", "could not be installed",
                                        "invalid required fixture declaration", "no result (ERROR/TIMEOUT)",
                                        "without a new code path", "without live before/after turn numbers")):
        return True
    head = re.search(r"head=\S+@([0-9a-f]{8,40})", last)
    return bool(head and not pr["headRefOid"].startswith(head[1]))


def select(state, prs, runs, at, input_epoch, scripts, slots):
    managed = {t["pr"] for t in state["tickets"].values()}
    managed.update(u["wip"]["pr"] for u in state["units"].values() if (u.get("wip") or {}).get("pr"))
    active = regates(runs)
    # Preserve legacy writers and their own inline gates during migration.
    writers = [r.get("display_title", "") for r in runs.values() if r.get("path", "").endswith("/opencode.yml")]
    room = min(slots, max(0, 2 - len(active) - pending(state, runs, at)))
    result = []
    for pr in sorted(prs, key=lambda p: p["number"]):
        n = pr["number"]
        if n in managed or n in active or not eligible(pr):
            continue
        if any(pr["title"].casefold() == title.casefold() or re.search(rf"\bPR\s*#{n}\b", title, re.I)
               for title in writers):
            continue
        attempt = state.get("backlog_attempts", {}).get(str(n))
        digest = key(pr, input_epoch, scripts)
        if attempt and (at - attempt["at"] < 1200 or
                        (attempt["key"] == digest and attempt["status"] != "unknown")):
            continue
        result.append({"pr": n, "key": digest})
    return result[:max(0, room)]


def reserve(state, candidate, at):
    attempts = state.setdefault("backlog_attempts", {})
    old = attempts.get(str(candidate["pr"]))
    if old and (at - old["at"] < 1200 or old["key"] == candidate["key"] and old["status"] != "unknown"):
        return False
    attempts[str(candidate["pr"])] = {"key": candidate["key"], "at": at, "status": "unknown"}
    return True
