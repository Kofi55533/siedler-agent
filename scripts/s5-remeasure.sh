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
case "$1" in
  boot)
    echo "Running boot verification"
    cargo build --release -j 2 -p s5gfx --bin s5render >> "$log" 2>&1
    python verify.py boot >> "$log" 2>&1
    ;;
  blocktest)
    echo "Running block verification with the original checkpoint"
    cargo build --release -j 2 -p s5blocktest >> "$log" 2>&1
    mkdir -p "$S5_ARTIFACTS/blocktest"
    for file in g20h.img g20h.img.state.json; do
      endpoint="repos/Kofi55533/siedler-forschung/contents/cloud-re/artifacts/recomp/blocktest/$file"
      gh api -H 'Accept: application/vnd.github.raw+json' "$endpoint" > "$S5_ARTIFACTS/blocktest/$file" 2>> "$log"
    done
    python verify.py blocktest --jobs 2 >> "$log" 2>&1
    ;;
  state|cache)
    echo "Building observation tools"
    cargo build --release -j 2 -p s5verify >> "$log" 2>&1
    sc="$S5_V/fixtures/bc-full/scenario.json"
    img="$S5_ARTIFACTS/bc/gs_h.img"
    verify="$CARGO_TARGET_DIR/release/s5verify"
    export S5_REMAP=$(python -c "import verify; print(verify.remap_for('$img', 'target/verify/observation-full-bc'))")
    if [ "$1" = state ]; then
      echo "Comparing state and RNG across BC Full"
      rc=0
      /usr/bin/time -f 'wall %e s' "$verify" observe-live ../data/scope/schema.json "$sc" "$img" >> "$log" 2>&1 || rc=$?
      [ "$rc" -le 1 ]
      grep -oE '^bc-full: (DIVERGENT turn [0-9]+|IDENTICAL [0-9]+ ticks)' "$log"
    else
      echo "Recording the full BC reference for repeat comparisons"
      tag="observation-bc-full-$rev"
      cache="$work/bc-full-reference.jsonl.gz"
      /usr/bin/time -f 'wall %e s' "$verify" observe-record ../data/scope/schema.json "$sc" "$img" "$cache" "$rev" >> "$log" 2>&1
      wc -c < "$cache" | sed 's/^/Reference cache bytes: /' >> "$log"
      sha256sum "$cache" >> "$log"
      gh release view "$tag" -R Kofi55533/siedler-sim-rs >/dev/null 2>&1 ||
        gh release create "$tag" -R Kofi55533/siedler-sim-rs --target "$rev" --prerelease --title "BC Full reference observation" --notes "Reusable reference data for the BC Full replay." >> "$log" 2>&1
      gh release upload "$tag" -R Kofi55533/siedler-sim-rs "$cache" --clobber >> "$log" 2>&1
      cd "$S5_REPO/crates/s5clean"
      cargo build --release -j 2 --example observe >> "$log" 2>&1
      rc=0
      /usr/bin/time -f 'wall %e s' "$CARGO_TARGET_DIR/release/examples/observe" "$sc" "$img" "$cache" >> "$log" 2>&1 || rc=$?
      [ "$rc" -le 1 ]
      grep -oE '^bc-full: (DIVERGENT turn [0-9]+|IDENTICAL [0-9]+ ticks)' "$log"
    fi
    ;;
  *) echo "Unknown validation job"; exit 2 ;;
esac
