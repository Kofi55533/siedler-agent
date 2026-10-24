# sourced by the xc-* workflows: per-call differential (crates/s5xcheck capture tests) on the runner layout of s5-setup.sh.
# The comparison itself is the oracle: the original's real calls in recorded scenarios against the clean port on the
# same imported state. Nothing here prints game state; logs stay under $S5/xc (private, never uploaded).
source "$(dirname "${BASH_SOURCE[0]}")/../s5-env.sh"
[ -n "${XC_LOCAL:-}" ] && source "$XC_LOCAL"   # dry run off the runner layout: a file overriding the paths above
XC=$S5/xc; mkdir -p "$XC"
XC_JOBS=${XC_JOBS:-4}                      # parallel capture processes (each ~0.7 GB RSS, one core; runner: 4 cores)
# compared turns per scenario: 1200 instead of 120 makes 16-24 % more class-A functions run (2026-10-24 probe, per
# quarter of the scenarios: 1949-2069 at 120 turns, 2399-2475 at 1200, 2402-2573 at 3000) for ~20 % more run time (the
# load of the image and the inventory dominate); 3000 turns added little more
XC_TURNS=${XC_TURNS:-1200}
# a point that gives no result in XC_TURNS (the 2026-10-24 census at 1200 turns: 4 images stop on a panic later in the
# play, 1 runs past XC_TIMEOUT; all 5 compared at 120) is run again with this many turns instead of being lost
XC_TURNS_SHORT=${XC_TURNS_SHORT:-120}
XC_MAX=${XC_MAX:-4} XC_STRIDE=${XC_STRIDE:-5}
# limit per capture run (one point of one scenario): a full 120-turn ExecuteTask run took under 3 min (2026-10-23
# census: whole shards in 85-172 s), 1200 turns ~20 % more, and the ticks run plays the scenario once per class, so a
# run still going after 20 min hangs; the old 40 min limit held the whole census on one hanging scenario
XC_TIMEOUT=${XC_TIMEOUT:-1200}

# images the census leaves out: no running game to compare (new-fixtures index.json not_produced/rejected_captures,
# 2026-10-23 census: no capture result). Menu only, the map never started (turn 0): camp-bigcity, camp-extra1,
# map-gipfelsturm, map-sandbox, sp-spreadland; map-calabria never reached its capture turn. The oracle stops on
# untranslated code: camp-thalgrund, ai-citygates, ai-builds-citygates. Native load incomplete (hangs, or no call
# at all): saveload-stable, saveload-recapture.
# A recaptured image gets a new name and is measured again.
XC_DEAD=${XC_DEAD:-"camp-bigcity camp-extra1 map-gipfelsturm map-sandbox sp-spreadland map-calabria camp-thalgrund ai-citygates ai-builds-citygates saveload-stable saveload-recapture"}
# images whose play does not reach XC_TURNS within XC_TIMEOUT: they play XC_TURNS_SHORT from the start instead of
# losing XC_TIMEOUT before the fallback (2026-10-25 time probe: ai-builds-triggered runs past 1200 s at 1200 turns
# even without any capture, its census entry was measured at 120)
XC_LONG=${XC_LONG-"ai-builds-triggered"}

# scenarios: "name image skip turns" lines. bc replays its recorded commands (commands.bin beside the image); every
# other image runs autonomously (AI players, armies, campaign scripts), which is still the original behaviour, plus
# the player commands of the generated fixtures on that image (xc_cmds), so the command handlers run too.
xc_scenarios(){
  echo "bc $S5_ARTIFACTS/bc/gs_h.img 100 $((XC_TURNS + 180))"
  for d in "$S5_V"/images/*/ "$S5_NF"/images/*/; do
    n=$(basename "$d"); [[ " $XC_DEAD " == *" $n "* ]] && continue
    [[ " $XC_LONG " == *" $n "* ]] && t=$((XC_TURNS < XC_TURNS_SHORT ? XC_TURNS : XC_TURNS_SHORT)) || t=$XC_TURNS
    [ -f "$d/start.img" ] && echo "$n $d/start.img 0 $t"
  done | sort -u -k1,1
}

