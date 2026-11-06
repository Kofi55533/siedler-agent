#!/usr/bin/env python3
"""The Bauplan: bauplan.py <out.json.gz> <vtables.json> <atlas.json.gz>...

Merges field atlases (crates/s5clean tests/field_atlas.rs: per native class, which byte of the reference object feeds
which field of the clean import) into ranges `[first, last, fields]` per class: consecutive bytes that feed the same
leaf fields form one range, and a byte only counts when it feeds at most MAX_LEAVES leaves (a discriminator such as
the type id feeds every field). Byte names are `+0x14` in the object itself, or `[+0x78][+0x0]+0x10` through pointer
words. With the vtables (method address -> classes), a worker's context pack shows the field map of the classes whose
vtable holds the function. The output is private (siedler-data); this script prints counts only.
"""
import gzip
import json
import re
import sys
from collections import defaultdict

MAX_LEAVES = 2


def leaves(fields):
    fields = [f for f in fields if f != "!error"]
    # a parent is a dotted prefix of another field (linear: a type id byte feeds thousands of fields)
    parents = {g[:i] for g in fields for i, c in enumerate(g) if c == "."}
    return tuple(sorted(f for f in set(fields) if f not in parents))


def split(name):
    """`[+0x78][+0x0]+0x10` -> ('[+0x78][+0x0]', 0x10)"""
    m = re.fullmatch(r"(.*?)\+0x([0-9a-f]+)", name)
    return (m.group(1), int(m.group(2), 16)) if m else (name, None)


def class_name(rtti):
    """`.?AVCSettler@GGL@@` -> `GGL::CSettler`"""
    parts = rtti.removeprefix(".?AV").removeprefix(".?AU").rstrip("@").split("@")
    return "::".join(reversed([p for p in parts if p]))


def main(out, vtables, *atlases):
    feeds = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))  # class -> byte -> leaves -> count
    validated = defaultdict(set)
    for path in atlases:
        for cls, entry in json.load(gzip.open(path)).items():
            for byte, fields in entry["bytes"].items():
                if "!error" in fields:
                    validated[cls].add(byte)
                found = leaves(fields)
                if 1 <= len(found) <= MAX_LEAVES:
                    feeds[cls][byte][found] += 1
    classes = {}
    for cls, bytes_ in feeds.items():
        by_base = defaultdict(dict)
        for byte, options in bytes_.items():
            base, offset = split(byte)
            if offset is not None:
                by_base[base][offset] = max(options, key=options.get)
        ranges = []
        for base in sorted(by_base, key=lambda b: (b.count("["), b)):
            run = None
            for offset in sorted(by_base[base]):
                found = by_base[base][offset]
                if run and run[1] + 1 == offset and run[2] == found:
                    run[1] = offset
                else:
                    run and ranges.append(run)
                    run = [offset, offset, found, base]
            run and ranges.append(run)
        classes[cls] = [{"at": f"{r[3]}+{r[0]:#x}", "size": r[1] - r[0] + 1, "fields": list(r[2]),
                         "validated": f"{r[3]}+{r[0]:#x}" in validated[cls]} for r in ranges]
    methods = defaultdict(set)
    for table in json.load(open(vtables)).values():
        for slot in table["slots"]:
            methods[int(slot, 16)].add(class_name(table["class"]))
    doc = {"classes": classes, "methods": {f"{a:#x}": sorted(c) for a, c in sorted(methods.items())}}
    with gzip.open(out, "wt") as f:
        json.dump(doc, f)
    covered = sum(1 for cs in methods.values() if cs & classes.keys())
    print(f"bauplan: {len(classes)} classes, {sum(len(r) for r in classes.values())} ranges, "
          f"{len(methods)} vtable methods, {covered} of them on a mapped class")


def pack(bauplan, addr, limit=120, most=2):
    """The context-pack lines for function `addr` (context.py): the field map of the mapped classes whose vtable
    holds it (a base method is in many: the `most` with the fewest ranges, the base's own fields come first)."""
    classes = [c for c in bauplan.get("methods", {}).get(f"{addr:#x}", []) if c in bauplan["classes"]]
    classes = sorted(classes, key=lambda c: len(bauplan["classes"][c]))[:most]
    if not classes:
        return []
    md = ["## Field map of the object (the Bauplan)", "",
          "Measured on reference images: the bytes of the native object (`this`) and what they feed in the clean",
          "import (`World`; a path names a field of the entity as `crates/s5clean` imports it). `+0x14` is the object",
          "itself, `[+0x78][+0x0]+0x10` follows pointer words. A native read of such a byte is a read of that field.",
          ""]
    for cls in classes:
        md += [f"### {cls}", "", "| bytes | clean field |", "|---|---|"]
        for r in bauplan["classes"][cls][:limit]:
            md.append(f"| `{r['at']}` ({r['size']}) | {', '.join(f'`{f}`' for f in r['fields'])} |")
        md.append("")
    return md


if __name__ == "__main__":
    main(*sys.argv[1:])
