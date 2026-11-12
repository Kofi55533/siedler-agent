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

git fetch -q origin "$S5_REMEASURE_REF" >> "$log" 2>&1
git checkout -q --detach 27b3d69f81ba52bcccb32704d95fef1752bc049e >> "$log" 2>&1
echo "comparator $(git rev-parse HEAD)" >> "$log"
record="$work/bc-full-oracle.jsonl.gz"
export CARGO_TARGET_DIR="$S5_REPO/recomp/target"
cd crates/s5clean
cargo build --locked --release -j 2 --example observe >> "$log" 2>&1
cd "$S5_REPO"
repo=Kofi55533/siedler-sim-rs
tag=oracle-bc-full-acc27e172e500233a61fcb6eb177142b5db664c8
echo "Waiting for the full Oracle reference"
until gh release download "$tag" -R "$repo" --pattern "$(basename "$record")" --dir "$work" --clobber >> "$log" 2>&1; do
  conclusion=$(gh api repos/Kofi55533/siedler-agent/actions/jobs/131292329653 --jq '.conclusion // ""' 2>> "$log")
  if [ -n "$conclusion" ]; then
    echo "Full job ended with $conclusion without a downloadable reference" >> "$log"
    exit 2
  fi
  sleep 30
done

old_tag=observation-bc-full-8d4a7e5aedb25f125346d22dc2315556847368f7
archive=bc-full-at-6841.tar.zst
window="$work/window"
slice=bc-full-at-6841-oracle.jsonl.gz
mkdir -p "$window"
gh release download "$old_tag" -R "$repo" --pattern "$archive" --dir "$work" >> "$log" 2>&1
tar -I zstd -xf "$work/$archive" -C "$window"
/usr/bin/time -f 'slice wall %e s' python tools/oracle_observe.py slice "$record" "$window/scenario.json" "$work/$slice" >> "$log" 2>&1
cd crates/s5clean
rc=0
/usr/bin/time -f 'cached window wall %e s' "$CARGO_TARGET_DIR/release/examples/observe" "$window/scenario.json" "$window/start.img" "$work/$slice" >> "$log" 2>&1 || rc=$?
[ "$rc" -le 1 ]
grep -oE '^bc-full-at6841: (DIVERGENT turn [0-9]+|IDENTICAL [0-9]+ ticks)' "$log"
if grep -q '^bc-full-at6841: DIVERGENT turn 6841' "$log"; then
  echo "The existing checkpoint differs at entry" >> "$log"
  exit 1
fi
gh release upload "$tag" -R "$repo" "$work/$slice" --clobber >> "$log" 2>&1
