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
git checkout -q --detach 5809e743877446ae87cded19841c8dd532b1eeb3 >> "$log" 2>&1
echo "comparator $(git rev-parse HEAD)" >> "$log"
sc="$S5_V/fixtures/bc-full/scenario.json"
img="$S5_ARTIFACTS/bc/gs_h.img"
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
gzip -t "$record"
wc -c < "$record" | sed 's/^/Reference bytes: /' >> "$log"
python tools/oracle_observe.py show "$record" 23900 > "$work/terminal.json" 2>> "$log"
echo "Validated gzip and decoded the original terminal tick 23900" >> "$log"
cd crates/s5clean
check() {
  echo "Compare $*" >> "$log"
  rc=0
  /usr/bin/time -f 'wall %e s' "$CARGO_TARGET_DIR/release/examples/observe" "$sc" "$img" "$record" "$@" > "$work/check.log" 2>&1 || rc=$?
  cat "$work/check.log" >> "$log"
  if [ "${1:-}" = 32 ]; then
    [ "$rc" = 0 ]
  else
    [ "$rc" -le 1 ]
  fi
  grep -oE '^bc-full: (DIVERGENT turn [0-9]+|IDENTICAL [0-9]+ ticks)' "$work/check.log"
}
check 32
check
check --rng-only
