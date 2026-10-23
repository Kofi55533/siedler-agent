#!/usr/bin/env python3
"""Context pack for one worker: context.py <kind> <base-dir> <native-source-dir> <repo> <out.md>

Everything a model needs to fix one kind, gathered by the script so the model does not search for it:
the measured differences at the original's real calls (base-dir/only/*.json), the native functions of the kind
(a task kind: its handlers in data/engine/task_handlers.json; an entity tick TICK_<CLASS>_<type>: the class's tick)
with their decompilation and that of their direct callees (code.jsonl, read by offset through functions.jsonl), and
where the clean code handles the kind today.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

BUDGET = 60_000  # characters of decompiled code in the pack
# the entity ticks of the census (crates/s5xcheck/tests/capture-ticks.rs): kind prefix -> (class, native tick)
TICKS = {"TICK_BUILDING_": ("GGL::CBuilding", "0x004AF630"), "TICK_SETTLER_": ("GGL::CSettler", "0x004A6A1B"),
         "TICK_ANIMAL_": ("GGL::CAnimal", "0x00511DF9")}


def index(src):
    funcs = {}
    with open(src / "functions.jsonl") as f:
        for line in f:
            d = json.loads(line)
            funcs[int(d["addr"], 16)] = (d["code_offset"], d["code_record_bytes"], d["name"])
    return funcs


def code(src, funcs, addr):
    entry = funcs.get(addr)
    if not entry:
        return None
    with open(src / "code.jsonl", "rb") as f:
        f.seek(entry[0])
        return json.loads(f.read(entry[1]))["c"].replace("\r\n", "\n").strip()


def main(kind, base, src, repo, out):
    base, src, repo = Path(base), Path(src), Path(repo)
    tick = next(((p, c) for p, c in TICKS.items() if kind.startswith(p)), None)
    funcs = index(src)
    if tick:
        prefix, (cls, addr) = tick
        handlers, search = {cls: addr}, [kind.removeprefix(prefix)]
        md = [f"# Entity tick of type {kind.removeprefix(prefix)} ({cls}::Tick {addr})", "",
              "## Measured differences at the original's real calls", "",
              f"Each sample: the original's `{cls}::Tick` ({addr}, entity virtual +0x38, called once per turn by the",
              "entity manager 0x576117) on an entity of this type, against `World::tick_entity_with_id` on the same",
              "imported state. `-` lines are the original (reference), `+` lines are the clean port; the path names the",
              "field of `World`. A settler's or animal's tick runs its current task (ExecuteTask 0x57BF33): a difference",
              "inside a task handler belongs to that handler (crates/s5clean/src/entity/tasks.rs).", ""]
    else:
        th = json.loads((repo / "data/engine/task_handlers.json").read_text())
        handlers, search = th["handlers"].get(kind, {}), [kind]
        md = [f"# Task kind {kind} (native task type {th['enum'].get(kind)})", "",
              "## Measured differences at the original's real calls", "",
              "Each sample: the original's `ExecuteTask` (0x57BF33) on an entity whose current task is this kind,",
              "against `World::execute_current_task` on the same imported state. `-` lines are the original",
              "(reference), `+` lines are the clean port; the path names the field of `World`.", ""]
    shown = 0
    for f in sorted((base / "only").glob("*.json")):
        g = json.loads(f.read_text())["groups"].get(kind)
        if not g:
            continue
        md.append(f"- scenario `{f.stem}`: {g['failing']}/{g['samples']} sampled calls differ ({g['entries']} calls)")
        for ex in g["examples"][: max(0, 6 - shown)]:
            shown += 1
            md += ["", f"### {f.stem} turn {ex['turn']}: {ex['label']}", "```diff", ("\n".join(ex["diff"]) if isinstance(ex["diff"], list) else ex["diff"]).strip()[:4000], "```"]
        md.append("")

    md += ["## Native functions of this kind (class -> address)", ""]
    md += [f"- `{cls}`: `{addr or 'unknown (inline in the dispatcher)'}`" for cls, addr in sorted(handlers.items())] or ["- none listed"]
    md.append("")
    used, seen = 0, set()
    queue = [int(a, 16) for a in handlers.values() if a]
    callees = []
    for addr in queue:
        c = code(src, funcs, addr)
        if c is None or addr in seen:
            continue
        seen.add(addr)
        md += [f"### Decompilation {addr:#x} ({funcs[addr][2]})", "```c", c[:20000], "```", ""]
        used += min(len(c), 20000)
        callees += [int(x, 16) for x in re.findall(r"FUN_([0-9a-f]{8})", c)]
    for addr in dict.fromkeys(callees):
        if used > BUDGET or addr in seen:
            continue
        c = code(src, funcs, addr)
        if c is None or len(c) > 8000:
            continue
        seen.add(addr)
        md += [f"### Callee {addr:#x}", "```c", c, "```", ""]
        used += len(c)

    md += ["## Where the clean code handles it today", ""]
    pats = search + [a.lower().replace("0x00", "0x") for a in handlers.values() if a]
    hits = subprocess.run(["grep", "-rniE", "|".join(re.escape(p) for p in pats), "crates/s5clean/src"],
                          cwd=repo, capture_output=True, text=True).stdout.splitlines()
    md += [f"- `{h[:200]}`" for h in hits[:40]] or ["- nothing: the kind is not ported yet "
                                                     "(task dispatch: crates/s5clean/src/entity/tasks.rs, entity "
                                                     "ticks: crates/s5clean/src/world/entities.rs tick_entity)"]
    Path(out).write_text("\n".join(md) + "\n")
    print(f"context {kind}: {len(handlers)} handlers, {len(seen)} functions, {shown} examples, {len(hits)} clean hits")


if __name__ == "__main__":
    main(*sys.argv[1:6])
