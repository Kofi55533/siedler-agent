#!/usr/bin/env bash
# coverage_keys.sh <siedler-sim-rs checkout>  -> "<fixture> <key>" per fixture (S5_GATE_FIXTURES incl. bc-full).
# The reference trace of a fixture depends only on recomp/ (s5recomp source tree), the game data / fixtures /
# images / generated lift (all from the siedler-data data-v2 release assets) and the sampling; NOT on s5clean.
# Key = sha256(fixture, recomp tree sha, data-v2 asset names+sizes+timestamps, tracer script hashes, STEP, CAP).
# Needs GH_TOKEN; no data download (so a cache-hit run stays cheap).
set -euo pipefail
SIM=$1; D=$(cd "$(dirname "$0")" && pwd); source "$D/s5-env.sh"
RT=$(git -C "$SIM" rev-parse HEAD:recomp)
DS=$(gh release view data-v2 -R Kofi55533/siedler-data --json assets -q '.assets[]|select(.name|test("tzst"))|"\(.name) \(.size) \(.updatedAt)"' | sort)
TS=$(cat "$D/coverage_trace.sh" "$D/coverage_cache.py" | sha256sum | cut -c1-16)
for f in $S5_GATE_FIXTURES; do
  k=$(printf '%s\n' "fixture=$f" "recomp=$RT" "$DS" "tools=$TS" "step=${COVER_STEP:-25}" "cap=${COVER_MAX_TURNS:-1000000}" | sha256sum | cut -c1-16)
  echo "$f $k"
done
