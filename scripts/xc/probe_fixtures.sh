#!/usr/bin/env bash
# probe_fixtures.sh: what would the base fixtures of the $S5_REPO checkout (fixtures/*/scenario.json on a .img image,
# with their own player commands) add to the census? Per fixture capture-functions over its turns (at most 1200) with
# its commands: entries, import errors, inventory functions called, and how many of those the census work list does not
# know yet. Counts only.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/lib.sh"
t0=$(date +%s); xc_build || exit 2; echo "build secs=$(( $(date +%s) - t0 ))"
gh release download xc-worklist -R "${XC_DATA:-Kofi55533/siedler-data}" -D "$XC" -p worklist.json --clobber || exit 2
python3 -c 'import json,sys;w=json.load(open(sys.argv[1]));print("\n".join(sorted(w["kinds"])))' "$XC/worklist.json" > "$XC/known.txt"
echo "work list: $(wc -l < "$XC/known.txt") kinds"
mkdir -p "$XC/fx"
for s in "$S5_REPO"/fixtures/*/scenario.json; do
  n=$(basename "$(dirname "$s")"); [[ $n == *-extra ]] && continue
  read -r img start turns < <(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d["image"].replace(" ","_"),d["start_turn"],d["turns"])' "$s")
  [[ $img == *.img ]] || continue
  [[ $img == /* ]] || img=$S5_ARTIFACTS/$img
  [ -f "$img" ] || { echo "$n: image missing"; continue; }
  c=$(dirname "$s")/commands.jsonl
  if [ -s "$c" ]; then python3 "$D/cmdbin.py" "$S5_REPO/oracle" "$XC/fx/$n.bin" "$c" > /dev/null; else : > "$XC/fx/$n.bin"; fi
  t=$(( turns < 1200 ? turns : 1200 ))
  echo "$n $img $t"
done > "$XC/fx/list.txt"
echo "fixtures: $(wc -l < "$XC/fx/list.txt")"
one(){ # one <name> <img> <turns>
  local n=$1 img=$2 turns=$3 t=$(date +%s) rc
  ( cd "$S5_REPO" && env S5_XCHECK_IMAGE="$img" S5_CAPTURE_SKIP=0 S5_CAPTURE_TURNS="$turns" S5_CAPTURE_MAX="$XC_MAX" \
      S5_CAPTURE_STRIDE="$XC_STRIDE" S5_CAPTURE_JSON="$XC/fx/$n.json" S5_CAPTURE_FUNCTIONS="$XC/inventory.txt" \
      $([ -s "$XC/fx/$n.bin" ] && echo S5_XCHECK_COMMANDS="$XC/fx/$n.bin") \
      timeout -k 30 1800 "$(xc_bin capture-functions)" --nocapture --test-threads=1 ) < /dev/null > "$XC/fx/$n.log" 2>&1
  rc=$?
  python3 - "$XC/fx/$n.json" "$XC/known.txt" "$n" "$rc" "$(( $(date +%s) - t))" "$turns" "$XC/fx/$n.bin" <<'P'
import json, os, struct, sys
p, known, n, rc, secs, turns, cb = sys.argv[1:]
cmds = struct.unpack_from("<I", open(cb, "rb").read(), 8)[0] if os.path.getsize(cb) else 0
d = json.load(open(p)) if os.path.isfile(p) and os.path.getsize(p) else None
if d is None:
    print(f"{n}: exit {rc} secs={secs} turns={turns} commands={cmds} no result"); sys.exit()
k = set(open(known).read().split()); g = d.get("groups", {})
called = [f for f, x in g.items() if x.get("entries")]
print(f"{n}: exit {rc} secs={secs} turns={turns} commands={cmds} entries={d.get('entries')} "
      f"import_errors={len(d.get('import_errors', []))} called={len(called)} new={sum(1 for f in called if f not in k)} "
      f"failing={sum(1 for x in g.values() if x.get('failing'))}")
P
}
while read -r n img t; do
  one "$n" "$img" "$t" &
  while [ "$(jobs -rp | wc -l)" -ge "$XC_JOBS" ]; do wait -n; done
done < "$XC/fx/list.txt"; wait
