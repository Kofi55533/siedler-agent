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
  local endpoint="repos/Kofi55533/siedler-sim-rs/contents/results/bc-full-observation-$GITHUB_RUN_ID.txt"
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
trap save_result EXIT
cd "$S5_REPO"
git fetch -q origin "$S5_REMEASURE_REF"
git checkout -q --detach FETCH_HEAD
rev=$(git rev-parse HEAD)
echo "candidate $rev" | tee -a "$log"

if [ "$1" != bc-full ]; then
  echo "Gate $1" >> "$log"
  cd recomp
  python verify.py "$1" >> "$log" 2>&1
  exit
fi

export CARGO_TARGET_DIR="$S5_REPO/recomp/target"
echo "Building replay comparison"
cargo test --manifest-path crates/s5diff/Cargo.toml --lib observation::tests >> "$log" 2>&1
cd recomp
cargo build --release -p s5verify >> "$log" 2>&1
cd ../crates/s5clean
cargo build --release --example observe >> "$log" 2>&1
clean="$CARGO_TARGET_DIR/release/examples/observe"
verify="$CARGO_TARGET_DIR/release/s5verify"
cd "$S5_REPO/recomp"
sc="$S5_V/fixtures/bc-full/scenario.json"
img="$S5_ARTIFACTS/bc/gs_h.img"
schema=../data/scope/schema.json
export S5_REMAP=$(python -c "import verify; print(verify.remap_for('$img', 'target/verify/observation-bc-full'))")
cache="$work/bc-full.jsonl.gz"
ticks=300
echo "Running BC Full live and cached prefix"
run_comparison() {
  local file=$1 rc=0
  shift
  /usr/bin/time -f 'wall %e s' "$@" > "$file" 2>&1 || rc=$?
  cat "$file" >> "$log"
  return "$rc"
}
live_rc=0
run_comparison "$work/live" "$verify" observe-live "$schema" "$sc" "$img" "$ticks" || live_rc=$?
[ "$live_rc" -le 1 ]
run_comparison "$work/record" "$verify" observe-record "$schema" "$sc" "$img" "$cache" "$rev" "$ticks"
wc -c < "$cache" | sed 's/^/Reference cache bytes: /' >> "$log"
cd "$S5_REPO/crates/s5clean"
cached_rc=0
run_comparison "$work/cached" "$clean" "$sc" "$img" "$cache" "$ticks" || cached_rc=$?
[ "$cached_rc" = "$live_rc" ]
grep -E '^bc-full: (DIVERGENT|IDENTICAL)' "$work/live" > "$work/live.verdict"
grep -E '^bc-full: (DIVERGENT|IDENTICAL)' "$work/cached" > "$work/cached.verdict"
diff -u "$work/live.verdict" "$work/cached.verdict" >> "$log"
grep -oE '^bc-full: (DIVERGENT turn [0-9]+|IDENTICAL [0-9]+ ticks)' "$work/live.verdict" || true
