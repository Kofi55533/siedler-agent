#!/usr/bin/env python3
"""board-guard.py: docs/AGENT_BOARD.md on siedler-sim-rs clean/integration is append-only for agent runs.

Two DeepSeek runs (2026-10-20 05:33 4ae47d86 and 05:50 e6e93fc8, Berlin) pushed a board that had been rewritten from
scratch (6192 -> 11 lines), wiping the history. Modes:
  check <old-sha> <new-sha> [repo]   exit 1 if the board at <new-sha> drops more than MAX_DROP non-blank lines of <old-sha>
                                     (multiset compare; a DS-REQ marker change '- [ ]' -> '- [~]'/'- [x]' counts as kept).
  prepush [repo]                     git pre-push hook body (stdin = '<lref> <lsha> <rref> <rsha>' lines): runs check for
                                     pushes to refs/heads/clean/integration.
  repair <start-sha> [repo]          after a run: if origin/clean/integration lost board lines that existed at <start-sha>,
                                     prepend the lost lines back (in their original order, deduplicated against the current
                                     board) and push one restore commit. Never force-pushes.
"""
import collections, os, re, subprocess, sys

PATH = "docs/AGENT_BOARD.md"
MAX_DROP = int(os.environ.get("BOARD_GUARD_MAX_DROP", "3"))
AGENTS = {"Muse", "opencode", "siedler-agent", "Kofi55533", "github-actions[bot]"}  # authors of agent-run commits
MARK = re.compile(r"^(\s*- )\[[ ~x]\]")


def git(repo, *a, check=True):
    r = subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(a[:3])}: {r.stderr.strip()[:200]}")
    return r.stdout


def board(repo, sha):
    r = subprocess.run(["git", "-C", repo, "show", f"{sha}:{PATH}"], capture_output=True, text=True)
    return r.stdout.splitlines() if r.returncode == 0 else None


def norm(l):
    return MARK.sub(r"\1[ ]", l.rstrip())


def dropped(old, new):
    have = collections.Counter(norm(l) for l in new)
    out = []
    for l in old:
        k = norm(l)
        if not k.strip():
            continue
        if have[k] > 0:
            have[k] -= 1
        else:
            out.append(l)
    return out


def check(repo, old_sha, new_sha):
    old, new = board(repo, old_sha), board(repo, new_sha)
    if old is None or new is None:
        return 0
    d = dropped(old, new)
    if len(d) > MAX_DROP:
        sys.stderr.write(
            f"board-guard: REJECTED push: {PATH} would lose {len(d)} of {len(old)} lines ({len(old)} -> {len(new)} lines).\n"
            f"The board is append-only: fetch origin/clean/integration, check out its {PATH}, append ONLY your new lines, commit, push.\n"
            f"first lost line: {d[0][:160]}\n")
        return 1
    return 0


def prepush(repo):
    rc = 0
    for line in sys.stdin:
        p = line.split()
        if len(p) != 4 or p[2] != "refs/heads/clean/integration" or set(p[1]) == {"0"} or set(p[3]) == {"0"}:
            continue
        if subprocess.run(["git", "-C", repo, "cat-file", "-e", p[3]], capture_output=True).returncode:
            subprocess.run(["git", "-C", repo, "fetch", "-q", "origin", "clean/integration"], capture_output=True)
        rc |= check(repo, p[3], p[1])
    return rc


def repair(repo, start):
    for attempt in range(4):
        git(repo, "fetch", "-q", "origin", "clean/integration")
        old, cur = board(repo, start), board(repo, "origin/clean/integration")
        if old is None or cur is None:
            return 0
        d = dropped(old, cur)
        if len(d) <= MAX_DROP:
            print(f"board-guard: board ok ({len(cur)} lines, {len(d)} lines of the run start not present)")
            return 0
        authors = set(git(repo, "log", "--format=%an", f"{start}..origin/clean/integration", "--", PATH).split("\n")) - {""}
        human = authors - AGENTS
        if human:
            print(f"board-guard: {len(d)} lines gone, but the board was also edited by {sorted(human)} -> no auto-restore (report only)")
            return 0
        lost = set(d)
        restored = [l for l in old if l in lost] + ([""] if cur and cur[0].strip() else []) + cur
        wt = os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), "board-repair")
        subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", wt], capture_output=True)
        git(repo, "worktree", "add", "-q", "--detach", wt, "origin/clean/integration")
        open(os.path.join(wt, PATH), "w").write("\n".join(restored) + "\n")
        git(wt, "add", PATH)
        git(wt, "-c", "user.name=siedler-agent", "-c", "user.email=Kofi55533@users.noreply.github.com", "commit", "-q", "-m",
            f"board: guard restore of {len(d)} lines deleted during a run (append-only board)")
        r = subprocess.run(["git", "-C", wt, "push", "-q", "--no-verify", "origin", "HEAD:clean/integration"], capture_output=True, text=True)
        subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", wt], capture_output=True)
        if r.returncode == 0:
            print(f"board-guard: restored {len(d)} lost lines ({len(cur)} -> {len(restored)} lines)")
            return 0
    print("board-guard: restore push failed 4x")
    return 1


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[0] == "check":
        sys.exit(check(a[3] if len(a) > 3 else ".", a[1], a[2]))
    if a[0] == "prepush":
        sys.exit(prepush(a[1] if len(a) > 1 else "."))
    if a[0] == "repair":
        sys.exit(repair(a[2] if len(a) > 2 else ".", a[1]))
    sys.exit(__doc__)
