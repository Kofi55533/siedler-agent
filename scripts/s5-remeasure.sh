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
candidate=$(cat "$GITHUB_WORKSPACE/remeasure-ref.txt")
[[ "$candidate" =~ ^[0-9a-f]{40}$ ]]
git fetch -q origin "$candidate" >> "$log" 2>&1
git checkout -q --detach FETCH_HEAD >> "$log" 2>&1
rev=$(git rev-parse HEAD)
[[ "$rev" = "$candidate" ]]
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
baseline=oracle-bc-full-windows-43396536719
tag="oracle-bc-full-windows-$GITHUB_RUN_ID"
start=$(jq -er '.start_turn' "$sc")
end=$(jq -er '.start_turn + .turns' "$sc")
config="$GITHUB_WORKSPACE/remeasure-shards.txt"
mapfile -t windows < <(grep -E '^[0-9]+-[0-9]+$' "$config")
fetch_asset() {
  local tag_name=$1 name=$2 dest=$3 attempt
  for attempt in $(seq 1 180); do
    if gh release download "$tag_name" -R "$repo" -p "$name" -D "$dest" --clobber > "$work/download.txt" 2>&1; then
      return
    fi
    sleep 30
  done
  printf 'Missing release asset %s/%s\n' "$tag_name" "$name" >> "$log"
  cat "$work/download.txt" >> "$log"
  return 1
}

if [ "$mode" = join ]; then
  if ! gh release view "$tag" -R "$repo" >/dev/null 2>&1; then
    gh release create "$tag" -R "$repo" --target "$rev" --prerelease \
    --title "BC Full parallel Oracle recording" \
    --notes "These are repeatable windows from validated native checkpoints. bc-full-oracle.jsonl.gz appears after the join and validation against the previous Oracle reference." >> "$log" 2>&1
  fi
  printf "%s\n" "$rev" > "$work/ready.txt"
  gh release upload "$tag" -R "$repo" "$work/ready.txt" --clobber >> "$log" 2>&1
  segments=()
  for window in "${windows[@]}"; do
    fetch_asset "$tag" "window-$window.jsonl.gz" "$work"
    fetch_asset "$tag" "time-$window.txt" "$work"
    segments+=("$work/window-$window.jsonl.gz")
  done
  /usr/bin/time -f 'join wall %e s' python tools/oracle_observe.py join "$sc" "$record" "${segments[@]}" >> "$log" 2>&1
  gzip -t "$record"
  mkdir -p "$work/original"
  fetch_asset "$baseline" bc-full-oracle.jsonl.gz "$work/original"
  python - "$work/original/bc-full-oracle.jsonl.gz" "$record" "$work" >> "$log" 2>&1 <<'PY'
import gzip
from itertools import zip_longest
import json
from pathlib import Path
import sys
import zlib

original, joined, work = map(Path, sys.argv[1:])
row_fields = {
    "script_callbacks": ("script_callbacks",),
    "ai_calls": ("ai_calls",),
    "lua_graph": ("lua_graph_updates", "lua_graph_removed"),
    "deferred_activity": ("deferred_activity",),
}
path_fields = {"path_heap": "heap", "path_predicates": "predicates", "path_owners": "owner"}
fields = (*row_fields, *path_fields, "ai_village")
compressors = {name: zlib.compressobj(1, wbits=31) for name in fields}
totals = dict.fromkeys(fields, 0)
changed = dict.fromkeys(fields, 0)
with gzip.open(original, "rt") as a, gzip.open(joined, "rt") as b:
    old, new = json.loads(next(a)), json.loads(next(b))
    for key in ("start", "turns", "scenario", "recorded_commands", "snapshot_sha256", "observation_profile"):
        if old[key] != new[key]:
            raise ValueError(f"joined recording differs in {key}")
    n = 0
    for raw_a, raw_b in zip_longest(a, b):
        if raw_a is None or raw_b is None:
            raise ValueError("joined recording has a different number of rows")
        n += 1
        prev, have = json.loads(raw_a), json.loads(raw_b)
        recorded = {name: [have.pop(key) for key in keys] for name, keys in row_fields.items()}
        for name, key in path_fields.items():
            recorded[name] = [search.pop(key) for search in have["path_searches"]]
        recorded["ai_village"] = [item for item in have["ai_state_updates"] if item[0].startswith("village:")]
        have["ai_state_updates"] = [item for item in have["ai_state_updates"] if not item[0].startswith("village:")]
        village_removed = [key for key in have["ai_state_removed"] if key.startswith("village:")]
        recorded["ai_village"].extend(village_removed)
        have["ai_state_removed"] = [key for key in have["ai_state_removed"] if not key.startswith("village:")]
        for name, values in recorded.items():
            changed[name] += any(value is not None for value in values) if name == "path_owners" else any(values)
            data = json.dumps(values, separators=(",", ":")).encode() + b"\n"
            totals[name] += len(compressors[name].compress(data))
        if prev != have:
            raise ValueError(f"joined observations differ entering tick {prev['state']['turn']}")
    assert n == new["turns"] + 1
    print(f"{n} state rows and all previous events equal to the verified Oracle archive")
