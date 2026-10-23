#!/usr/bin/env python3
"""Generate scripts/scope_classes.json: one scope class per inventory obligation.

The fleet goal is the whole game in Rust, but only what has to be rewritten:

  A  simulation        game logic; ported bit-exact, verified by the fixture gate
  B  presentation      rendering (RenderWare/ED/GD/EVid), UI, sound, app framework,
                       file I/O, LAN transport: needed, but verified by call streams
                       (same Direct3D/Miles calls), not by the simulation fixtures
  C  library/runtime   CRT/STL/EH funclets, static initializers, hash/png/zlib code:
                       Rust std or a crate plus boundary tests, never a port
  D  obsolete          Ubi.com online service, lobby, ladder, CD key, its crypto:
                       the service is gone; not rewritten

Inputs (on the box): the siedler-sim-rs subsystem map tools (tools/subsystem_map/
classify.py + build.py, run unchanged; build.py is executed from a copy that dumps its
per-function assignment) and the Forschung decompilation they read. The inventory itself
(scoreboard/function_inventory.json) is a gate fingerprint input and is not modified.

  python3 scripts/scope/make_scope_classes.py --sim-rs ../siedler-sim-rs
"""
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import pickle
import re
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parents[1]
WHY = {
    "A": "simulation: port bit-exact, fixture gate",
    "B": "presentation/platform: needed; verify by call stream, held until that harness exists",
    "C": "library/runtime/static init: Rust std or crate + boundary tests, no port",
    "D": "obsolete online service (Ubi.com, lobby, ladder, CD key): not rewritten",
}
ONLINE = re.compile(r"UbiCom|GameService|Ladder|CDKey|^cl[A-Z]|^tpl:\?\$cl|CProxy|CSession|CDoc|TinCat|"
                    r"Lobby|^(Hash|Cipher|PRNG)Module_|MatchResults")
LIBRARY = re.compile(r"^HashModule_|png|zlib|inflate|deflate", re.I)


def assignment(sim_rs, work):
    tools = sim_rs / "tools/subsystem_map"
    env = dict(os.environ, OUT=str(work))
    subprocess.run([sys.executable, str(tools / "classify.py")], env=env, check=True, stdout=subprocess.DEVNULL)
    source = (tools / "build.py").read_text()
    anchor = "# splits"
    if source.count(anchor) != 1:
        raise SystemExit("build.py changed: '# splits' anchor not found exactly once")
    dump = ("pickle.dump(sub, open(OUT + '/sub.pkl', 'wb')); raise SystemExit(0)\n")
    patched = source.replace(anchor, dump + anchor).replace(
        "HERE=os.path.dirname(os.path.abspath(__file__))", f"HERE={str(tools)!r}", 1)
    script = work / "build_dump.py"
    script.write_text(patched)
    subprocess.run([sys.executable, str(script)], env=env, check=True, stdout=subprocess.DEVNULL)
    cls = pickle.load(open(work / "cls.pkl", "rb"))["cls"]
    return pickle.load(open(work / "sub.pkl", "rb")), cls


def scope(label, cls):
    c = cls or ""
    if label.startswith("X:"):
        kind = label[2:]
        if kind == "network":
            return "D" if ONLINE.search(c) or not c else "B"
        if kind in {"runtime", "static-init"}:
            return "C"
        if kind == "framework" and ONLINE.search(c):
            return "D"
        return "B"
    if ONLINE.search(c):
        return "D"
    if LIBRARY.search(c):
        return "C"
    return "A"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim-rs", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=HERE / "scope_classes.json")
    args = ap.parse_args()
    inventory_path = HERE / "scoreboard/function_inventory.json"
    inventory = json.loads(inventory_path.read_text())
    logic = [f.split("+")[0] for f in inventory["functions"] if f.endswith("L")]
    mapped = json.loads((args.sim_rs / "docs/subsystems/subsystems.json").read_text())
    frontier = {int(f["addr"], 16) for s in mapped["subsystems"] if s.get("frontier") for f in s["functions"]}
    with tempfile.TemporaryDirectory() as work:
        sub, cls = assignment(args.sim_rs, Path(work))
    classes = collections.defaultdict(list)
    for a in logic:
        n = int(a, 16)
        classes[scope(sub.get(n) or "", cls.get(n))].append(a)
    sim_head = subprocess.run(["git", "-C", str(args.sim_rs), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
    result = {
        "schema": "siedler-scope-classes.v1",
        "generated_by": "scripts/scope/make_scope_classes.py",
        "sim_rs_commit": sim_head,
        "inventory_sha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
        "classes": {k: {"why": WHY[k], "count": len(classes[k])} for k in "ABCD"},
        "frontier": sorted(f"{a:x}" for a in frontier if f"{a:x}" in set(classes["A"])),
        "addresses": {k: classes[k] for k in "ABCD"},
    }
    if sum(len(v) for v in classes.values()) != len(logic):
        raise SystemExit("scope classes lost addresses")
    args.out.write_text(json.dumps(result, separators=(",", ":")) + "\n")
    print(json.dumps(result["classes"]), "frontier", len(result["frontier"]))


if __name__ == "__main__":
    main()
