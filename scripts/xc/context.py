#!/usr/bin/env python3
"""Context pack for one worker: context.py <kind> <base-dir> <native-source-dir> <repo> <out.md>

Everything a model needs to fix one kind, gathered by the script so the model does not search for it:
the measured differences at the original's real calls (base-dir/only/*.json), the native functions of the kind
(a task kind: its handlers in data/engine/task_handlers.json; an entity tick TICK_<CLASS>_<type>: the class's tick;
a function FN_<address>: that function, plus how to write its binding) with their decompilation and that of their
direct callees (code.jsonl, read by offset through functions.jsonl), and where the clean code handles the kind today.
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
        c = json.loads(f.read(entry[1])).get("c")  # some records carry only the asm
    return c.replace("\r\n", "\n").strip() if c else None


BINDING_API = """A binding is one file `crates/s5xcheck/src/functions/fn_{low}.rs` (collected by build.rs into
`s5xcheck::functions::ALL`; nothing else to register). It exports `pub const BINDING: Binding` and translates the
native call into a call of the clean implementation, nothing more: read the arguments from the entry (`entry.this()`
= ECX, `entry.arg(i)` = i-th stack argument, `entry.r32(addr)` = native memory), map native pointers to clean ids
(an entity object's id is at `+8`), and call crates/s5clean. Any logic belongs in crates/s5clean, not in the binding.
The port computes its result from the clean `World` (its entities, map, players, ...), which is imported from the
same state: never from native memory read at the entry, and never by handing a memory reader or a native address to
crates/s5clean (verify.sh rejects `|_world|` ports and `|address| entry.r32(address)` readers). Native containers,
allocators and CRT helpers (std::map/vector/list walks, sizes, iterators, memcpy, operator new) have no clean
counterpart: Rust collections in World replace them. When the function is such a helper, port the game behaviour
it serves in its caller instead; it is then covered by the caller's comparison.
The API (crates/s5xcheck/src/capture.rs):

- `Call::world(label, |world| ...)`: the whole effect is on the world; after the call the reference memory is
  imported again and compared with your port's world.
- `Call::returning(label, |world| -> u32 ...)`: also compares the return value (EAX).
- `Call {{ label, port: Box::new(|world| vec![..]), reference: Box::new(|mach| vec![..]) }}`: compares any values,
  e.g. out-parameters: capture the pointer at entry, read `mach.m.r32(ptr)` in `reference`, return the same values
  from `port`. The calls only count as verified when they are observed: the world changed, or values are compared.
- return `None` for calls the port does not cover (they are then neither compared nor counted as samples).

An existing binding as the pattern:

```rust
{example}
```
"""


def function_pack(kind, base, src, repo, funcs):
    addr = int(kind.removeprefix("FN_"), 16)
    low = f"{addr:x}"
    name = funcs.get(addr, (0, 0, "?"))[2]
    md = [f"# Function {kind}: native `{name}` at {addr:#x}", ""]
    bound = (repo / f"crates/s5xcheck/src/functions/fn_{low}.rs").is_file()
    md += ["## What to do", "",
           (f"The binding exists and some sampled calls differ: fix the clean implementation (crates/s5clean) until "
            f"they are identical." if bound else
            f"This function has no binding yet: write the clean implementation of what it does (crates/s5clean: "
            f"extend the existing code where it already does this, a new function where not) and its binding "
            f"`crates/s5xcheck/src/functions/fn_{low}.rs`. Then every sampled real call of the original is compared "
            f"with your port on the same state."), "",
           "## Binding", ""]
    examples = sorted((repo / "crates/s5xcheck/src/functions").glob("fn_*.rs"))
    good = [e for e in examples if e.name == "fn_57a683.rs"]  # an id to a World query: the pattern to follow
    example = (good or examples)[0].read_text().strip() if examples else ""
    md += [BINDING_API.format(low=low, example=example), ""]
    md += ["## Calls of the original in the scenarios", ""]
    shown = 0
    for f in sorted((base / "only").glob("*.json")):
        g = json.loads(f.read_text())["groups"].get(kind)
        if not g:
            continue
        md.append(f"- scenario `{f.stem}`: {g['entries']} calls" + ("" if g.get("unbound") else
                  f", {g['failing']}/{g['samples']} sampled calls differ, {g.get('observed', 0)} observed"))
        for ex in g["examples"][: max(0, 6 - shown)]:
            shown += 1
            md += ["", f"### {f.stem} turn {ex['turn']}: {ex['label']}", "```diff",
                   ("\n".join(ex["diff"]) if isinstance(ex["diff"], list) else ex["diff"]).strip()[:4000], "```"]
    md.append("")
    return md, {name: f"{addr:#010x}"}, [f"{addr:#x}"], shown


def main(kind, base, src, repo, out):
    base, src, repo = Path(base), Path(src), Path(repo)
    tick = next(((p, c) for p, c in TICKS.items() if kind.startswith(p)), None)
    funcs = index(src)
    if kind.startswith("FN_"):
        md, handlers, search, shown = function_pack(kind, base, src, repo, funcs)
    elif tick:
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
    for f in sorted((base / "only").glob("*.json")) if not kind.startswith("FN_") else []:
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
