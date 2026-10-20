#!/usr/bin/env python3
"""subsystem_task.py: 'Subsystem:' task type (Dominic 2026-10-20).

  ensure            (refill) keep scripts/subsystems.json "slots" (2) Subsystem tasks active or queued: for each missing
                    one, queue an issue for the first subsystem (in file order, status != done) that has no active/queued
                    task (one run per subsystem/branch at a time). Prints one log line per queued issue.
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


def ensure():
    runs = gh("run", "list", "-R", A, "-w", "opencode.yml", "--limit", "100", "--json", "status,displayTitle")
    busy = [sid(r["displayTitle"]) for r in runs if r["status"] != "completed" and sid(r["displayTitle"])]
    q = gh("issue", "list", "-R", A, "--label", "queued", "--state", "open", "--limit", "100", "--json", "title")
    busy += [sid(i["title"]) for i in q if sid(i["title"])]
    busy = set(busy)
    need = CFG.get("slots", 2) - len(busy)
    labeled = False
    for s in CFG["subsystems"]:
        if need <= 0:
            break
        if s.get("status") == "done" or s["id"] in busy:
            continue
        title = f"Subsystem: {s['id']} — port the {s['title']} function group bit-exact (branch {s['branch']})"
        body = (f"Subsystem run (Dominic 2026-10-20): port the whole {s['title']} subsystem, not one divergence.\n\n"
                f"Scope: {s['scope']}.\nClasses: {s['classes']}.\nFiles: {s['files']}.\nFixtures: {s['fixtures']}.\n"
                f"Branch: {s['branch']} (continue it if it exists; one PR).\n\n---PLANNER-COMMENT---\n"
                f"/opencode SUBSYSTEM RUN {s['id']}: follow the SUBSYSTEM RUN section of the prompt exactly (it overrides the "
                f"one-divergence/+-50-turn scope). Branch {s['branch']}, base clean/integration, fixtures {s['fixtures']}.\n")
        if not labeled:  # once per creation round, not every refill cycle (1 API call each)
            subprocess.run(["gh", "label", "create", "subsystem", "-R", A, "--color", "5319e7", "--description",
                            "Subsystem run (whole function group, 200 min); kept at 2 by refill"], capture_output=True)
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
    return t


if __name__ == "__main__":
    if sys.argv[1:2] == ["ensure"]:
        ensure()
    elif sys.argv[1:2] == ["prompt"]:
        sys.stdout.write(prompt(sys.argv[2]))
