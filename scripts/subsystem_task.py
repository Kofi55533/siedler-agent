#!/usr/bin/env python3
"""subsystem_task.py: 'Subsystem:' task type (Dominic 2026-10-20).

  ensure            (refill) keep SUBSYSTEM_SLOTS (env, default scripts/subsystems.json "slots" = 2) Subsystem tasks active
                    or queued: for each missing one, queue an issue for the first subsystem in "order" (frontier blockers
                    battle, ai-village, lua-callbacks first, then dependency order of the whole-game map) that is not done
                    and has no active/queued task (one run per subsystem/branch at a time; the next run continues the same
                    branch/PR until the subsystem is complete). Done = status "done" in subsystems.json, or the
                    subsystem's overview docs/subsystems/<id>.md on siedler-sim-rs clean/integration has a line
                    "Status: complete" (one GraphQL call with the PAT for the next candidates; unreadable -> not done).
                    Prints one log line per queued issue.
  prompt <title>    (opencode.yml) the subsystem prompt variant for an issue titled 'Subsystem: <id> ...' (prompts/subsystem.md
                    with the subsystem's fields filled in); appended after the normal prompt and overriding its scope rules.
"""
import json, os, re, subprocess, sys

A = "Kofi55533/siedler-agent"
D = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(D, "subsystems.json")))


def gh(*a, parse=True, author=False):
    # author=True: an issue opencode.yml must accept (issue author Kofi55533) -> SUBSYS_TOKEN (the PAT) if refill passes it;
    # reads use the caller's GH_TOKEN (refill: the built-in GITHUB_TOKEN, separate rate limit)
    env = dict(os.environ, GH_TOKEN=os.environ["SUBSYS_TOKEN"]) if author and os.environ.get("SUBSYS_TOKEN") else None
    r = subprocess.run(["gh", *a], capture_output=True, text=True, timeout=120, env=env)
    if r.returncode:
        raise SystemExit(f"gh {' '.join(a[:3])} failed")
    return json.loads(r.stdout) if parse else r.stdout


def sid(title):
    m = re.match(r"\s*Subsystem:\s*([a-z0-9-]+)", title or "")
    return m.group(1) if m else None


def done_remote(ids):
    """ids whose docs/subsystems/<id>.md on clean/integration says 'Status: complete' (1 GraphQL call, PAT; private repo)."""
    tok = os.environ.get("SUBSYS_TOKEN")
    if not tok or not ids:
        return set()
    q = "{repository(owner:\"Kofi55533\",name:\"siedler-sim-rs\"){" + " ".join(
        f'a{i}:object(expression:"clean/integration:docs/subsystems/{x}.md"){{... on Blob{{text}}}}' for i, x in enumerate(ids)) + "}}"
    r = subprocess.run(["gh", "api", "graphql", "-f", f"query={q}"], capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, GH_TOKEN=tok))
    if r.returncode:
        return set()
    try:
        d = json.loads(r.stdout)["data"]["repository"]
    except Exception:
        return set()
    return {x for i, x in enumerate(ids)
            if re.search(r"(?mi)^\s*status:\s*complete\b", ((d.get(f"a{i}") or {}).get("text") or ""))}


def ensure():
    runs = gh("run", "list", "-R", A, "-w", "opencode.yml", "--limit", "100", "--json", "status,displayTitle")
    busy = [sid(r["displayTitle"]) for r in runs if r["status"] != "completed" and sid(r["displayTitle"])]
    q = gh("issue", "list", "-R", A, "--label", "queued", "--state", "open", "--limit", "100", "--json", "title")
    busy += [sid(i["title"]) for i in q if sid(i["title"])]
    busy = set(busy)
    slots = int(os.environ.get("SUBSYSTEM_SLOTS") or CFG.get("slots", 8))
    need = slots - len(busy)
    if need <= 0:
        return
    order = sorted(CFG["subsystems"], key=lambda x: x.get("order", 10**6))
    cand = [x for x in order if x.get("status") != "done" and x["id"] not in busy][: need + 6]
    done = done_remote([x["id"] for x in cand])
    for x in sorted(done):
        print(f"subsystem {x}: overview says Status: complete -> not queued again")
    labeled = False
    for s in cand:
        if need <= 0:
            break
        if s["id"] in done:
            continue
        title = f"Subsystem: {s['id']} — port the {s['title']} function group bit-exact (branch {s['branch']})"
        body = (f"Subsystem run (Dominic 2026-10-20): port the whole {s['title']} subsystem, not one divergence.\n\n"
                f"Scope: {s['scope']}.\nClasses: {s['classes']}.\nFiles: {s['files']}.\nFixtures: {s['fixtures']}.\n"
                f"Map: order {s.get('order', '?')}, {s.get('functions_total', '?')} functions, ported {s.get('ported', '?')}, "
                f"executed by a fixture {s.get('executed', '?')} (siedler-sim-rs docs/subsystems/INDEX.md).\n"
                f"Overview: docs/subsystems/{s['id']}.md on the branch (read first, update with every push).\n"
                f"Branch: {s['branch']} (continue it if it exists; one PR).\n\n---PLANNER-COMMENT---\n"
                f"/opencode SUBSYSTEM RUN {s['id']}: follow the SUBSYSTEM RUN section of the prompt exactly (it overrides the "
                f"one-divergence/+-50-turn scope). Branch {s['branch']}, base clean/integration, fixtures {s['fixtures']}.\n")
        if not labeled:  # once per creation round, not every refill cycle (1 API call each)
            subprocess.run(["gh", "label", "create", "subsystem", "-R", A, "--color", "5319e7", "--description",
                            "Subsystem run (whole function group, 200 min); SUBSYSTEM_SLOTS kept active by refill"], capture_output=True)
            labeled = True
        url = gh("issue", "create", "-R", A, "--title", title, "--body", body, "--label", "planner-task",
                 "--label", "queued", "--label", "subsystem", parse=False, author=True).strip()
        busy.add(s["id"]); need -= 1
        print(f"queued subsystem task {s['id']}: {url}")


def prompt(title):
    i = sid(title)
    s = next((x for x in CFG["subsystems"] if x["id"] == i), None)
    if not s:
        return ""
    t = open(os.path.join(D, "..", "prompts", "subsystem.md")).read()
    for k in ("id", "title", "branch", "scope", "classes", "files", "fixtures"):
        t = t.replace("{" + k + "}", s[k])
    for k in ("order", "functions_total", "ported", "executed", "executed_unported"):
        t = t.replace("{" + k + "}", str(s.get(k, "?")))
    t = t.replace("{depends_on}", ", ".join(s.get("depends_on", [])) or "-")
    return t


if __name__ == "__main__":
    if sys.argv[1:2] == ["ensure"]:
        ensure()
    elif sys.argv[1:2] == ["prompt"]:
        sys.stdout.write(prompt(sys.argv[2]))
