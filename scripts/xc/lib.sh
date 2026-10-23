# sourced by the xc-* workflows: per-call differential (crates/s5xcheck capture tests) on the runner layout of s5-setup.sh.
# The comparison itself is the oracle: the original's real calls in recorded scenarios against the clean port on the
# same imported state. Nothing here prints game state; logs stay under $S5/xc (private, never uploaded).
source "$(dirname "${BASH_SOURCE[0]}")/../s5-env.sh"
[ -n "${XC_LOCAL:-}" ] && source "$XC_LOCAL"   # dry run off the runner layout: a file overriding the paths above
XC=$S5/xc; mkdir -p "$XC"
XC_JOBS=${XC_JOBS:-3}                      # parallel capture processes (each ~0.7 GB RSS, one core)
XC_TURNS=${XC_TURNS:-120}                  # compared turns per scenario
XC_MAX=${XC_MAX:-4} XC_STRIDE=${XC_STRIDE:-5}
# limit per capture run (one point of one scenario): a full 120-turn ExecuteTask run takes under 3 min (2026-10-23
# census: whole shards in 85-172 s) and the ticks run plays the scenario once per class, so a run still going after
# 15 min hangs; the old 40 min limit held the whole census on one hanging scenario
XC_TIMEOUT=${XC_TIMEOUT:-900}

# images the census leaves out: no running game to compare (new-fixtures index.json not_produced/rejected_captures,
# 2026-10-23 census: no capture result). Menu only, the map never started (turn 0): camp-bigcity, camp-extra1,
# map-gipfelsturm, map-sandbox, sp-spreadland; map-calabria never reached its capture turn. The oracle stops on
# untranslated code: camp-thalgrund, ai-citygates, ai-builds-citygates. Native load incomplete (hangs, or no call
# at all): saveload-stable, saveload-recapture.
# A recaptured image gets a new name and is measured again.
XC_DEAD=${XC_DEAD:-"camp-bigcity camp-extra1 map-gipfelsturm map-sandbox sp-spreadland map-calabria camp-thalgrund ai-citygates ai-builds-citygates saveload-stable saveload-recapture"}

# scenarios: "name image skip turns" lines. bc replays its recorded commands (commands.bin beside the image); every
# other image runs autonomously (AI players, armies, campaign scripts), which is still the original behaviour.
xc_scenarios(){
  echo "bc $S5_ARTIFACTS/bc/gs_h.img 100 $((XC_TURNS + 180))"
  for d in "$S5_V"/images/*/ "$S5_NF"/images/*/; do
    n=$(basename "$d"); [[ " $XC_DEAD " == *" $n "* ]] && continue
    [ -f "$d/start.img" ] && echo "$n $d/start.img 0 $XC_TURNS"
  done | sort -u -k1,1
}

# build the capture tests of the current $S5_REPO checkout (shares recomp/target with the build cache)
xc_build(){
  ( cd "$S5_REPO" || exit 1
    [ -f crates/s5xcheck/Cargo.lock ] || cp recomp/Cargo.lock crates/s5xcheck/Cargo.lock
    CARGO_TARGET_DIR=${XC_TARGET:-$S5_REPO/recomp/target} cargo test --release --no-run -j "${CARGO_BUILD_JOBS:-3}" \
      --manifest-path crates/s5xcheck/Cargo.toml --test capture-execute-task --test capture-entity-tick \
      $([ -f crates/s5xcheck/tests/capture-ticks.rs ] && echo --test capture-ticks) \
      --message-format=json 2> "$XC/build.log" \
    | python3 -c 'import sys,json
for l in sys.stdin:
    d=json.loads(l)
    if d.get("reason")=="compiler-artifact" and d.get("executable"): print(d["target"]["name"], d["executable"])' > "$XC/bins.txt"
    rc=${PIPESTATUS[0]}; rm -f crates/s5xcheck/Cargo.lock; exit $rc ) || { echo "xc build failed ($(grep -c '^error' "$XC/build.log") errors)"; return 1; }
}
xc_bin(){ awk -v n="$1" '$1==n{print $2}' "$XC/bins.txt"; }

