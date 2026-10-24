#!/usr/bin/env python3
"""cmdbin.py <oracle dir> <out.bin> <commands.jsonl>...: the decoded command streams of generated fixtures
(siedler-sim-rs fixtures-gen/*/commands.jsonl, one {"turn","id","class","fields"} per line, absolute game turns) as one
S5CMD001 command file for the capture harness (S5_XCHECK_COMMANDS). The bytes come from the oracle's own layout
(oracle/commands.py, as recomp/commands_bin.py writes them); a command whose class it cannot lay out is left out.
Prints counts only."""
import json
import struct
import sys
import types

sys.path.insert(0, sys.argv[1])
sys.modules.setdefault("emu", types.SimpleNamespace(Call=None))  # layout needs no emulator (pefile/unicorn)
import commands as cm  # noqa: E402

cmds = []
for p in sys.argv[3:]:
    for line in open(p):
        if line.strip():
            c = json.loads(line)
            cmds.append((int(c["turn"]), int(c["id"]), c["class"], c.get("fields") or {}))
records, slots, skipped = [], {}, 0
# grouped by turn in stream order, slot = index within the turn (as recomp/commands_bin.py)
for t, eid, cls, f in sorted(cmds, key=lambda c: c[0]):
    try:
        a, writes = cm.layout(eid, cls, f, slots.get(t, 0))
    except (KeyError, TypeError, ValueError, struct.error):
        skipped += 1
        continue
    slots[t] = slots.get(t, 0) + 1
    records.append(struct.pack("<III", t, a, len(writes)) +
                   b"".join(struct.pack("<II", addr, len(d)) + d for addr, d in writes))
with open(sys.argv[2], "wb") as out:
    out.write(b"S5CMD001" + struct.pack("<I", len(records)) + b"".join(records))
print(f"{len(records)} commands, {skipped} without layout")
