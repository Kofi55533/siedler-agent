#!/usr/bin/env bash
# Compare a reviewed Clean commit against the private BC observation.
set -euo pipefail

mode=$1
checkout=$(cd "$2" && pwd)
inputs=$(cd "$3" && pwd)
case "$mode" in
  quick|full) ;;
  *) echo "usage: s5-observation.sh {quick|full} SIM_CHECKOUT INPUT_DIR" >&2; exit 2 ;;
esac
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
log="$tmp/observe.log"
ref="$inputs/bc-full-reference.jsonl.gz"
archive="$inputs/bc-full-start.tar.zst"
expected="$inputs/bc-full-expected.txt"
for file in "$ref" "$archive" "$expected"; do
  [ -s "$file" ] || { echo "Missing private observation input: $(basename "$file")" >&2; exit 2; }
done
tar --zstd -xf "$archive" -C "$tmp" scenario.json start.img commands.jsonl
sha=$(git -C "$checkout" rev-parse HEAD)
echo "BC Full $mode observation at $sha"
cd "$checkout/crates/s5clean"
export CARGO_TARGET_DIR="$tmp/target"
cargo build --locked --release --example observe > "$tmp/build.log" 2>&1 || {
  echo "Clean observation build failed. Re-run in the private checkout for compiler diagnostics." >&2
  exit 2
}
args=("$tmp/scenario.json" "$tmp/start.img" "$ref")
if [ "$mode" = quick ]; then args+=(32); fi
rc=0
/usr/bin/time -f '%e' -o "$tmp/wall" "$CARGO_TARGET_DIR/release/examples/observe" "${args[@]}" > "$log" 2>&1 || rc=$?
if [[ $(cat "$tmp/wall") =~ ^[0-9]+\.[0-9]+$ ]]; then
  echo "Replay elapsed $(cat "$tmp/wall") s"
fi
if [ "$rc" -gt 1 ]; then
  echo "Observation error (exit $rc). Re-run in the private checkout for diagnostics." >&2
  exit 2
fi
if [ "$mode" = quick ]; then
  if [ "$rc" = 0 ] && grep -qE '^bc-full: IDENTICAL 32 ticks \(33 states, [0-9]+ shared seed transitions\)$' "$log"; then
    echo "Quick check passed (32 ticks)"
    exit 0
  fi
  sed -nE 's/^bc-full: DIVERGENT turn ([0-9]+): .*/First observed difference entering turn \1/p' "$log"
  echo "Quick check failed" >&2
  exit 1
fi
if [ "$rc" = 0 ] && grep -qE '^bc-full: IDENTICAL 23900 ticks \(23901 states, [0-9]+ shared seed transitions\)$' "$log"; then
  echo "Full check passed (23,900 ticks agree)"
  exit 0
fi
turn=$(sed -nE 's/^bc-full: DIVERGENT turn ([0-9]+): .*/\1/p' "$log")
if [ "$rc" != 1 ] || ! [[ $turn =~ ^[0-9]{1,5}$ ]]; then
  echo "Full check produced no recognized verdict" >&2
  exit 2
fi
if [ "$turn" -gt 6842 ]; then
  echo "First observed difference entering turn $turn (later than 6842)"
  exit 0
fi
if [ "$turn" = 6842 ] &&
  grep -Fxq -f "$expected" "$log"; then
  echo "Known BC Full observation at turn 6842 (hurt_guard)"
  exit 0
fi
echo "Unexpected BC Full observation at turn $turn" >&2
exit 1
