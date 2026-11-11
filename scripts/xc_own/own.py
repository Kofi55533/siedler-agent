#!/usr/bin/env python3
"""Own-repo mode of the xc fleet (README.md next to this file): a pool account that never writes to a repo of the
main account. It works in its own results repo (XC_OWN_RESULTS: worker.sh unchanged, with XC_PROJECT and XC_AGENT
pointing there); the main account imports the passed changes (xc-import.yml) and re-verifies them with its own
worker.sh before anything reaches clean/integration.

  own.py part <kind> <spec>
      exit 0 when the kind falls in the partition <spec> "r[,r...]/m" (sha256 of the kind name mod m)
  own.py pick <worklist> <n> [--claim] [--limit=L]                                   (pool account, own token)
      the kinds the pool works on now, as a JSON list. A kind qualifies when the main fleet would leave it free
      (fleet rules of xc.py available(), read only from the main repos: todo, no open PR on its branch there, no
      merge since the census was pinned, no live claim, attempts left) and the own tracking issues in XC_OWN_RESULTS
      leave it free as well (no own live claim, no own pass since the pin or within XC_OWN_REDO_H, own attempts
      left). Of these it takes only
      the kinds in its partition XC_OWN_PARTITION (default 1/2) and skips the XC_OWN_SKIP (default 12) best ranked
      ones, which the main fleet takes next. --claim claims them in the own tracking issues only.
  own.py imports <worklist> <n> <results ls-remote file> <seen.json> [--claim]       (main account, main token)
      the result branches of the pool worth importing now, as a JSON list of {"kind", "sha"}: a branch xc/<kind>
      whose kind the main fleet rules leave available, whose head was not tried yet (seen.json, updated in place),
      at most <n> minus the imports still running. --claim claims them in the main tracking issues (main pool slot).
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "xc"))
import xc  # noqa: E402  the fleet's own rules; this file only reads and calls them

MAIN_AGENT = os.environ.get("XC_MAIN_AGENT", "Kofi55533/siedler-agent")
MAIN_PROJECT = os.environ.get("XC_MAIN_PROJECT", "Kofi55533/siedler-sim-rs")
RESULTS = os.environ.get("XC_OWN_RESULTS", "")


def part(kind, spec):
    """the deterministic partition: kind -> residue of its sha256 mod m; spec "r[,r...]/m", "" or "all" = every kind"""
    if not spec or spec == "all":
        return True
    res, m = spec.split("/")
    return int(hashlib.sha256(kind.encode()).hexdigest(), 16) % int(m) in {int(r) for r in res.split(",")}


class repos:
    """xc.py reads its repos from module globals: point them at another pair for a block"""

    def __init__(self, agent, project):
        self.new = (agent, project)

    def __enter__(self):
        self.old = (xc.AGENT, xc.PROJECT)
        xc.AGENT, xc.PROJECT = self.new

    def __exit__(self, *exc):
        xc.AGENT, xc.PROJECT = self.old


def own_ended(pool):
    """a claim of another pool counts as live until it is released or CLAIM_H passes (no run lookups, fewer calls);
    the own runs are looked up as xc.py does"""
    real = xc.ended
    return lambda run, p: real(run, p) if p == pool else False


def passed_lately(body, now):
    """the pool passed the kind less than XC_OWN_REDO_H (default 24) hours ago: its result branch waits for the import
    (a census pinned after the pass would otherwise hand the kind out again within the hour)"""
    m = xc.fields(body).get("merged", "").split()
    return len(m) > 1 and now - int(m[1]) < float(os.environ.get("XC_OWN_REDO_H", "24")) * 3600


def pick(worklist, n, claim=False, limit=None, now=None):
    if not RESULTS or RESULTS.split("/")[0] == MAIN_AGENT.split("/")[0]:
        sys.exit("own.py pick: XC_OWN_RESULTS must be a repo of this pool's own account")
    doc, now = xc.load(worklist), now or time.time()
    xc.ended = own_ended(xc.POOL)
    with repos(MAIN_AGENT, MAIN_PROJECT):
        main = xc.tracking()
        free = dict(xc.available(doc, main, now))
    with repos(RESULTS, RESULTS):
        own = xc.tracking()
        if limit is not None:
            n = min(n, limit - xc.jobs(own, now, xc.POOL))
        commit = xc.gh(f"repos/{MAIN_PROJECT}/commits/{doc['sha']}") or {}
        pinned = commit.get("commit", {}).get("committer", {}).get("date")
        pinned = xc.datetime.fromisoformat(pinned.replace("Z", "+00:00")).timestamp() if pinned else None
        ranked = [r[-1] for r in sorted(xc.rank(kind, k) for kind, k in free.items())]
        skip = int(os.environ.get("XC_OWN_SKIP", "12"))
        spec = os.environ.get("XC_OWN_PARTITION", "1/2")
        kinds = []
        for kind in ranked[skip:]:
            body = own.get(kind, (None, ""))[1]
            if (part(kind, spec) and not xc.claimed(body, now) and not xc.merged_since(body, doc["sha"], pinned)
                    and not passed_lately(body, now) and xc.tried(body, doc["sha"]) < xc.ATTEMPTS):
                kinds.append(kind)
        kinds = kinds[:max(n, 0)]
        if claim:
            kinds = xc.claim_own(kinds)
    print(json.dumps(kinds))
    return kinds


def branches(lsremote):
    """`git ls-remote <results> 'refs/heads/xc/*'` output -> branch -> head sha"""
    out = {}
    for line in Path(lsremote).read_text().splitlines():
        if "\trefs/heads/" in line:
            sha, ref = line.split("\t", 1)
            out[ref.removeprefix("refs/heads/")] = sha
    return out


def importing(body):
    """the claim belongs to an import job: its token is "import-<run id>" (xc-import.yml)"""
    return xc.fields(body).get("claimed", "").split()[3:4] != [] and xc.fields(body)["claimed"].split()[3].startswith("import-")


def imports(worklist, n, lsremote, seen_path, claim=False, now=None):
    doc, now = xc.load(worklist), now or time.time()
    heads = branches(lsremote)
    seen = json.loads(Path(seen_path).read_text()) if Path(seen_path).exists() else {}
    # a head tried once is not tried again (the pool's next pass pushes a new head); entries expire after 14 days
    seen = {sha: t for sha, t in seen.items() if now - t < 14 * 86400}
    with repos(MAIN_AGENT, MAIN_PROJECT):
        issues = xc.tracking()
        running = sum(1 for _, body in issues.values() if xc.claimed(body, now, xc.POOL) and importing(body))
        n = min(n, max(int(os.environ.get("XC_IMPORT_MAX", "2")) - running, 0))
        cand = []
        for kind, k in xc.available(doc, issues, now):
            sha = heads.get(xc.branch(kind))
            if sha and sha not in seen:
                cand.append((xc.rank(kind, k), kind, sha))
        cand = sorted(cand)[:n]
        kinds = [kind for _, kind, _ in cand]
        if claim and kinds:
            kinds = xc.claim_own(kinds)
    out = [{"kind": kind, "sha": sha} for _, kind, sha in cand if kind in kinds]
    for o in out:
        seen[o["sha"]] = int(now)
    Path(seen_path).write_text(json.dumps(seen, indent=0, sort_keys=True))
    print(json.dumps(out))
    return out


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    lim = [int(a.split("=")[1]) for a in argv if a.startswith("--limit=")]
    args = [a for a in argv[2:] if not a.startswith("--")]
    if cmd == "part":
        sys.exit(0 if part(args[0], args[1]) else 1)
    elif cmd == "pick":
        pick(args[0], int(args[1]), claim="--claim" in argv, limit=lim[0] if lim else None)
    elif cmd == "imports":
        imports(args[0], int(args[1]), args[2], args[3], claim="--claim" in argv)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
