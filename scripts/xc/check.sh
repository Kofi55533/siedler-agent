#!/usr/bin/env bash
# check.sh <kind> [all]: the model's own check while it works. Rebuilds the capture tests on the current checkout and
# compares <kind> at the original's real calls in the scenarios where it failed (all: every scenario where it runs).
# Prints per-scenario counts and the first remaining difference. The script-side verdict is verify.sh, not this.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/lib.sh"
KIND=$1; WHICH=${2:-failing}
xc_build || { tail -n 40 "$XC/build.log" | grep -vE '^\s*Compiling'; exit 1; }
scen=$(cat "$XC/scenarios-$WHICH.txt")
rm -rf "$XC/try"; xc_scenarios | awk -v s=" $scen " 'index(s, " "$1" ")' | xc_runs "$XC/try" "$KIND"
python3 - "$XC/try" "$KIND" <<'P'
import json, sys
from pathlib import Path
total = bad = observed = 0; first = None; unbound = False
for f in sorted(Path(sys.argv[1]).glob("*.json")):
    g = json.loads(f.read_text())["groups"].get(sys.argv[2])
    if not g:
        print(f"{f.stem}: no call of the kind sampled"); continue
    if g.get("unbound"):
        unbound = True; print(f"{f.stem}: {g['entries']} calls, not bound yet (write the binding)"); continue
    total += g["samples"]; bad += g["failing"]; observed += g.get("observed", 0)
    print(f"{f.stem}: {g['failing']}/{g['samples']} sampled calls differ"
          + (f", {g.get('observed', 0)} observed" if "observed" in g else ""))
    if g["examples"] and first is None:
        first = (f.stem, g["examples"][0])
if first:
    d = first[1]["diff"]; d = "\n".join(d) if isinstance(d, list) else d
    print(f"\nfirst difference ({first[0]} turn {first[1]['turn']}, {first[1]['label']}):\n{d}")
fn = sys.argv[2].startswith("FN_")
if fn and total and not bad and not observed:
    print("\nnone of the sampled calls changed anything the comparison sees: compare the result or the written values "
          "(Call::returning, or a reference closure)")
done = total and not bad and not unbound and (observed or not fn)
print(f"\n{sys.argv[2]}: {bad} of {total} sampled calls differ" + (" -- DONE" if done else ""))
P
for n in $scen; do [ -s "$XC/try/$n.json" ] || { echo "$n: run failed or crashed, last lines:"; tail -n 15 "$XC/run-$n-only.log"; }; done
