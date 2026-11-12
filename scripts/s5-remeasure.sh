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
  gh api --method PUT "$path" --input "$work/report.json" >/dev/null 2>&1 || echo "Private report upload failed"
  echo "Oracle observation $mode exit=$rc"
  exit "$rc"
}
trap save EXIT
cd "$S5_REPO"
for attempt in $(seq 1 150); do
  git fetch -q origin "$S5_REMEASURE_REF" >> "$log" 2>&1
  git checkout -q --detach FETCH_HEAD >> "$log" 2>&1
  [ -f tools/oracle_task_hashes.c ] && break
  sleep 10
done
test -f tools/oracle_task_hashes.c
rev=$(git rev-parse HEAD)
echo "candidate $rev" >> "$log"
cc -O3 -shared -fPIC tools/oracle_task_hashes.c -o "$work/task-hasher.so" >> "$log" 2>&1
hasher=(--task-hasher "$work/task-hasher.so")
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
start_turn=$(jq -er '.start_turn' "$sc")
terminal_turn=$(jq -er '.start_turn + .turns' "$sc")
checkpoint_turn=$((start_turn + 16))
ticks=()
checkpoints=()
if [ "$mode" = smoke ]; then
  ticks=(--ticks 32)
  checkpoints=(--checkpoint-dir "$work/checkpoints" --checkpoint-at "$checkpoint_turn")
fi
if [ "$mode" = full ]; then
  checkpoints=(--checkpoint-dir "$work/checkpoints")
  for turn in 4000 7000 10000 13000 15000 17000 19000 20500 22000 23000; do
    checkpoints+=(--checkpoint-at "$turn")
  done
fi
if [ "$mode" = benchmark ]; then
  for backend in python native; do
    args=()
    if [ "$backend" = native ]; then args=("${hasher[@]}"); fi
    echo "Measuring full recording with $backend task hashes" | tee -a "$log"
    python tools/oracle_observe.py --scenario "$sc" --snapshot "$snapshot" --game "$S5_GAME" \
      --runtime "$S5_ARTIFACTS/boot/runtime" --output "$work/benchmark-$backend.jsonl.gz" \
      --ticks 512 --benchmark full "${args[@]}" 2>> "$log" | tee -a "$log"
  done
  cmp <(gzip -dc "$work/benchmark-python.jsonl.gz") <(gzip -dc "$work/benchmark-native.jsonl.gz")
  echo "Python and native recorder output is identical" | tee -a "$log"
  exit 0
fi
repo=Kofi55533/siedler-sim-rs
tag="oracle-bc-full-rng-$rev"
if [ "$mode" = full ]; then
  gh release view "$tag" -R "$repo" >/dev/null 2>&1 ||
    gh release create "$tag" -R "$repo" --target "$rev" --prerelease \
      --title "BC Full original Oracle RNG and state recording" --notes "Native checkpoints arrive during recording. The complete observation profile is available when bc-full-oracle.jsonl.gz is present." >> "$log" 2>&1
fi
stream_progress() {
  local line old path branch tick
  path="repos/Kofi55533/siedler-sim-rs/contents/results/oracle-progress-$GITHUB_RUN_ID-$mode.txt"
  branch=codex/replay-observation-results
  while IFS= read -r line; do
    echo "$line"
    if [[ "$mode" = full && "$line" =~ ^Oracle\ (recorded|recording|checkpoint) ]]; then
      printf '%s\n' "$line" >> "$work/progress.txt"
      old=$(gh api "$path?ref=$branch" --jq .sha 2>/dev/null || true)
      base64 -w0 "$work/progress.txt" | jq -Rs --arg branch "$branch" --arg old "$old" \
        '{message:"Update Oracle progress",branch:$branch,content:.} + (if $old=="" then {} else {sha:$old} end)' > "$work/progress.json"
      gh api --method PUT "$path" --input "$work/progress.json" >/dev/null 2>&1 || true
      if [[ "$line" =~ ^Oracle\ checkpoint\ at\ tick\ ([0-9]+)$ ]]; then
        tick="${BASH_REMATCH[1]}"
        gh release upload "$tag" -R "$repo" "$work/checkpoints/turn-$tick.bin" --clobber >> "$log" 2>&1 || true
      fi
    fi
  done
}
echo "Starting original Oracle recording ($mode)"
python tools/oracle_observe.py --scenario "$sc" --snapshot "$snapshot" --game "$S5_GAME" \
  --runtime "$S5_ARTIFACTS/boot/runtime" --output "$record" "${ticks[@]}" "${checkpoints[@]}" "${hasher[@]}" \
  2>> "$log" | tee -a "$log" | stream_progress
