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
  grep -q 's5_entity_rows' tools/oracle_task_hashes.c 2>/dev/null && break
  sleep 10
done
grep -q 's5_entity_rows' tools/oracle_task_hashes.c
rev=$(git rev-parse HEAD)
echo "candidate $rev" >> "$log"
cc -O3 -shared -fPIC tools/oracle_task_hashes.c -o "$work/task-hasher.so" >> "$log" 2>&1
hasher=(--task-hasher "$work/task-hasher.so" --entity-reader "$work/task-hasher.so")
python -m pip install --quiet pefile capstone >> "$log" 2>&1
snapshot="$S5_ARTIFACTS/lift/snap20h.bin"
if [ ! -s "$snapshot" ]; then
  echo "Fetching original Oracle start snapshot"
  mkdir -p "$(dirname "$snapshot")"
  curl --fail --silent --show-error --location --retry 3 \
    -H "Authorization: Bearer $GH_TOKEN" -H 'Accept: application/vnd.github.raw+json' \
    "https://api.github.com/repos/Kofi55533/siedler-forschung/contents/cloud-re/artifacts/recomp/lift/snap20h.bin" \
    -o "$snapshot" >> "$log" 2>&1
fi
test "$(git hash-object "$snapshot")" = 6937fb76baaf2772eb1a5cd2783e5d0d3c9778c2
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
test "$mode" = wintersturm-checkpoint
sc="$S5_REPO/fixtures/wintersturm/scenario.json"
img="$work/start.img"
record="$work/wintersturm-oracle.jsonl.gz"
turn=$(python - "$snapshot" <<'PY'
import pickle
import struct
import sys
import zlib

with open(sys.argv[1], "rb") as stream:
    snapshot = pickle.load(stream)
regions = {base: compressed for base, compressed in snapshot["mem"]}
decoded = {}
def read(address):
    for base in sorted((base for base in regions if base <= address), reverse=True):
        if base not in decoded:
            decoded[base] = zlib.decompress(regions[base])
        offset = address - base
        if offset + 4 <= len(decoded[base]):
            return struct.unpack_from("<I", decoded[base], offset)[0]
    raise ValueError(f"missing snapshot address {address:#x}")
print(read(read(read(0x895DAC) + 0x1C)))
PY
)
ticks=128
python tools/oracle_observe.py window-scenario "$sc" "$work/window.json" "$turn" "$ticks" >> "$log" 2>&1
python recomp/export_image.py "$snapshot" "$img" --mem-only >> "$log" 2>&1
python tools/oracle_observe.py --scenario "$work/window.json" --snapshot "$snapshot" --game "$S5_GAME" \
  --runtime "$S5_ARTIFACTS/boot/runtime" --output "$record" --ticks "$ticks" --movement --player-resources \
  "${hasher[@]}" >> "$log" 2>&1
export CARGO_TARGET_DIR="$S5_REPO/recomp/target"
cd crates/s5clean
cargo build --locked --release -j 2 --example observe >> "$log" 2>&1
for scope in movement resources rng-all; do
  rc=0
  /usr/bin/time -f 'wall %e s' "$CARGO_TARGET_DIR/release/examples/observe" "$work/window.json" "$img" \
    "$record" "--$scope" > "$work/compare.txt" 2>&1 || rc=$?
  cat "$work/compare.txt" >> "$log"
  [ "$rc" -le 1 ]
  grep -qE '^wintersturm: (IDENTICAL|DIVERGENT)' "$work/compare.txt"
done
tag="oracle-wintersturm-checkpoint-$GITHUB_RUN_ID"
assets=("$record" "$work/window.json")
if [ -s "$work/window.commands.jsonl" ]; then
  assets+=("$work/window.commands.jsonl")
else
  jq '.commands = ""' "$work/window.json" > "$work/no-commands.json"
  mv "$work/no-commands.json" "$work/window.json"
fi
gh release create "$tag" -R Kofi55533/siedler-sim-rs --target "$rev" --prerelease \
  --title "Wintersturm checkpoint observation" --notes "Original replay and window scenario." \
  "${assets[@]}" >> "$log" 2>&1
