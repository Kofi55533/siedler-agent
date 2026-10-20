#!/usr/bin/env bash
# coverage_publish.sh <siedler-sim-rs checkout> [require-main 0|1]
# Downloads all per-fixture trace aggregates (trace-<fixture>--<key>.json.gz) from release coverage-cache,
# rebuilds COVERAGE.md with the current crates/s5clean/src cited check, and publishes COVERAGE.md + coverage-meta.txt.
# require-main=1: do not publish if a non-bc-full fixture has no aggregate at all (first computation pending).
set -euo pipefail
SIM=$(cd "$1" && pwd); REQ=${2:-0}; D=$(cd "$(dirname "$0")" && pwd); R=Kofi55533/siedler-agent
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
t0=$(date +%s)
"$D/coverage_keys.sh" "$SIM" > $W/keys
gh release download coverage-cache -R $R -D $W/in -p 'trace-*' >/dev/null 2>&1 || mkdir -p $W/in
missing=""; while read -r f k; do ls $W/in/trace-$f--*.json.gz >/dev/null 2>&1 || missing="$missing $f"; done < $W/keys
mm=$(printf '%s\n' $missing | grep -vx bc-full | tr '\n' ' ' || true)
if [ "$REQ" = 1 ] && [ -n "${mm// /}" ]; then echo "coverage_publish: no aggregate yet for:$missing -> not publishing"; exit 0; fi
python3 "$D/coverage_cache.py" aggregate --sim "$SIM" --indir $W/in --out $W/COVERAGE.md --want $(cut -d' ' -f2 $W/keys) > $W/agg.txt
cat $W/agg.txt
[ -s $W/COVERAGE.md ] || { echo "coverage_publish: empty COVERAGE.md"; exit 1; }
# full uncited list (every executed, uncited logic function, not just the top 50) for the planner's port fill source
python3 "$D/coverage_cache.py" aggregate --sim "$SIM" --indir $W/in --out $W/COVERAGE-full.md --top 5000 --want $(cut -d' ' -f2 $W/keys) >/dev/null || rm -f $W/COVERAGE-full.md
{ echo "clean_integration_sha=$(git -C "$SIM" rev-parse HEAD)"
  echo "generated_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  grep -E '^(fixtures|stale)=' $W/agg.txt
  echo "missing=${missing# }"
  echo "workflow_run=${GITHUB_SERVER_URL:-}/$R/actions/runs/${GITHUB_RUN_ID:-}"
  echo "note=per-fixture reference traces cached by key (recomp tree + data-v2 + sampling); cited check recomputed each run"; } > $W/coverage-meta.txt
if ! gh release view coverage-cache -R $R >/dev/null 2>&1; then
  gh release create coverage-cache -R $R --target main --prerelease -t coverage-cache \
    -n "Hourly coverage map of siedler-sim-rs clean/integration (coverage.yml). Assets are overwritten each run." >/dev/null
fi
gh release upload coverage-cache -R $R --clobber $W/COVERAGE.md $W/coverage-meta.txt $( [ -s $W/COVERAGE-full.md ] && echo $W/COVERAGE-full.md )
echo "coverage_publish: published COVERAGE.md $(wc -c < $W/COVERAGE.md) bytes, missing=[${missing# }] secs=$(( $(date +%s)-t0 ))"
