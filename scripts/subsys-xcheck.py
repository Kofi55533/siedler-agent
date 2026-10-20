#!/usr/bin/env python3
"""subsys-xcheck.py: gate rule for subsystem code no fixture executes (Dominic 2026-10-20, whole game bit-exact).

  subsys-xcheck.py plan <repo> <base-rev> <head-rev> <pr.diff> <body.txt> [coverage.md]
      -> prints 'none' (no newly cited unexecuted logic function: rule not applicable) or, one per line,
         'unexecuted <addr> ...', 'all-unexecuted 0|1', 'tests <cargo test args>' and 'missing <reason>' lines.

New addresses = 0x[0-9A-F]{6} in added lines of crates/s5clean/src/*.rs (same rule as tools/coverage_map.py) that base does
not cite yet, restricted to logic functions of the whole-game map (docs/subsystems/subsystems.json at base). Executed = the
map's executed flags (fixture traces) united with the uncited rows of the current coverage map (coverage.md). For the
unexecuted ones the PR needs (a) 'Differential-tests: <cargo test args>' in the body, (b) each address in a
crates/s5xcheck/tests/*.rs file of the head that also makes a reference call, (c) a 'Fixture:' or 'Fixture-request:' line.
"""
import json, re, subprocess, sys
from pathlib import Path

HEX6 = re.compile(r"0x((?:00)?[0-9A-Fa-f]{6})\b")
REFCALL = re.compile(r"\b(s5xcheck::(call|call_this|with_reference)|call_this|with_reference|query_isolated)\s*\(|\bxcheck::call\s*\(")


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True).stdout


def main():
    _, mode, repo, base, head, diff, body = sys.argv[:7]
    cov = sys.argv[7] if len(sys.argv) > 7 else None
    # The 12,568-entry subsystem map cannot exclude the remaining obligations
    # of the authoritative 29,723-entry inventory. Missing map data is unknown
    # execution coverage, never permission to waive native differential tests.
    inventory = json.loads((Path(__file__).parent / "scoreboard/function_inventory.json").read_text())
    logic = {int(x.split("+", 1)[0], 16) for x in inventory["functions"] if x.endswith("L")}
    fn = {a: ("unclassified", False) for a in logic}
    try:
        m = json.loads(git(repo, "show", f"{base}:docs/subsystems/subsystems.json"))
    except Exception:
        m = {"subsystems": []}
    for s in m["subsystems"]:
        for f in s["functions"]:
            fn[int(f["addr"], 16)] = (s["id"], bool(f["executed"]))
    executed = {a for a, (_, e) in fn.items() if e}
    if cov:
        try:
            for x in re.findall(r"^\| \d+ \| `0x([0-9A-Fa-f]+)`", open(cov).read(), re.M):
                executed.add(int(x, 16))
        except OSError:
            pass
    old = {int(x, 16) for x in HEX6.findall(git(repo, "grep", "-hoE", r"0x(00)?[0-9A-Fa-f]{6}\b", base, "--", "crates/s5clean/src"))}
    new, cur = set(), None
    for line in open(diff, errors="replace"):
        if line.startswith("+++ "):
            p = line[4:].strip(); cur = p[2:] if p.startswith("b/") else p; continue
        if line.startswith("+") and cur and cur.startswith("crates/s5clean/src/") and cur.endswith(".rs"):
            new.update(int(x, 16) for x in HEX6.findall(line))
    new = {a for a in new - old if a in fn}
    un = sorted(a for a in new if a not in executed)
    if not un:
        print("none"); return
    print("unexecuted " + " ".join(f"{a:#x}" for a in un))
    print(f"all-unexecuted {int(len(un) == len(new))}")
    b = open(body, errors="replace").read()
    t = re.findall(r"(?mi)^\s*differential-tests:\s*(\S.*?)\s*$", b)
    if t:
        print("tests " + t[0])
    else:
        print("missing PR body line 'Differential-tests: <cargo test args>' (e.g. --test <subsystem-id>)")
    if not re.search(r"(?mi)^\s*fixture(-request)?:\s*\S", b):
        print("missing PR body line 'Fixture: <fixture that executes it>' or 'Fixture-request: <scenario that would reach it>'")
    files = [f for f in git(repo, "ls-tree", "-r", "--name-only", head, "--", "crates/s5xcheck/tests").split() if f.endswith(".rs")]
    # Evidence in an unrelated, unselected test target does not count. This is
    # deliberately conservative for filtered tests: use one named target.
    selected = re.findall(r"--test\s+([A-Za-z0-9_-]+)", t[0]) if t else []
    files = [f for f in files if Path(f).stem in selected]
    texts = [git(repo, "show", f"{head}:{f}") for f in files]
    texts = [t_.lower() for t_ in texts if REFCALL.search(t_)]
    miss = [a for a in un if not any(re.search(rf"0x{a:06x}\b", t_) for t_ in texts)]
    if miss:
        print("missing differential test (address in a crates/s5xcheck/tests file with a reference call) for " +
              " ".join(f"{a:#x}" for a in miss[:12]) + (" ..." if len(miss) > 12 else ""))


if __name__ == "__main__":
    main()
