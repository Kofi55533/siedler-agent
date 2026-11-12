#!/usr/bin/env bash
# Isolated replay-observation validation using the existing remeasure runner.
set -euo pipefail
source "$(dirname "$0")/s5-env.sh"
work="$RUNNER_TEMP/s5-observation"
mkdir -p "$work" "$S5/live"
log="$S5/live/bc-full.log"
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

if [ "$1" != rng ]; then
  echo "Running verification gate $1"
  cd recomp
  if [ "$1" = blocktest ]; then
    cargo build --release -j 2 -p s5blocktest >> "$log" 2>&1
    python verify.py blocktest --jobs 2 >> "$log" 2>&1
  else
    cargo build --release -j 2 -p s5recomp --bin s5recomp >> "$log" 2>&1
    python verify.py "$1" >> "$log" 2>&1
  fi
  exit
fi

export CARGO_TARGET_DIR="$S5_REPO/recomp/target"
echo "Building replay comparison"
cd recomp
cargo build --release -j 2 -p s5verify >> "$log" 2>&1
sc="$S5_V/fixtures/bc-full/scenario.json"
img="$S5_ARTIFACTS/bc/gs_h.img"
export S5_REMAP=$(python -c "import verify; print(verify.remap_for('$img', 'target/verify/observation-rng-bc-full'))")
echo "Scanning shared RNG from the BC Full starting image"
rc=0
/usr/bin/time -f 'wall %e s' target/release/s5verify observe-rng ../data/scope/schema.json "$sc" "$img" >> "$log" 2>&1 || rc=$?
[ "$rc" -le 1 ]
grep -oE '^bc-full: (DIVERGENT turn [0-9]+|IDENTICAL [0-9]+ ticks)' "$log"
