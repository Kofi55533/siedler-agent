#!/usr/bin/env python3
"""One-off (static) generator for function_inventory.json: the whole-EXE function list of SettlersHoK.exe
(Ghidra export, siedler-forschung cloud-re/results/rust_handoff/source/functions.jsonl, 39,740 functions) with a
game-logic class filter. Run on the box, not in CI (the inputs are large/private); CI only reads the result.

Class rules mirror siedler-sim-rs recomp/inventory.py (direct class: name `Class::method`, AddHandlers handler map,
RTTI vtable owner, Lua API; neighbour anchors within 40 functions; callers vote, 4 rounds) and the drop filter of
tools/coverage_map.py (CRT 0x5C0000-0x5D8A00, DROP_CLASS_RE). Unclassified functions are KEPT as logic (like
coverage_map.py), so the logic total is an upper bound.

usage: make_function_inventory.py <siedler-forschung root> <out.json> [<decompiled_all/_index.json>]"""
import bisect, collections, hashlib, json, os, re, subprocess, sys

FO = sys.argv[1]
OUT = sys.argv[2]
R = os.path.join(FO, "cloud-re", "results")
DECL = os.path.join(R, "rust_handoff", "inputs", "declarations")
FJ = os.path.join(R, "rust_handoff", "source", "functions.jsonl")
CRT = (0x5C0000, 0x5D8A00)
CRT_NAMES = ("_", "operator", "FID_conflict", "FindMITargetTypeInstance", "PMDtoOffset", "Catch@", "Unwind@")
DROP_CLASS_RE = re.compile(
    r"runtime|^crt$|^(std|stl)|d3d|direct3d|widget|^EGUI|^GGUI|button|tooltip|window|sound|audio|"
    r"^Lua|script|platform|registry|filesystem|^BB::(CBinary|CFile)|serial|observer", re.I)

fs = [json.loads(l) for l in open(FJ)]
entries = sorted((int(f["addr"], 16), f) for f in fs)
classes = json.load(open(os.path.join(DECL, "classes.json")))
by_name = {c["name"]: c for c in classes}
owner = {}
for c in classes:
    bases = [by_name[b] for b in c.get("bases_direct", []) if b in by_name]
    for vt in c.get("vtables", []):
        for i, m in enumerate(vt["methods"]):
            inh = any(i < len(bvt["methods"]) and bvt["methods"][i] == m
                      for b in bases for bvt in b.get("vtables", []) if bvt["this_offset"] == vt["this_offset"])
            if not inh:
                owner.setdefault(int(m, 16), c["name"])
handlers = {}
for h in json.load(open(os.path.join(DECL, "handler_map.json"))):
    if h.get("method"):
        handlers.setdefault(int(h["method"], 16), h["cls"])
lua = {int(x["func"], 16) for x in json.load(open(os.path.join(DECL, "lua_api.json")))}
callers = {}
idx = sys.argv[3] if len(sys.argv) > 3 else os.path.join(FO, "wissen", "binaeranalyse", "decompiled_all", "_index.json")
if os.path.exists(idx):
    for a, v in json.load(open(idx)).items():
        callers[int(a, 16)] = [int(c, 16) for c in v.get("callers", [])]


def direct(e, name):
    if CRT[0] <= e < CRT[1] or (name or "").startswith(CRT_NAMES):
        return "(runtime library)", "crt"
    if name and name.startswith("std::"):
        return "(runtime library)", "stl"
    if name and "::" in name and not name.startswith("RTTI_shared"):
        return name.rsplit("::", 1)[0], "name"
    if e in handlers:
        return handlers[e], "handler"
    if e in owner:
        return owner[e], "vtable"
    if e in lua:
        return "LuaAPI", "lua"
    return None, None


cls = {}
how = collections.Counter()
anchor = []
for e, f in entries:
    c, h = direct(e, f["name"])
    cls[e] = c
    anchor.append(c)
    if h:
        how[h] += 1
