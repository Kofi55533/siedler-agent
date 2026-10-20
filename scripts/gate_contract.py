"""Typed receipts and offline validation shared by the gate and batch writer."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def fixtures(body):
    names = set()
    for line in body.splitlines():
        m = re.match(r"(?i)^\s*frontier-fixture:\s*(.*?)\s*$", line)
        if not m:
            continue
        for name in re.split(r"[,\s]+", m[1]):
            if not name:
                continue
            if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
                raise ValueError("Invalid required fixture name")
            names.add(name)
    return sorted(names)


def receipt(tsv, base, tree, head, commit, expected, tests="", data_release=""):
    for sha in (base, tree, head, commit):
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Receipt must name immutable Git objects")
    rows = {}
    for line in tsv.splitlines():
        fields = line.split()
        if len(fields) != 7:
            raise ValueError("Malformed receipt row")
        name, status, before, after, rb, rt, rh = fields
        if name in rows or (rb, rt, rh) != (base, tree, head):
            raise ValueError("Mixed or duplicated receipt")
        if status not in {"same", "better"} or not before.isdigit() or not after.isdigit():
            raise ValueError("No successful typed comparison")
        if not 0 < int(before) <= int(after) or (status == "same") != (int(before) == int(after)):
            raise ValueError("Regression or invalid baseline")
        rows[name] = {"before": int(before), "after": int(after), "status": status}
    if not rows or set(rows) != set(expected):
        raise ValueError("Required fixture set is incomplete")
    return {"schema": 1, "base": base, "tree": tree, "head": head, "commit": commit,
            "fixtures": rows, "differential_tests": tests,
            "data_release": hashlib.sha256(data_release.encode()).hexdigest() if data_release else None}


def input_tree(repo, ref):
    # Only prose coordination is ignored. JSON ledgers/corpora may be consumed
    # by tests; blanket exclusion of DSnn directories could hide changed input.
    result = subprocess.run(["git", "-C", str(repo), "ls-tree", "-rz", ref],
                            capture_output=True, check=True).stdout
    entries = []
    for row in result.split(b"\0"):
        if not row:
            continue
        path = row.split(b"\t", 1)[1].decode()
        if path.startswith("docs/") and path.endswith(".md"):
            continue
        entries.append(row)
    return hashlib.sha256(b"\0".join(entries)).hexdigest()


def script_digest(directory):
    names = ("s5-gate.sh", "s5-live.sh", "s5-env.sh", "s5-build.sh", "s5-fixture-install.sh",
             "scoreboard/function_inventory.json",
             "subsys-xcheck.py", "gate_contract.py", "lane-claims.py", "muse-claims.txt")
    return hashlib.sha256(b"".join((Path(directory) / n).read_bytes() for n in names)).hexdigest()


def main():
    import sys
    if sys.argv[1] == "fixtures":
        print("\n".join(fixtures(Path(sys.argv[2]).read_text())))
    elif sys.argv[1] == "receipt":
        _, _, path, base, tree, head, commit, expected, tests, data_release = sys.argv
        print(json.dumps(receipt(Path(path).read_text(), base, tree, head, commit, expected.split(), tests, data_release)))
    elif sys.argv[1] == "input-tree":
        print(input_tree(sys.argv[2], sys.argv[3]))
    elif sys.argv[1] == "script-digest":
        print(script_digest(sys.argv[2]))


if __name__ == "__main__":
    main()
