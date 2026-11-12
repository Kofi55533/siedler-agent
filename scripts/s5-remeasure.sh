#!/usr/bin/env bash
# Isolated replay-observation validation using the existing remeasure runner.
set -euo pipefail
source "$(dirname "$0")/s5-env.sh"
work="$RUNNER_TEMP/s5-observation"
mkdir -p "$work" "$S5/live"
log="$work/bc-full.log"
: > "$log"

save_result() {
  local rc=$? previous request
  trap - EXIT
  # Store compact diagnostics in the private simulation repository.
  request="$work/request.json"
  local endpoint="repos/Kofi55533/siedler-sim-rs/contents/results/bc-full-observation-$GITHUB_RUN_ID-$1.txt"
  local branch=codex/replay-observation-results
  previous=$(gh api "$endpoint?ref=$branch" --jq .sha 2>/dev/null || true)
  base64 -w0 "$log" | jq -Rs --arg branch "$branch" --arg sha "$previous" \
    '{message:"Record BC Full observation",branch:$branch,content:.}+ (if $sha == "" then {} else {sha:$sha} end)' > "$request"
  if ! gh api --method PUT "$endpoint" --input "$request" >/dev/null 2>&1; then
    echo "private observation report upload failed"
    exit 1
  fi
  echo "private observation report saved (exit=$rc)"
  exit "$rc"
}
fixture=$1
trap 'save_result "$fixture"' EXIT
cd "$S5_REPO"
git fetch -q origin "$S5_REMEASURE_REF"
git checkout -q --detach FETCH_HEAD
rev=$(git rev-parse HEAD)
echo "candidate $rev" | tee -a "$log"

export CARGO_TARGET_DIR="$S5_REPO/recomp/target"
cd recomp
cargo build --release -j 2 -p s5verify >> "$log" 2>&1
sc="$S5_V/fixtures/bc-full/scenario.json"
img="$S5_ARTIFACTS/bc/gs_h.img"
verify="$CARGO_TARGET_DIR/release/s5verify"
tag="observation-bc-full-$rev"
schema="$S5_REPO/data/scope/schema.json"
export S5_REMAP=$(python -c "import verify; print(verify.remap_for('$img', 'target/verify/observation-window-bc'))")
echo "Preparing the BC Full checkpoint"
mkdir -p "$work/window"
"$verify" window "$schema" "$sc" "$img" 6841 2 "$work/window" >> "$log" 2>&1
python - "$work/window/commands.jsonl" >> "$log" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as commands:
    for row in commands:
        command = json.loads(row)
        print("window command:", command["turn"], command["class"])
PY
rc=0
/usr/bin/time -f 'window wall %e s' "$verify" observe-live "$schema" "$work/window/scenario.json" "$work/window/start.img" >> "$log" 2>&1 || rc=$?
[ "$rc" -le 1 ]
if grep -Eq '^bc-full-at6841: DIVERGENT turn 6842: tasks.hurt_guard' "$log"; then
  cache="bc-full-at-6841.tar.zst"
  tar -I 'zstd -3' -cf "$work/$cache" -C "$work/window" scenario.json start.img start.img.state.json commands.jsonl
  gh release upload "$tag" -R Kofi55533/siedler-sim-rs "$work/$cache" --clobber >> "$log" 2>&1
fi
echo "Checking the first 32 ticks with the shared reference file"
gh release download "$tag" -R Kofi55533/siedler-sim-rs -p 'bc-full-reference.jsonl.gz' -D "$work" >> "$log" 2>&1
cd "$S5_REPO/crates/s5clean"
cargo build --release -j 2 --example observe >> "$log" 2>&1
/usr/bin/time -f 'prefix wall %e s' "$CARGO_TARGET_DIR/release/examples/observe" "$sc" "$img" "$work/bc-full-reference.jsonl.gz" 32 >> "$log" 2>&1
grep -E '^bc-full: IDENTICAL 32 ticks' "$log"
