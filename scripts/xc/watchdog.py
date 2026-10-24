#!/usr/bin/env python3
"""xc-watchdog: one cycle of keeping the census -> worker -> merge loop alive (see .github/workflows/xc-watchdog.yml).

Reads only run metadata, the work list sha and issue titles; prints decisions, never game state.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xc  # noqa: E402

R = os.environ["R"]
MAIN = xc.AGENT
CENSUS_MAX_AGE = 6 * 3600
CENSUS_MIN_GAP = 3600
STALL = 24 * 3600


def gh(*args):
    return xc.gh(*args)


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def runs(workflow, repo=R, n=10):
    return (gh(f"repos/{repo}/actions/workflows/{workflow}/runs?per_page={n}") or {}).get("workflow_runs", [])


def active(rs):
    return [r for r in rs if r["status"] in ("queued", "in_progress", "waiting", "pending", "requested")]


def dispatch(workflow, **inputs):
    # this repo's own workflow token: in a fork pool the PAT belongs to the main account and cannot dispatch there
    env = dict(os.environ, GH_TOKEN=os.environ.get("DISPATCH_TOKEN") or os.environ["GH_TOKEN"])
    r = subprocess.run(["gh", "api", "-X", "POST", f"repos/{R}/actions/workflows/{workflow}/dispatches", "-f", "ref=main",
                        *[a for k, v in inputs.items() for a in ("-f", f"inputs[{k}]={v}")]], capture_output=True, text=True,
                       env=env)
    print(f"dispatch {workflow}: {'ok' if r.returncode == 0 else 'failed'}")


def worklist():
    d = tempfile.mkdtemp()
    r = subprocess.run(["gh", "release", "download", "xc-worklist", "-R", os.environ["XC_DATA"], "-D", d, "-p",
                        "worklist.json"], capture_output=True, text=True)
    return os.path.join(d, "worklist.json") if r.returncode == 0 else None


def pickable(path):
    out = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "xc.py"), "pick", path, "50"],
                         capture_output=True, text=True).stdout.strip()
    return json.loads(out or "[]")


def stall_issue(reason):
    issues = gh(f"repos/{MAIN}/issues?state=open&per_page=100") or []
    open_ = [i for i in issues if i["title"] == "[xc] pipeline stalled"]
    if reason and not open_:
        gh(f"repos/{MAIN}/issues", "-f", "title=[xc] pipeline stalled", "-f",
           f"body={reason}\n\nOpened by xc-watchdog ({R}); closed by it once the loop moves again.")
        print("stall issue opened:", reason)
    elif not reason and open_:
        gh("-X", "PATCH", f"repos/{MAIN}/issues/{open_[0]['number']}", "-f", "state=closed")
        print("stall issue closed")


def main():
    now = time.time()
    wl = worklist()
    doc = json.load(open(wl)) if wl else {}
    sha = doc.get("sha", "")
    old = doc.get("census", 1) != xc.CENSUS  # measured by an earlier census version: re-measure on the same tip
    tip = (gh(f"repos/{xc.PROJECT}/commits/clean/integration") or {}).get("sha", "")
    reasons = []

    if R == MAIN:
        cr = runs("xc-census.yml")
        done = [r for r in cr if r["status"] == "completed"]
        ok = [r for r in done if r["conclusion"] == "success"]
        last_ok = ts(ok[0]["created_at"]) if ok else 0
        last_any = ts(cr[0]["created_at"]) if cr else 0
        if active(cr):
            print("census running")
        elif not wl or now - last_ok > CENSUS_MAX_AGE or ((old or tip and tip != sha) and now - last_any > CENSUS_MIN_GAP):
            dispatch("xc-census.yml")
        if len(done) >= 3 and all(r["conclusion"] == "failure" for r in done[:3]):
            reasons.append("the last 3 xc-census runs failed")

    # waves overlap freely (the claims keep them apart); a new one starts while kinds are pickable, no wave waits
    # in the queue and this repo holds fewer than XC_WORKERS claims (each claim is one running worker job)
    kinds = pickable(wl) if wl else []
    queued = [r for r in runs("xc-worker.yml") if r["status"] in ("queued", "waiting", "pending", "requested")]
    busy = sum(1 for _, body in xc.tracking().values() if xc.claimed(body, now, R))  # this repo's slots only
    limit = int(os.environ.get("XC_WORKERS") or 20)
    print(f"{len(kinds)} kinds pickable, {busy} claimed, {len(queued)} waves queued")
    if kinds and not queued and busy < limit:
        dispatch("xc-worker.yml", n=limit - busy)

    if R == MAIN and kinds:
        prs = gh(f"repos/{xc.PROJECT}/pulls?state=closed&base=clean/integration&per_page=50&sort=updated&direction=desc") or []
        merged = [ts(p["merged_at"]) for p in prs if p.get("merged_at") and p["head"]["ref"].startswith("xc/")]
        first = runs("xc-worker.yml", n=100)
        started = ts(first[-1]["created_at"]) if first else now
        if now - max(merged + [started]) > STALL:
            reasons.append("no xc merge in 24 h although task kinds are pickable")
    if R == MAIN:
        stall_issue("; ".join(reasons))


if __name__ == "__main__":
    main()
