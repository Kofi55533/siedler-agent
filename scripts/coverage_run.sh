#!/usr/bin/env bash
# coverage_run.sh "<fixtures>" <per-fixture timeout>  (in the siedler-sim-rs checkout, s5-env sourced, s5recomp built)
# Traces every listed fixture whose current key has no asset yet (parallel, xargs -P nproc), uploads
# trace-<fixture>--<key>.json.gz to release coverage-cache and deletes that fixture's older-key assets.
set -uo pipefail
FIX=$1; TO=$2; D=$(cd "$(dirname "$0")" && pwd); R=Kofi55533/siedler-agent; SIM=$(pwd)
P=${RUNNER_TEMP:-/tmp}/covrun; rm -rf $P; mkdir -p $P/out
"$D/coverage_keys.sh" "$SIM" > $P/keys || exit 1
gh release view coverage-cache -R $R --json assets -q '.assets[].name' > $P/assets 2>/dev/null || : > $P/assets
todo=""
for f in $FIX; do
  k=$(awk -v f=$f '$1==f{print $2}' $P/keys)
  grep -qx "trace-$f--$k.json.gz" $P/assets && { echo "cache hit: $f ($k)"; continue; }
  todo="$todo $f:$k"
done
[ -n "$todo" ] || { echo "coverage_run: nothing to trace"; exit 0; }
echo "coverage_run: tracing$todo"
t0=$(date +%s)
export P TO D
printf '%s\n' $todo | xargs -P "$(nproc)" -I{} bash -c 'f=${1%%:*}; k=${1##*:}; mkdir -p $P/tmp-$f
  TMPDIR=$P/tmp-$f timeout $TO "$D/coverage_trace.sh" $f $P/out/trace-$f--$k.json.gz $k > $P/$f.log 2>&1 || touch $P/FAILED-$f
  grep -hE "^coverage_trace: " $P/$f.log' _ {}
echo "coverage_run: trace wall secs=$(( $(date +%s)-t0 ))"
rc=0
for x in $todo; do
  f=${x%%:*}; k=${x##*:}; a=$P/out/trace-$f--$k.json.gz
  if [ -e $P/FAILED-$f ] || [ ! -s $a ]; then echo "FAILED $f: $(grep -vE '^coverage_trace: ' $P/$f.log | tail -n 3 | cut -c1-200 | tr '\n' ' ')"; rc=1; continue; fi
  gh release upload coverage-cache -R $R --clobber $a >/dev/null || { echo "upload $f failed"; rc=1; continue; }
  for old in $(grep -E "^trace-$f--[0-9a-f]+\.json\.gz$" $P/assets | grep -vx "trace-$f--$k.json.gz"); do
    gh release delete-asset coverage-cache "$old" -R $R -y >/dev/null 2>&1 || true
  done
  echo "uploaded trace-$f--$k.json.gz $(wc -c < $a) bytes"
done
exit $rc
