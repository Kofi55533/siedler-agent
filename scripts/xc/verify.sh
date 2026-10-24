#!/usr/bin/env bash
# verify.sh <kind>: the script-side verdict on a worker's change (the model's word counts for nothing).
#  1. scope: only crates/s5clean/** and docs/** change (a function kind FN_<address> also its binding
#     crates/s5xcheck/src/functions/fn_<address>.rs, which only translates the call); no test removed; no
#     hand-written Debug/PartialEq (the comparison runs over the derived {:#?} dump, a custom impl could hide
#     fields); importer only extended.
#  2. <kind> identical at every sampled call in every scenario where it runs ($XC/scenarios-all.txt).
#  3. the full comparison of the scenarios where <kind> failed shows no other kind newly differing (against the base's
#     full runs, measured only for the scenarios where the work list suggests a regression).
#  4. crates/s5clean unit tests and the building-tick capture stay green.
# Prints counts and VERDICT PASS|FAIL only.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/lib.sh"
KIND=$1; BASE=$(cat "$XC/base.sha"); cd "$S5_REPO"
fail(){ echo "$*"; echo "VERDICT FAIL"; exit 1; }
BIND=$(xc_binding "$KIND")
bad=$(xc_changed | grep -vE "^(crates/s5clean/|docs/${BIND:+|$BIND\$})" | head -3 | tr '\n' ' ')
[ -n "$bad" ] && fail "scope: changes outside crates/s5clean, docs${BIND:+ and $BIND}: $bad (revert them)"
if [ -n "$BIND" ] && [ -f "$BIND" ]; then
  # the binding translates the native call (arguments, ids) and calls crates/s5clean; the logic lives there
  code=$(grep -vE '^\s*(//|$)' "$BIND")
  [ "$(wc -l <<<"$code")" -gt 60 ] && fail "scope: the binding $BIND has $(wc -l <<<"$code") code lines (at most 60: move the logic into crates/s5clean)"
  grep -qE '\bunsafe\b|\.w(8|16|32)\(|static mut' <<<"$code" && fail "scope: the binding $BIND writes native memory or uses unsafe/static mut"
  grep -qE 'world\.|World::|s5clean::' <<<"$code" || fail "scope: the binding $BIND does not call crates/s5clean"
  # the port runs on the clean world: a closure that ignores it, or hands native memory to crates/s5clean, ports the
  # native data structure (a std::map walk, a vector size) instead of the game behaviour
  grep -qE '(Call::(world|returning)\([^|]*|Box::new\()(move\s*)?\|_\w*\|' <<<"$code" \
    && fail "scope: the port in $BIND ignores the world (|_world|): compute the result from the clean World, not from native memory read at the entry"
  grep -qE '\|\s*\w+\s*\|\s*entry\.(r(8|16|32)|mach)' <<<"$code" \
    && fail "scope: $BIND passes a native memory reader to crates/s5clean: the clean code works on World, never on native addresses"
fi
grep -qE '^\+.*(impl|dyn) Fn(Mut)?\(u32\) -> u32' <<<"$(git diff "$BASE" -- crates/s5clean)" \
  && fail "scope: crates/s5clean takes a native memory reader (Fn(u32) -> u32): the clean code works on World, never on native addresses"
git add -A -- $(xc_paths "$KIND")
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
t0=$(date +%s)
xc_build || fail "build failed"
t1=$(date +%s)
rm -rf "$XC/new"
# full runs of the failing scenarios (they hold the kind too) beside kind-only runs of the other scenarios
FULL=$(cat "$XC/scenarios-failing.txt")
xc_scenarios | awk -v s=" $FULL " 'index(s, " "$1" ")' | xc_runs "$XC/new/full" &
xc_scenarios | awk -v s=" $(cat "$XC/scenarios-all.txt") " -v f=" $FULL " 'index(s, " "$1" ") && !index(f, " "$1" ")' \
  | xc_runs "$XC/new/only" "$KIND"
wait
t2=$(date +%s)
# the base's full runs only where another kind seems to break against the work list (base build kept by worker.sh)
SUS=$(python3 "$D/xc.py" suspects "$XC/worklist.json" "$XC/new/full" "$KIND")
if [ -n "$SUS" ]; then
  echo "regression check: base measured in $(wc -w <<<"$SUS") scenario(s)"
  xc_scenarios | awk -v s=" $SUS " 'index(s, " "$1" ")' | XC_BINS=$XC/base-bin/bins.txt xc_runs "$XC/base/full"
fi
t3=$(date +%s)
python3 "$D/xc.py" check "$XC/base" "$XC/new" "$KIND" "$XC/worklist.json" $FULL > "$XC/verdict.txt"
grep -v '^VERDICT' "$XC/verdict.txt"
grep -q 'VERDICT PASS' "$XC/verdict.txt" || fail "comparison failed"
timeout 2400 cargo test --release -q -j "${CARGO_BUILD_JOBS:-3}" --manifest-path crates/s5clean/Cargo.toml > "$XC/unit.log" 2>&1 \
  || fail "s5clean tests failed: $(grep -E '^test .* FAILED|^error' "$XC/unit.log" | head -3 | tr '\n' ' ')"
( S5_CAPTURE_TURNS=10 timeout 1800 "$(xc_bin capture-entity-tick)" --test-threads=1 ) > "$XC/tick.log" 2>&1 \
  || fail "building tick capture failed: $(grep -m1 -E 'building tick' "$XC/tick.log")"
echo "s5clean tests ok, building tick ok"
echo "timing: build $((t1 - t0))s runs $((t2 - t1))s base $((t3 - t2))s tests $(( $(date +%s) - t3 ))s"
echo "VERDICT PASS"