# xc_cmds: per scenario image, the commands of every generated fixture of the $S5_REPO checkout that starts on it
# (fixtures-gen/*/scenario.json "image" + commands.jsonl, absolute game turns) merged into $XC/cmds/<name>.bin, which
# xc_run replays (S5_XCHECK_COMMANDS). Commands after the compared turns are not reached. Rebuilt once per checkout.
# combat plays without: its commands leave a state the reference importer cannot read at 8107 sampled calls (probe
# 2026-10-25, against 0 without them), so those calls would go uncompared and the play takes twice as long
XC_CMDS_SKIP=${XC_CMDS_SKIP-combat}
xc_cmds(){
  local stamp; stamp=$(git -C "$S5_REPO" rev-parse HEAD 2>/dev/null)
  stamp="$stamp $XC_CMDS_SKIP"
  [ "$(cat "$XC/cmds/stamp" 2>/dev/null)" = "$stamp" ] && return 0
  rm -rf "$XC/cmds"; mkdir -p "$XC/cmds"
  xc_scenarios | python3 -c 'import json, os, sys
from pathlib import Path
repo, s5 = sys.argv[1], sys.argv[2]
# the fixtures name their image by its runner path (/opt/s5/data/...); the scenarios by $S5/data/...
key = lambda p: os.path.normpath(p.split("/data/", 1)[1]) if "/data/" in p else os.path.normpath(p)
by_image, skip = {}, set(sys.argv[3].split())
for s in sorted(Path(repo, "fixtures-gen").glob("*/scenario.json")):
    c = s.parent / json.loads(s.read_text()).get("commands", "commands.jsonl")
    if c.is_file() and c.stat().st_size:
        by_image.setdefault(key(json.loads(s.read_text())["image"]), []).append(str(c))
for line in sys.stdin:
    name, img = line.split()[:2]
    if name != "bc" and name not in skip and key(img) in by_image:
        print(name, *by_image[key(img)])' "$S5_REPO" "$S5" "$XC_CMDS_SKIP" \
  | while read -r name streams; do
      echo "$name: $(python3 "$(dirname "${BASH_SOURCE[0]}")/cmdbin.py" "$S5_REPO/oracle" "$XC/cmds/$name.bin" $streams)"
    done > "$XC/cmds/summary.txt"
  echo "$stamp" > "$XC/cmds/stamp"
}

# the census inventory of capture-functions: every class-A (simulation) function of scripts/scope_classes.json, one hex
# address per line; each one the scenario calls becomes a kind FN_<address> (counted, compared once it has a binding)
xc_inventory(){
  python3 -c 'import json,sys;print("\n".join(json.load(open(sys.argv[1]))["addresses"]["A"]))' \
    "$(dirname "${BASH_SOURCE[0]}")/../scope_classes.json" > "$XC/inventory.txt"
}

# build the capture tests of the current $S5_REPO checkout (shares recomp/target with the build cache)
xc_build(){
  xc_inventory || return 1
  xc_cmds
  ( cd "$S5_REPO" || exit 1
    [ -f crates/s5xcheck/Cargo.lock ] || cp recomp/Cargo.lock crates/s5xcheck/Cargo.lock
    CARGO_TARGET_DIR=${XC_TARGET:-$S5_REPO/recomp/target} cargo test --release --no-run -j "${CARGO_BUILD_JOBS:-3}" \
      --manifest-path crates/s5xcheck/Cargo.toml --test capture-execute-task --test capture-entity-tick \
      $([ -f crates/s5xcheck/tests/capture-ticks.rs ] && echo --test capture-ticks) \
      $([ -f crates/s5xcheck/tests/capture-functions.rs ] && echo --test capture-functions) \
      --message-format=json 2> "$XC/build.log" \
    | python3 -c 'import sys,json
for l in sys.stdin:
    d=json.loads(l)
    if d.get("reason")=="compiler-artifact" and d.get("executable"): print(d["target"]["name"], d["executable"])' > "$XC/bins.txt"
    rc=${PIPESTATUS[0]}; rm -f crates/s5xcheck/Cargo.lock; exit $rc ) || { echo "xc build failed ($(grep -c '^error' "$XC/build.log") errors)"; return 1; }
}
xc_bin(){ awk -v n="$1" '$1==n{print $2}' "${XC_BINS:-$XC/bins.txt}"; }
# xc_keep_bins <dir>: copies of the capture test binaries just built, so runs of this build stay possible after the
# checkout changed and was rebuilt (XC_BINS=<dir>/bins.txt selects them)
xc_keep_bins(){
  rm -rf "$1"; mkdir -p "$1"
  while read -r name exe; do cp "$exe" "$1/$name" && echo "$name $1/$name"; done < "$XC/bins.txt" > "$1/bins.txt"
}

# capture points: the test binaries of the census and the kinds each one measures. capture-ticks groups the entity
# ticks (building, settler, animal) by type as TICK_<CLASS>_<type>; capture-functions has one kind FN_<address> per
# native function (bound in crates/s5xcheck/src/functions, or only counted from the inventory); every other kind is an
# ExecuteTask task kind.
xc_points(){
  echo capture-execute-task
  [ -n "$(xc_bin capture-ticks)" ] && echo capture-ticks
  [ -n "$(xc_bin capture-functions)" ] && echo capture-functions
}
xc_point(){ case $1 in TICK_*) echo capture-ticks ;; FN_*) echo capture-functions ;; *) echo capture-execute-task ;; esac; }

