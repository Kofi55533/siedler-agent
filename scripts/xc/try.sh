#!/usr/bin/env bash
# try.sh <scenario>: the function census point (capture-functions) next to the entity-tick point (capture-ticks) on
# one scenario of the current $S5_REPO checkout: the bound tick functions must see the calls capture-ticks sees, and
# the inventory run shows what counting the unported functions costs. Prints counts only.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/lib.sh"
t0=$(date +%s); xc_build || exit 2; echo "build secs=$(( $(date +%s) - t0 ))"
read -r name img skip turns < <(xc_scenarios | awk -v n="$1" '$1==n')
[ -n "${img:-}" ] || { echo "no scenario $1"; exit 2; }
# a trial compares at most XC_TRY_TURNS turns: the census plays 1200 in about 2400 s, past XC_TIMEOUT
turns=$(( turns < ${XC_TRY_TURNS:-300} ? turns : ${XC_TRY_TURNS:-300} )); echo "turns: $turns"
# the inventory (xc_build): every class-A function of scripts/scope_classes.json, as the census counts them
echo "inventory: $(wc -l < "$XC/inventory.txt") functions"
one(){ # one <tag> <bin> [env...]
  local tag=$1 bin=$2; shift 2; local t=$(date +%s)
  ( cd "$S5_REPO" && env S5_XCHECK_IMAGE="$img" S5_CAPTURE_SKIP="$skip" S5_CAPTURE_TURNS="$turns" S5_CAPTURE_MAX="$XC_MAX" \
      S5_CAPTURE_STRIDE="$XC_STRIDE" S5_CAPTURE_JSON="$XC/$tag.json" "$@" \
      timeout -k 30 "$XC_TIMEOUT" "$(xc_bin "$bin")" --nocapture --test-threads=1 ) < /dev/null > "$XC/try-$tag.log" 2>&1
  echo "$tag: exit $? secs=$(( $(date +%s) - t)) json=$([ -s "$XC/$tag.json" ] && echo yes || echo no)"
}
# the bound functions' blocks (capture::Want): the functions run names the calls that first ran blocks no compared
# call ran, the wanted run compares those calls too (what the next census does with the previous one's work list)
blocks=; [ -f "$S5_REPO/data/scope/fn_blocks.json" ] && blocks=$S5_REPO/data/scope/fn_blocks.json
# XC_TRY_RUNS (e.g. `functions`): only these runs, for a small trial
runs(){ [ -z "${XC_TRY_RUNS:-}" ] || [[ ",$XC_TRY_RUNS," == *",$1,"* ]]; }
rm -f "$XC"/one-*.txt "$XC"/ticks.json "$XC"/functions.json "$XC"/inventory.json "$XC"/wanted.json
# the runs are single-threaded and independent: side by side on the runner's cores (each ~0.7 GB RSS)
runs ticks && one ticks capture-ticks > "$XC/one-ticks.txt" &
runs functions && one functions capture-functions S5_CAPTURE_BLOCKS="$blocks" > "$XC/one-functions.txt" &
runs inventory && one inventory capture-functions S5_CAPTURE_FUNCTIONS="$XC/inventory.txt" > "$XC/one-inventory.txt" &
wait; cat "$XC"/one-*.txt 2>/dev/null
if runs wanted && [ -n "$blocks" ] && [ -s "$XC/functions.json" ]; then
  python3 "$D/xc.py" want-file "$XC/functions.json" "$XC/want.txt"
  one wanted capture-functions S5_CAPTURE_BLOCKS="$blocks" S5_CAPTURE_WANT="$XC/want.txt"
fi
python3 - "$XC" <<'P'
import json, sys
from pathlib import Path
xc = Path(sys.argv[1])
def load(t):
    p = xc / f"{t}.json"
    return json.loads(p.read_text()) if p.is_file() and p.stat().st_size else None
ticks, fns, inv = load("ticks"), load("functions"), load("inventory")
cls = {"TICK_BUILDING_": "FN_4AF630", "TICK_SETTLER_": "FN_4A6A1B", "TICK_ANIMAL_": "FN_511DF9"}
for prefix, fn in cls.items():
    t = [g for k, g in (ticks or {}).get("groups", {}).items() if k.startswith(prefix)]
    for tag, doc in (("functions", fns), ("inventory", inv)):
        g = (doc or {}).get("groups", {}).get(fn, {})
        print(f"{prefix:15} ticks entries={sum(x['entries'] for x in t)} differ={sum(x['failing'] for x in t)}/{sum(x['samples'] for x in t)}"
              f" | {tag} entries={g.get('entries')} differ={g.get('failing')}/{g.get('samples')} observed={g.get('observed')}")
if inv:
    un = [g for g in inv["groups"].values() if g.get("unbound")]
    print(f"inventory: {len(un)} unbound functions called, {sum(g['entries'] for g in un)} calls, {len(inv['import_errors'])} import errors")
    # the callers (Group::callers): how many unbound functions run only inside bound ones (covered once those are
    # identical), how many are called from the turn itself
    bound = {k for k, g in inv["groups"].items() if not g.get("unbound")}
    c = [g.get("callers", {}) for g in un]
    print(f"callers: {sum(1 for x in c if x)} of {len(un)} unbound with callers, "
          f"{sum(1 for x in c if x and all(k in bound for k in x))} only inside bound functions, "
          f"{sum(1 for x in c if 'ROOT' in x)} called from the turn itself")
for t in ("ticks", "functions", "inventory", "wanted"):
    d = load(t)
    if d: print(f"{t}: {d['entries']} entries, {len(d['import_errors'])} import errors")
    # where the time went (capture::Profile, seconds and field sizes only)
    p = (d or {}).get("profile")
    if p:
        print(f"{t} profile: " + " ".join(f"{k}={p[k]:.1f}" if isinstance(p[k], float) else f"{k}={p[k]}"
                                          for k in ("total", "samples", "import", "key", "bind", "native", "port", "diff",
                                                    "skipped") if k in p))
        print(f"{t} largest fields: " + " ".join(f"{k}={v}" for k, v in p.get("fields", {}).items()))
    b = (d or {}).get("blocks")
    if b:  # Report::blocks: the recomp blocks the compared calls ran (counts only)
        n = lambda s: len(s.split(",")) if s else 0
        eq, df = set(b["equal"].split(",")) - {""}, set(b["differing"].split(",")) - {""}
        ex = set(b.get("executed", "").split(",")) - {""}
        print(f"{t}: blocks equal={n(b['equal'])} differing={n(b['differing'])} only-differing={len(df - eq)} "
              f"executed={len(ex)} executed-unverified={len(ex - eq)} fresh={n(b.get('fresh', ''))}")
        print(f"{t}: compared calls={sum(g.get('samples', 0) for g in d['groups'].values())}")
P
