#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/s5-env.sh"
mode=$1
work="$RUNNER_TEMP/oracle-observation"
mkdir -p "$work"
log="$work/result.txt"
: > "$log"
save() {
  rc=$?
  trap - EXIT
  path="repos/Kofi55533/siedler-sim-rs/contents/results/oracle-observation-$GITHUB_RUN_ID-$mode.txt"
  branch=codex/replay-observation-results
  old=$(gh api "$path?ref=$branch" --jq .sha 2>/dev/null || true)
  base64 -w0 "$log" | jq -Rs --arg branch "$branch" --arg old "$old" \
    '{message:"Record Oracle replay observation",branch:$branch,content:.}+ (if $old=="" then {} else {sha:$old} end)' > "$work/report.json"
  gh api --method PUT "$path" --input "$work/report.json" >/dev/null 2>&1 || { echo "Private report upload failed"; exit 2; }
  echo "Oracle observation $mode exit=$rc"
  exit "$rc"
}
trap save EXIT
cd "$S5_REPO"
for attempt in $(seq 1 150); do
  git fetch -q origin "ea319dba150082fdee08ac71dd3674b7a8164c21" >> "$log" 2>&1
  git checkout -q --detach FETCH_HEAD >> "$log" 2>&1
  grep -q '^def join_reference' tools/oracle_observe.py 2>/dev/null && break
  sleep 10
done
grep -q '^def join_reference' tools/oracle_observe.py
rev=$(git rev-parse HEAD)
echo "candidate $rev" >> "$log"
cc -O3 -shared -fPIC tools/oracle_task_hashes.c -o "$work/task-hasher.so" >> "$log" 2>&1
hasher=(--task-hasher "$work/task-hasher.so" --entity-reader "$work/task-hasher.so")
python -m pip install --quiet pefile capstone >> "$log" 2>&1
snapshot="$S5_ARTIFACTS/lift/snap_gs_h.bin"
if [ ! -s "$snapshot" ]; then
  echo "Fetching original Oracle start snapshot"
  mkdir -p "$(dirname "$snapshot")"
  curl --fail --silent --show-error --location --retry 3 \
    -H "Authorization: Bearer $GH_TOKEN" -H 'Accept: application/vnd.github.raw+json' \
    "https://api.github.com/repos/Kofi55533/siedler-forschung/contents/cloud-re/artifacts/recomp/lift/snap_gs_h.bin" \
    -o "$snapshot" >> "$log" 2>&1
fi
test "$(git hash-object "$snapshot")" = f6561c747709e308c2e0581fa366b243da2f7bfb
# data-v2 intentionally omits some media. Restore every original game file that
# the canonical snapshot still holds open, using the same installation it came from.
python - "$snapshot" "$S5_GAME" > "$work/missing-install.txt" 2>> "$log" <<'PY'
from pathlib import Path
import pickle
import sys

with Path(sys.argv[1]).open("rb") as file:
    snapshot = pickle.load(file)
install = Path(sys.argv[2])
paths = [value[1] for value in snapshot["handles"].values() if value[0] == "file"]
paths += [value[1] for value in snapshot["mods"].values()]
marker = "/cloud-re/inputs/game/"
missing = set()
for path in paths:
    path = path.replace("\\", "/")
    if marker in path:
        relative = Path(path.split(marker, 1)[1])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe snapshot installation path")
        if not (install / relative).is_file():
            missing.add(relative.as_posix())
print(*sorted(missing), sep="\n")
PY
while IFS= read -r path; do
  [ -n "$path" ] || continue
  url_path=$(python -c 'from urllib.parse import quote; import sys; print(quote(sys.argv[1]))' "$path")
  mkdir -p "$(dirname "$S5_GAME/$path")"
  curl --fail --silent --show-error --location --retry 3 \
    -H "Authorization: Bearer $GH_TOKEN" -H 'Accept: application/vnd.github.raw+json' \
    "https://api.github.com/repos/Kofi55533/siedler-forschung/contents/cloud-re/inputs/game/$url_path" \
    -o "$work/install-file" >> "$log" 2>&1
  mv "$work/install-file" "$S5_GAME/$path"
done < "$work/missing-install.txt"
sc="$S5_V/fixtures/bc-full/scenario.json"
img="$S5_ARTIFACTS/bc/gs_h.img"
record="$work/bc-full-oracle.jsonl.gz"

