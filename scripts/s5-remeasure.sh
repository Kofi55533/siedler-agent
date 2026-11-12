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
  [ -f tools/oracle_rng_lanes.py ] && break
  sleep 10
done
test -f tools/oracle_rng_lanes.py
rev=$(git rev-parse HEAD)
echo "candidate $rev" >> "$log"
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
ticks=()
checkpoints=()
if [ "$mode" = smoke ]; then
  ticks=(--ticks 32)
  checkpoints=(--checkpoint-dir "$work/checkpoints" --checkpoint-at 16)
fi
if [ "$mode" = full ]; then
  checkpoints=(--checkpoint-dir "$work/checkpoints")
  for turn in 4000 7000 10000 13000 15000 17000 19000 20500 22000 23000; do
    checkpoints+=(--checkpoint-at "$turn")
  done
fi
if [ "$mode" = benchmark ]; then
  for profile in engine seed full; do
    echo "Measuring $profile"
    python tools/oracle_observe.py --scenario "$sc" --snapshot "$snapshot" --game "$S5_GAME" \
      --runtime "$S5_ARTIFACTS/boot/runtime" --output "$work/benchmark-$profile.jsonl.gz" \
      --ticks 512 --benchmark "$profile" 2>> "$log" | tee -a "$log"
  done
  exit 0
fi
echo "Starting original Oracle recording ($mode)"
python tools/oracle_observe.py --scenario "$sc" --snapshot "$snapshot" --game "$S5_GAME" \
  --runtime "$S5_ARTIFACTS/boot/runtime" --output "$record" "${ticks[@]}" "${checkpoints[@]}" 2>> "$log" | tee -a "$log"
repo=Kofi55533/siedler-sim-rs
tag="oracle-bc-full-rng-$rev"
if [ "$mode" = full ]; then
  gzip -t "$record"
  python tools/oracle_observe.py show "$record" 23900 > "$work/terminal.json" 2>> "$log"
  gh release view "$tag" -R "$repo" >/dev/null 2>&1 ||
    gh release create "$tag" -R "$repo" --target "$rev" --prerelease \
      --title "BC Full original Oracle RNG and state recording" --notes "Complete BC Full tick range for the recorded profile. Private native checkpoint files support replay windows." >> "$log" 2>&1
  gh release upload "$tag" -R "$repo" "$record" --clobber >> "$log" 2>&1
  for image in "$work/checkpoints/"*.bin; do
    zstd -T2 -q -o "$image.zst" "$image"
    gh release upload "$tag" -R "$repo" "$image.zst" --clobber >> "$log" 2>&1
  done
else
  test -s "$work/checkpoints/turn-16.bin"
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
