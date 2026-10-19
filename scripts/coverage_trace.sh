#!/usr/bin/env bash
# coverage_trace.sh <fixture> <out.json.gz> <key>
# Block-traces sampled turns of ONE fixture with the reference port (s5recomp) exactly like siedler-sim-rs
# tools/coverage_map.sh (same fixture->scenario/image mapping, every COVER_STEP-th turn plus the command turns),
# but streams each finished turn trace into a per-pc aggregate (scripts/coverage_cache.py extract) and deletes it,
# so long horizons (bc-full, full fixture lengths) need no trace disk. Run from the siedler-sim-rs checkout root
# with scripts/s5-env.sh sourced. Prints only fixture names, turn ranges and timings.
# env: COVER_STEP=25, COVER_MAX_TURNS=1000000 (horizon past the start; default = full fixture length)
set -euo pipefail
f=$1; OUTJ=$2; KEY=$3
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(pwd)
S5=${S5:-/opt/s5}; V=${S5_V:-$S5/data/fixtures/codex-verify-infra}; NF=${S5_NF:-$S5/data/newfix}; ART=${S5_ARTIFACTS:-$S5/data/art}
S5RECOMP=${S5RECOMP:-$ROOT/recomp/target/release/s5recomp}
STEP=${COVER_STEP:-25}; CAP=${COVER_MAX_TURNS:-1000000}
[ -x "$S5RECOMP" ] || { echo "s5recomp missing" >&2; exit 2; }
SCRATCH=$(mktemp -d "${TMPDIR:-/tmp}/covtrace-$f.XXXX"); trap 'rm -rf "$SCRATCH"' EXIT
base=${f%-mapstart}
sc=$V/fixtures/$base/scenario.json; scdir=$V/fixtures/$base
case $base in
  bc-full) img=$ART/bc/gs_h.img ;;
  build|production) img=$V/images/wsecon/start.img ;;
  wsecon) img=$V/images/wsecon-direct/start.img ;;
  wintersturm|economy|combat) img=$V/images/$base/start.img ;;
  *) sc=$NF/fixtures/$base/scenario.json; scdir=$NF/fixtures/$base
     img=$(python -c "import json;print(json.load(open('$sc'))['image'])") ;;
esac
[ -f "$sc" ] || { echo "coverage_trace: $f: no scenario" >&2; exit 3; }
read -r START TURNS < <(python - "$sc" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(d["start_turn"], d["turns"])
PY
)
END=$((START + TURNS)); CAPEND=$((START + CAP)); [ "$CAPEND" -lt "$END" ] && END=$CAPEND
CMDBIN=""
if [ -s "$scdir/commands.jsonl" ]; then
  python3 - "$scdir" "$SCRATCH/cmds.json" "$END" <<'PY'
import json, os, sys
d, out, end = sys.argv[1], sys.argv[2], int(sys.argv[3])
cmds = [json.loads(l) for l in open(os.path.join(d, "commands.jsonl"))]
json.dump({"kopf": {"gamestart_turn": 0},
           "befehle": [{"turn": c["turn"], "event_id": c["id"],
                        "felder": dict(c["fields"], ObjectClassName=c["class"])}
                       for c in cmds if c["turn"] <= end]}, open(out, "w"))
PY
  (cd "$ROOT/recomp" && python commands_bin.py "$SCRATCH/cmds.json" "$SCRATCH/cmds.bin" >/dev/null) && CMDBIN=$SCRATCH/cmds.bin
fi
TURNS_CSV=$(python3 - "$START" "$END" "$STEP" "$scdir" <<'PY'
import json, os, sys
start, end, step = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
ts = set(range(start, end + 1, step)); ts.add(start); ts.add(end)
p = os.path.join(sys.argv[4], "commands.jsonl")
if os.path.exists(p):
    for l in open(p):
        t = json.loads(l)["turn"]
        if start <= t <= end:
            ts.add(t)
print(",".join(str(t) for t in sorted(ts)))
PY
)
D=$SCRATCH/traces; mkdir -p "$D"
export S5_REMAP=$(cd "$ROOT/recomp" && python -c "import verify, sys; print(verify.remap_for(sys.argv[1], sys.argv[2]))" "$img" "$SCRATCH/remap")
echo "coverage_trace: $f turns $START–$END, $(echo "$TURNS_CSV" | tr ',' '\n' | wc -l) sampled turns"
t0=$(date +%s)
if [ -n "$CMDBIN" ]; then
  COMMANDS="$CMDBIN" TRACE="$D/{turn}.bin" TRACE_TURNS="$TURNS_CSV" "$S5RECOMP" "$img" $((END - START + 1)) >/dev/null 2>&1 &
else
  TRACE="$D/{turn}.bin" TRACE_TURNS="$TURNS_CSV" "$S5RECOMP" "$img" $((END - START + 1)) >/dev/null 2>&1 &
fi
PID=$!
python3 "$HERE/coverage_cache.py" extract --dir "$D" --pid $PID --fixture "$f" --key "$KEY" \
  --start $START --end $END --step $STEP --out "$OUTJ"
set +e; wait $PID; rc=$?; set -e
[ $rc = 0 ] || { echo "coverage_trace: $f s5recomp exit=$rc" >&2; rm -f "$OUTJ"; exit 1; }
echo "coverage_trace: $f traced in $(( $(date +%s)-t0 ))s"