repo=Kofi55533/siedler-sim-rs
original=oracle-bc-full-rng-1f5fd5735587262c776fa6b48decd05d0c587630
baseline=oracle-bc-full-windows-43333869478
tag="oracle-bc-full-windows-$GITHUB_RUN_ID"
start=$(jq -er '.start_turn' "$sc")
end=$(jq -er '.start_turn + .turns' "$sc")
config="$GITHUB_WORKSPACE/remeasure-shards.txt"
mapfile -t windows < <(grep -E '^[0-9]+-[0-9]+$' "$config")
fetch_asset() {
  until gh release download "$1" -R "$repo" -p "$2" -D "$3" --clobber > "$work/download.txt" 2>&1; do
    sleep 30
  done
}

case "$mode" in
  early) turn=121; ticks=64 ;;
  middle) turn=10000; ticks=128 ;;
  economy) turn=17000; ticks=96 ;;
  *) exit 2 ;;
esac
if [ "$turn" != "$start" ]; then
  gh release download "$original" -R "$repo" -p "turn-$turn.bin" -D "$work" --clobber >> "$log" 2>&1
  snapshot="$work/turn-$turn.bin"
  img="$work/start.img"
  python recomp/export_image.py "$snapshot" "$img" --mem-only >> "$log" 2>&1
fi
export CARGO_TARGET_DIR="$S5_REPO/recomp/target"
(cd crates/s5clean && cargo build --locked --release -j 2 --example observe) >> "$work/build.txt" 2>&1 &
compile=$!
if ! wait "$compile"; then
  cat "$work/build.txt" >> "$log"
  exit 2
fi
cat "$work/build.txt" >> "$log"
obs=tools/oracle_observe.py
/usr/bin/time -f 'record wall %e s' python "$obs" --scenario "$sc" --snapshot "$snapshot" \
  --game "$S5_GAME" --runtime "$S5_ARTIFACTS/boot/runtime" --output "$record" --from-turn "$turn" \
  --ticks "$ticks" --movement --movement-reader "$work/task-hasher.so" --player-resources --attachments \
  --lua-globals --resource-notifications --deferred-events --script-triggers --ai-state --terrain-state --path-searches "${hasher[@]}" >> "$log" 2>&1
python "$obs" window-scenario "$sc" "$work/window.json" "$turn" "$ticks" >> "$log" 2>&1
python "$obs" coverage "$record" > "$work/coverage.json" 2>> "$log"
cat "$work/coverage.json" >> "$log"
jq -e --argjson n "$((ticks + 1))" '
  .resource_notifications.recorded and .state.deferred_events.ticks == $n and
  .state.script_triggers.ticks == $n and .state.script_triggers.updates > 0 and
  .state.ai_state.ticks == $n and .state.ai_state.updates > 0 and
  .state.terrain_state.ticks == $n and .state.terrain_state.updates > 0 and .path_searches.recorded
' "$work/coverage.json" >> "$log"
if [ "$mode" = middle ]; then
  jq -e '.resource_notifications.events > 0 and (.path_searches.fine + .path_searches.coarse) > 0' "$work/coverage.json" >> "$log"
fi
split="$((turn + ticks / 2))"
python "$obs" window-scenario "$sc" "$work/first.json" "$turn" "$((split - turn))" >> "$log" 2>&1
python "$obs" window-scenario "$sc" "$work/second.json" "$split" "$((turn + ticks - split))" >> "$log" 2>&1
python "$obs" slice "$record" "$work/first.json" "$work/first.jsonl.gz" >> "$log" 2>&1
python "$obs" slice "$record" "$work/second.json" "$work/second.jsonl.gz" >> "$log" 2>&1
python "$obs" join "$work/window.json" "$work/joined.jsonl.gz" "$work/first.jsonl.gz" "$work/second.jsonl.gz" >> "$log" 2>&1
python "$obs" coverage "$work/joined.jsonl.gz" > "$work/joined-coverage.json" 2>> "$log"
jq -e --slurpfile prior "$work/coverage.json" '
  .resource_notifications == $prior[0].resource_notifications and
  .state == $prior[0].state and .path_searches == $prior[0].path_searches and .mapped == $prior[0].mapped
' "$work/joined-coverage.json" >> "$log"
cd crates/s5clean
for lane in resource-notifications deferred-events script-triggers ai-state terrain-state path-searches resources movement attachments lua-globals all; do
  opts=()
  if [ "$lane" != all ]; then opts=("--$lane"); fi
  rc=0
  echo "Check $lane" >> "$log"
  /usr/bin/time -f 'compare wall %e s' "$CARGO_TARGET_DIR/release/examples/observe" \
    "$work/window.json" "$img" "$work/joined.jsonl.gz" "${opts[@]}" >> "$log" 2>&1 || rc=$?
  [ "$rc" -le 1 ]
done
echo "Observation archive recorded, reassembled and compared"