if [ "$mode" = full ]; then
  gzip -t "$record"
  python tools/oracle_observe.py show "$record" "$terminal_turn" > "$work/terminal.json" 2>> "$log"
  echo "Validated the complete archive and terminal tick $terminal_turn" >> "$log"
  gh release upload "$tag" -R "$repo" "$record" --clobber >> "$log" 2>&1
  uploaded=$(gh release view "$tag" -R "$repo" --json assets --jq '.assets[].name' 2>> "$log")
  for image in "$work/checkpoints/"*.bin; do
    if ! grep -Fxq "$(basename "$image")" <<< "$uploaded"; then
      gh release upload "$tag" -R "$repo" "$image" --clobber >> "$log" 2>&1
    fi
  done
else
  checkpoint="$work/checkpoints/turn-$checkpoint_turn.bin"
  test -s "$checkpoint"
  python - "$sc" "$work/window.json" "$checkpoint_turn" <<'PY'
import json
import sys
from pathlib import Path

original, window = map(Path, sys.argv[1:3])
turn = int(sys.argv[3])
scenario = json.loads(original.read_text())
commands_path = scenario.get("commands")
commands = []
if commands_path:
    commands = [json.loads(line) for line in (original.parent / commands_path).read_text().splitlines()
                if line.strip()]
commands = [command for command in commands if turn <= command["turn"] < turn + 16]
(window.parent / "commands.jsonl").write_text("".join(json.dumps(command) + "\n" for command in commands))
scenario.update(start_turn=turn, turns=16, commands="commands.jsonl")
window.write_text(json.dumps(scenario))
PY
  python tools/oracle_observe.py --scenario "$work/window.json" --snapshot "$checkpoint" --game "$S5_GAME" \
    --runtime "$S5_ARTIFACTS/boot/runtime" --output "$work/resumed.jsonl.gz" 2>> "$log" | tee -a "$log"
  python tools/oracle_observe.py slice "$record" "$work/window.json" "$work/expected.jsonl.gz" 2>> "$log"
  python - "$work/expected.jsonl.gz" "$work/resumed.jsonl.gz" >> "$log" 2>&1 <<'PY'
import gzip
import json
import sys
from itertools import zip_longest

with gzip.open(sys.argv[1], "rt") as expected, gzip.open(sys.argv[2], "rt") as resumed:
    next(expected)
    next(resumed)
    for want, have in zip_longest(expected, resumed):
        if want is None or have is None or json.loads(want) != json.loads(have):
            raise ValueError("resumed Oracle trace differs from the uninterrupted run")
print("Oracle checkpoint replay matched all recorded fields and events")
PY
  python tools/oracle_observe.py --scenario "$sc" --snapshot "$snapshot" --game "$S5_GAME" \
    --runtime "$S5_ARTIFACTS/boot/runtime" --output "$work/native-rng-probe.jsonl.gz" \
    --ticks 1 --probe-rng 2>> "$log" | tee -a "$log"
fi
export CARGO_TARGET_DIR="$S5_REPO/recomp/target"
cd crates/s5clean
cargo build --locked --release -j 2 --example observe >> "$log" 2>&1
rc=0
limit=()
if [ "$mode" = smoke ]; then limit=(32); fi
/usr/bin/time -f 'wall %e s' "$CARGO_TARGET_DIR/release/examples/observe" "$sc" "$img" "$record" "${limit[@]}" >> "$log" 2>&1 || rc=$?
if [ "$mode" = smoke ]; then
  [ "$rc" = 0 ]
  grep -oE '^bc-full: IDENTICAL 32 ticks' "$log"
else
  [ "$rc" -le 1 ]
  grep -oE '^bc-full: (DIVERGENT turn [0-9]+|IDENTICAL [0-9]+ ticks)' "$log"
fi
