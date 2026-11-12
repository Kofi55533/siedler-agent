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
for attempt in $(seq 1 50); do
  git fetch -q origin "$S5_REMEASURE_REF" >> "$log" 2>&1
  git checkout -q --detach FETCH_HEAD >> "$log" 2>&1
  [ -f tools/oracle_observe.py ] && break
  sleep 10
done
test -f tools/oracle_observe.py
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
sc="$S5_V/fixtures/bc-full/scenario.json"
img="$S5_ARTIFACTS/bc/gs_h.img"
record="$work/bc-full-oracle.jsonl.gz"
ticks=()
if [ "$mode" = smoke ]; then ticks=(--ticks 32); fi
echo "Starting original Oracle recording ($mode)"
# The recorder's stdout contains only progress counters. Full diagnostics stay private.
python tools/oracle_observe.py --scenario "$sc" --snapshot "$snapshot" --game "$S5_GAME" \
  --runtime "$S5_ARTIFACTS/boot/runtime" --output "$record" "${ticks[@]}" 2>> "$log" | tee -a "$log"
wc -c < "$record" | sed 's/^/Reference bytes: /' >> "$log"
if [ "$mode" = full ]; then
  tag="oracle-bc-full-$rev"
  gh release view "$tag" -R Kofi55533/siedler-sim-rs >/dev/null 2>&1 ||
    gh release create "$tag" -R Kofi55533/siedler-sim-rs --target "$rev" --prerelease \
      --title "BC Full Oracle observation" --notes "Oracle reference and recorded coverage for BC Full." >> "$log" 2>&1
  gh release upload "$tag" -R Kofi55533/siedler-sim-rs "$record" --clobber >> "$log" 2>&1
fi
export CARGO_TARGET_DIR="$S5_REPO/recomp/target"
cd crates/s5clean
cargo build --locked --release -j 2 --example observe >> "$log" 2>&1
limit=()
if [ "$mode" = smoke ]; then limit=(32); fi
rc=0
/usr/bin/time -f 'wall %e s' "$CARGO_TARGET_DIR/release/examples/observe" "$sc" "$img" "$record" "${limit[@]}" >> "$log" 2>&1 || rc=$?
if [ "$mode" = smoke ]; then
  [ "$rc" = 0 ]
  grep -oE '^bc-full: IDENTICAL 32 ticks' "$log"
else
  [ "$rc" -le 1 ]
  grep -oE '^bc-full: (DIVERGENT turn [0-9]+|IDENTICAL [0-9]+ ticks)' "$log"
fi