for name, compressor in compressors.items():
    totals[name] += len(compressor.flush())
    assert changed[name]
    print(f"{name}: {changed[name]} populated rows, {totals[name]} compressed bytes alone")
timings = {path.stem[5:]: float(path.read_text()) for path in work.glob("time-*.txt")}
slowest = max(timings, key=timings.get)
print(f"Each window, Oracle process seconds: {timings}")
print(f"Slowest {slowest}: {timings[slowest]:.2f} seconds")
print(f"Sequential process time of the same windows: {sum(timings.values()):.2f} seconds")
print(f"Joined archive {joined.stat().st_size} bytes")
PY
  python tools/oracle_observe.py coverage "$record" > "$work/recipient-coverage.json" 2>> "$log"
  cat "$work/recipient-coverage.json" >> "$log"
  jq -e '.mapped == 45746 and .unmapped == 0 and .state.attachments.ticks == 23901 and .state.lua_globals.ticks == 23901 and .state.movement.ticks == 23901 and .player_resource_ticks == 23901 and .state.deferred_events.ticks == 23901 and .state.script_triggers.ticks == 23901 and .state.ai_state.ticks == 23901 and .state.terrain_state.ticks == 23901 and .resource_notifications.recorded and .resource_notifications.events > 0 and .path_searches.recorded and .path_searches.coarse > 0 and .path_searches.fine > 0 and .script_dispatches.recorded and .script_dispatches.events > 0 and .script_callbacks.recorded and .script_callbacks.action > 0 and .ai_calls.recorded and .ai_calls.calls.player > 0 and .path_heap.recorded and .path_heap.push > 0 and .path_heap.pop > 0' "$work/recipient-coverage.json" >> "$log"
  jq -e '.state.lua_graph.ticks == 23901 and .ai_village.recorded and .ai_village.updates > 0 and .deferred_activity.recorded and .deferred_activity.enqueue > 0 and
    .deferred_activity.deliver > 0 and .path_predicates.recorded and .path_predicates.walkable > 0 and
    .path_owners.recorded and ([.path_owners.sites[].mapped] | add > 0)' "$work/recipient-coverage.json" >> "$log"
  assets=( "$work/recipient-coverage.json" )
  if [ "$(stat -c %s "$record")" -lt 2147483648 ]; then assets+=( "$record" ); fi
  gh release upload "$tag" -R "$repo" "${assets[@]}" --clobber >> "$log" 2>&1
  cd crates/s5clean
  export CARGO_TARGET_DIR="$S5_REPO/recomp/target"
  cargo build --locked --release -j 2 --example observe >> "$log" 2>&1
  for mode_name in combined lua-graph deferred-activity ai-calls ai-village path-searches script-callbacks; do
    flags=()
    if [[ "$mode_name" != combined ]]; then flags+=( "--$mode_name" ); fi
    verdict="$work/compare-$mode_name.txt"
    rc=0
    /usr/bin/time -f 'wall %e s' "$CARGO_TARGET_DIR/release/examples/observe" "$sc" "$img" "$record" \
      "${flags[@]}" > "$verdict" 2>&1 || rc=$?
    cat "$verdict" >> "$log"
    [ "$rc" -le 1 ]
    grep -qE '^bc-full: (DIVERGENT (entering )?turn [0-9]+|IDENTICAL [0-9]+ ticks)' "$verdict"
  done
  gh release upload "$tag" -R "$repo" "$work"/compare-*.txt --clobber >> "$log" 2>&1
  grep -E '(^[0-9]+ state rows|^Slowest |^Sequential process time|^Joined archive)' "$log"
  exit
fi

[[ "$mode" =~ ^([0-9]+)-([0-9]+)$ ]]
from="${BASH_REMATCH[1]}"
to="${BASH_REMATCH[2]}"
[[ "$start" -le "$from" && "$from" -lt "$to" && "$to" -le "$end" ]]
if [ "$from" != "$start" ]; then
  echo "Waiting for native checkpoint $from"
  fetch_asset "$original" "turn-$from.bin" "$work"
  snapshot="$work/turn-$from.bin"
fi
fetch_asset "$tag" ready.txt "$work"
window="$work/window-$mode.jsonl.gz"
echo "Recording $from through $to"
time_file="$work/time-$mode.txt"
/usr/bin/time -f '%e' -o "$time_file" python tools/oracle_observe.py --scenario "$sc" --snapshot "$snapshot" \
  --game "$S5_GAME" --runtime "$S5_ARTIFACTS/boot/runtime" --output "$window" --from-turn "$from" \
  --ticks "$((to - from))" --movement --movement-reader "$work/task-hasher.so" \
  --player-resources --attachments --lua-globals --lua-graph --resource-notifications --deferred-events \
  --deferred-activity --script-triggers --ai-village --terrain-state --path-heap --path-predicates --path-owners \
  --script-dispatches --script-callbacks --ai-calls "${hasher[@]}" >> "$log" 2>&1
gzip -t "$window"
gh release upload "$tag" -R "$repo" "$window" "$time_file" --clobber >> "$log" 2>&1