# xc_run <scenario line fields: name image skip turns> <out.json> [only-kinds]: the capture runs of one scenario (every
# point, or only the points of the given kinds), merged into one document (the groups of the points are disjoint)
xc_run(){
  local name=$1 img=$2 skip=$3 turns=$4 out=$5 only=${6:-} tag=$name${6:+-only} bin rc=0 r t0=$SECONDS
  rm -f "$out" "$out".*.part
  local bins; if [ -n "$only" ]; then bins=$(tr ',' '\n' <<<"$only" | while read -r k; do xc_point "$k"; done | sort -u)
  else bins=$(xc_points); fi
  : > "$XC/run-$tag.log"
  local short=$((turns - XC_TURNS + XC_TURNS_SHORT)) first=$turns t
  [ "$short" -gt 0 ] || short=$turns  # a scenario already playing short (XC_LONG) has no shorter retry
  for bin in $bins; do
    for t in "$first" "$short"; do
      ( cd "$S5_REPO" && export S5_XCHECK_IMAGE=$img S5_CAPTURE_SKIP=$skip S5_CAPTURE_TURNS=$t S5_CAPTURE_MAX=$XC_MAX \
          S5_CAPTURE_STRIDE=$XC_STRIDE S5_CAPTURE_JSON=$out.$bin.part && { [ -z "$only" ] || export S5_CAPTURE_ONLY=$only; } &&
        { [ -z "${XC_NO_CMDS:-}" ] && [ -s "$XC/cmds/$name.bin" ] && export S5_XCHECK_COMMANDS=$XC/cmds/$name.bin || :; } &&
        { [ "$bin" != capture-functions ] || export S5_CAPTURE_FUNCTIONS=$XC/inventory.txt; } &&
        timeout -k 30 "$XC_TIMEOUT" "$(xc_bin "$bin")" --nocapture --test-threads=1 ) \
        < /dev/null >> "$XC/run-$tag.log" 2>&1
      r=$?
      # a failing comparison exits 101 with its document written; without one the point is tried once more, shorter
      [ -s "$out.$bin.part" ] && break
      [ "$t" = "$short" ] || [ "$short" -ge "$turns" ] && { rc=$r; break; }
      echo "no result in $t turns (exit $r): again with $short" >> "$XC/run-$tag.log"
      # a timeout is the play's length, not the point's: the scenario's further points start short (the 2026-10-24
      # census lost 3 x XC_TIMEOUT of one shard's 60 min on one image)
      [ "$r" != 124 ] || first=$short
    done
  done
  echo "$rc" > "$XC/rc-$tag"
  local cmds=; [ -n "${XC_NO_CMDS:-}" ] || cmds=$XC/cmds/$name.bin
  python3 - "$out" "$cmds" $(for bin in $bins; do echo "$out.$bin.part"; done) <<'M'
import json, struct, sys
from pathlib import Path
cmds = Path(sys.argv[2]) if sys.argv[2] else None
docs = [json.loads(Path(p).read_text()) for p in sys.argv[3:] if Path(p).is_file() and Path(p).stat().st_size]
if docs:
    out = {"function": ",".join(d["function"] for d in docs), "turns": docs[0]["turns"],
           "entries": sum(d["entries"] for d in docs), "import_errors": [e for d in docs for e in d["import_errors"]],
           "groups": {k: v for d in docs for k, v in d["groups"].items()},
           # a point without its document (crash, timeout) is named, so a missing kind is not read as fixed
           "missing_points": [Path(p).suffixes[-2][1:] for p in sys.argv[3:] if not (Path(p).is_file() and Path(p).stat().st_size)],
           # the player commands the play ran with (xc_cmds)
           "commands": struct.unpack_from("<I", cmds.read_bytes(), 8)[0] if cmds and cmds.is_file() and cmds.stat().st_size >= 12 else 0}
    Path(sys.argv[1]).write_text(json.dumps(out))
M
  rm -f "$out".*.part
  echo $((SECONDS - t0)) > "$XC/secs-$tag"   # wall seconds of this scenario's runs (verify.sh sums them up)
  [ -s "$out" ]
}

# xc_runs <dir> [only-kinds] < scenario lines: run them $XC_JOBS at a time, <dir>/<name>.json each
xc_runs(){
  local dir=$1 only=${2:-}; mkdir -p "$dir"
  while read -r name img skip turns; do
    [ -f "$img" ] || continue
    xc_run "$name" "$img" "$skip" "$turns" "$dir/$name.json" "$only" &
    # a free slot starts the next run at once (runs differ a lot in length: one point vs all points)
    while [ "$(jobs -rp | wc -l)" -ge "$XC_JOBS" ]; do wait -n; done
  done; wait
}

# xc_binding <kind>: the binding file a function kind FN_<address> may add or change (crates/s5xcheck/src/functions)
xc_binding(){ case $1 in FN_*) local a=${1#FN_}; a=${a,,}; echo "crates/s5xcheck/src/functions/fn_${a#"${a%%[!0]*}"}.rs" ;; esac; }
# xc_paths <kind>: the pathspecs of a worker's change: the clean crate, the docs, and for a function kind the bindings
# directory (verify.sh allows only the kind's own binding in it; a pathspec naming a file not yet written would fail)
xc_paths(){ echo crates/s5clean docs; [ -n "$(xc_binding "$1")" ] && echo crates/s5xcheck/src/functions; }

# paths the worker changed in $S5_REPO relative to HEAD's tree and the index (setup leftovers excluded)
xc_changed(){
  git -C "$S5_REPO" status --porcelain --untracked-files=all -- . ':!recomp/gen' ':!recomp/gen.closure.pkl' \
    ':!crates/s5xcheck/Cargo.lock' ':!target' ':!recomp/target' | cut -c4- | sed 's/.* -> //'
}