# capture points: the test binaries of the census and the kinds each one measures. capture-ticks groups the entity
# ticks (building, settler, animal) by type as TICK_<CLASS>_<type>; every other kind is an ExecuteTask task kind.
xc_points(){ echo capture-execute-task; [ -n "$(xc_bin capture-ticks)" ] && echo capture-ticks; }
xc_point(){ case $1 in TICK_*) echo capture-ticks ;; *) echo capture-execute-task ;; esac; }

# xc_run <scenario line fields: name image skip turns> <out.json> [only-kinds]: the capture runs of one scenario (every
# point, or only the points of the given kinds), merged into one document (the groups of the points are disjoint)
xc_run(){
  local name=$1 img=$2 skip=$3 turns=$4 out=$5 only=${6:-} tag=$name${6:+-only} bin rc=0 r
  rm -f "$out" "$out".*.part
  local bins; if [ -n "$only" ]; then bins=$(tr ',' '\n' <<<"$only" | while read -r k; do xc_point "$k"; done | sort -u)
  else bins=$(xc_points); fi
  : > "$XC/run-$tag.log"
  for bin in $bins; do
    ( cd "$S5_REPO" && export S5_XCHECK_IMAGE=$img S5_CAPTURE_SKIP=$skip S5_CAPTURE_TURNS=$turns S5_CAPTURE_MAX=$XC_MAX \
        S5_CAPTURE_STRIDE=$XC_STRIDE S5_CAPTURE_JSON=$out.$bin.part && { [ -z "$only" ] || export S5_CAPTURE_ONLY=$only; } &&
      timeout -k 30 "$XC_TIMEOUT" "$(xc_bin "$bin")" --nocapture --test-threads=1 ) \
      < /dev/null >> "$XC/run-$tag.log" 2>&1
    r=$?; [ -s "$out.$bin.part" ] || rc=$r   # a failing comparison exits 101 with its document written
  done
  echo "$rc" > "$XC/rc-$tag"
  python3 - "$out" $(for bin in $bins; do echo "$out.$bin.part"; done) <<'M'
import json, sys
from pathlib import Path
docs = [json.loads(Path(p).read_text()) for p in sys.argv[2:] if Path(p).is_file() and Path(p).stat().st_size]
if docs:
    out = {"function": ",".join(d["function"] for d in docs), "turns": docs[0]["turns"],
           "entries": sum(d["entries"] for d in docs), "import_errors": [e for d in docs for e in d["import_errors"]],
           "groups": {k: v for d in docs for k, v in d["groups"].items()},
           # a point without its document (crash, timeout) is named, so a missing kind is not read as fixed
           "missing_points": [Path(p).suffixes[-2][1:] for p in sys.argv[2:] if not (Path(p).is_file() and Path(p).stat().st_size)]}
    Path(sys.argv[1]).write_text(json.dumps(out))
M
  rm -f "$out".*.part
  [ -s "$out" ]
}

# xc_runs <dir> [only-kinds] < scenario lines: run them $XC_JOBS at a time, <dir>/<name>.json each
xc_runs(){
  local dir=$1 only=${2:-} n=0; mkdir -p "$dir"
  while read -r name img skip turns; do
    [ -f "$img" ] || continue
    xc_run "$name" "$img" "$skip" "$turns" "$dir/$name.json" "$only" &
    n=$((n + 1)); [ $((n % XC_JOBS)) = 0 ] && wait
  done; wait
}

# paths the worker changed in $S5_REPO relative to HEAD's tree and the index (setup leftovers excluded)
xc_changed(){
  git -C "$S5_REPO" status --porcelain --untracked-files=all -- . ':!recomp/gen' ':!recomp/gen.closure.pkl' \
    ':!crates/s5xcheck/Cargo.lock' ':!target' ':!recomp/target' | cut -c4- | sed 's/.* -> //'
}
