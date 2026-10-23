#!/usr/bin/env bash
# verify.sh <kind>: the script-side verdict on a worker's change (the model's word counts for nothing).
#  1. scope: only crates/s5clean/** and docs/** change; no test removed; no hand-written Debug/PartialEq (the
#     comparison runs over the derived {:#?} dump, a custom impl could hide fields); importer only extended.
#  2. <kind> identical at every sampled call in every scenario where it runs ($XC/scenarios-all.txt).
#  3. the full comparison of the scenarios where <kind> failed shows no other kind newly differing.
#  4. crates/s5clean unit tests and the building-tick capture stay green.
# Prints counts and VERDICT PASS|FAIL only.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/lib.sh"
KIND=$1; BASE=$(cat "$XC/base.sha"); cd "$S5_REPO"
fail(){ echo "$*"; echo "VERDICT FAIL"; exit 1; }
bad=$(xc_changed | grep -vE '^(crates/s5clean/|docs/)' | head -3 | tr '\n' ' ')
[ -n "$bad" ] && fail "scope: changes outside crates/s5clean and docs: $bad (revert them)"
git add -A -- crates/s5clean docs
git diff --cached --quiet "$BASE" && fail "scope: no change"
del=$(git diff --cached --name-only --diff-filter=D "$BASE" -- crates/s5clean/tests | head -3 | tr '\n' ' ')
[ -n "$del" ] && fail "scope: test files deleted: $del"
P=$(git diff --cached -U0 "$BASE" -- crates/s5clean)
rt=$(grep -cE '^-\s*#\[test\]' <<<"$P"); at=$(grep -cE '^\+\s*#\[test\]' <<<"$P")
[ "$rt" -gt "$at" ] && fail "scope: $rt #[test] removed, $at added"
grep -qE '^\+.*impl[^{]*(fmt::)?Debug for|^\+.*impl[^{]*PartialEq(<[^>]*>)? for' <<<"$P" && fail "scope: hand-written Debug/PartialEq impl added (derive them)"
grep -qE '^-.*derive\([^)]*(Debug|PartialEq)' <<<"$P" && ! grep -qE '^\+.*derive\([^)]*(Debug|PartialEq)' <<<"$P" && fail "scope: derive(Debug/PartialEq) removed"
grep -qE '^\+.*(skip_debug|finish_non_exhaustive)' <<<"$P" && fail "scope: dump-hiding attribute added"
rl=$(git diff --cached -U0 "$BASE" -- crates/s5clean/src/reference.rs | grep -cE '^-[^-]')
[ "$rl" -gt 0 ] && fail "scope: $rl line(s) removed from the importer crates/s5clean/src/reference.rs (only extend it)"
echo "scope ok: $(git diff --cached --shortstat "$BASE")"
git reset -q
xc_build || fail "build failed"
rm -rf "$XC/new"
xc_scenarios | awk -v s=" $(cat "$XC/scenarios-all.txt") " 'index(s, " "$1" ")' | xc_runs "$XC/new/only" "$KIND"
xc_scenarios | awk -v s=" $(cat "$XC/scenarios-failing.txt") " 'index(s, " "$1" ")' | xc_runs "$XC/new/full"
python3 "$D/xc.py" check "$XC/base" "$XC/new" "$KIND" > "$XC/verdict.txt"; grep -v '^VERDICT' "$XC/verdict.txt"
grep -q 'VERDICT PASS' "$XC/verdict.txt" || fail "comparison failed"
timeout 2400 cargo test --release -q -j "${CARGO_BUILD_JOBS:-3}" --manifest-path crates/s5clean/Cargo.toml > "$XC/unit.log" 2>&1 \
  || fail "s5clean tests failed: $(grep -E '^test .* FAILED|^error' "$XC/unit.log" | head -3 | tr '\n' ' ')"
( S5_CAPTURE_TURNS=10 timeout 1800 "$(xc_bin capture-entity-tick)" --test-threads=1 ) > "$XC/tick.log" 2>&1 \
  || fail "building tick capture failed: $(grep -m1 -E 'building tick' "$XC/tick.log")"
echo "s5clean tests ok, building tick ok"
echo "VERDICT PASS"