for i, (e, f) in enumerate(entries):
    if cls[e]:
        continue
    prev = next((c for c in reversed(anchor[max(0, i - 40):i]) if c), None)
    nxt = next((c for c in anchor[i + 1:i + 41] if c), None)
    if prev and prev == nxt and not prev.startswith("("):
        cls[e] = prev; how["addr"] += 1
for _ in range(4):
    ch = 0
    for e, _f in entries:
        if cls[e]:
            continue
        votes = collections.Counter(cls[c] for c in callers.get(e, []) if cls.get(c))
        if votes:
            (c, k), = votes.most_common(1)
            if k * 2 > sum(votes.values()) and not c.startswith("("):
                cls[e] = c
            elif len(votes) > 1:
                cls[e] = "(shared helper)"
            else:
                continue
            ch += 1; how["callers"] += 1
    if not ch:
        break

drop_reason = collections.Counter()
logic = []
rows = []
for e, f in entries:
    hi = max(int(b, 16) for _a, b in f["body_ranges"]) if f["body_ranges"] else e
    lo = min(int(a, 16) for a, _b in f["body_ranges"]) if f["body_ranges"] else e
    c = cls[e] or ""
    dropped = CRT[0] <= e < CRT[1] or bool(DROP_CLASS_RE.search(c))
    if dropped:
        drop_reason["crt" if (CRT[0] <= e < CRT[1] or c == "(runtime library)") else DROP_CLASS_RE.search(c).group(0).lower()] += 1
    rows.append(f"{e:06x}{hi - e:+x}" + ("" if dropped else "L"))
    if not dropped:
        logic.append(e)

fo_rev = subprocess.run(["git", "-C", FO, "log", "-1", "--format=%h %cI", "--", os.path.relpath(FJ, FO)],
                        capture_output=True, text=True).stdout.strip()
manifest = json.load(open(os.path.join(R, "rust_handoff", "source", "export_manifest.json")))
out = {
    "schema": "siedler-function-inventory.v1",
    "static": True,
    "note": "Statisch erzeugt (scripts/scoreboard/make_function_inventory.py auf der Box), nicht in CI neu berechnet.",
    "module": "SettlersHoK.exe (nur EXE; keine DLLs)",
    "source": "Kofi55533/siedler-forschung cloud-re/results/rust_handoff/source/functions.jsonl",
    "source_commit": fo_rev,
    "source_sha256": hashlib.sha256(open(FJ, "rb").read()).hexdigest(),
    "ghidra_export": {"functions": manifest.get("functions"), "decompile_success": manifest.get("decompile_success")},
    "class_rules": "recomp/inventory.py (Klassenzuordnung) + tools/coverage_map.py (DROP_CLASS_RE, CRT 0x5C0000-0x5D8A00); "
                   "Aufrufer aus wissen/binaeranalyse/decompiled_all/_index.json; unklassifizierte Funktionen zählen als Logik (Obergrenze)",
    "total_functions": len(entries),
    "logic_functions": len(logic),
    "unclassified_kept_as_logic": sum(1 for e in logic if not cls[e]),
    "dropped": dict(drop_reason.most_common()),
    "classified_by": dict(how),
    "other_inventories": [
        {"what": "Ghidra-Dekompilat aller 73 PE-Images der Gold-Edition (EXE, Karteneditor, Qt, DLLs, Tools)",
         "functions": 205748, "exe_part": 39740, "where": "Google Drive siedler-gold-pe-decompilation-2026-10-03-structured_teil01/02-von-02.zip (manifest/coverage.json)"},
        {"what": "älterer Ghidra-Export nur EXE", "functions": 28162,
         "where": "siedler-forschung wissen/binaeranalyse/decompiled_all/_index.json (Laptop: Desktop/siedler_colab_training/decompiled_all)"},
    ],
    "functions": rows,
}
json.dump(out, open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))
print(json.dumps({k: v for k, v in out.items() if k != "functions"}, ensure_ascii=False, indent=1))
