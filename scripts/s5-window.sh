#!/usr/bin/env bash
# s5-window.sh <fixture>:<divergent turn>[:<lead>[:<tail|end>]] ...
# Records a reference window around a divergence so the clean engine can be replayed against it WITHOUT the reference
# engine (siedler-sim-rs: `cargo run --release --example window -- <dir>` in crates/s5clean; seconds per round instead of
# a full live run). Per token: s5verify window steps the original to T = divergent turn - lead (the scenario start when
# the divergence is within 1000 turns of it, else lead 60), writes its memory there as start.img and the reference
# snapshot of every turn up to divergent turn + tail (default 60). Uploads <fixture>-win.tzst (dir <fixture>-win/ with
# start.img, scenario.json, commands.jsonl, start.jsonl, ref.jsonl.gz) to the PRIVATE release Kofi55533/siedler-data
# tag "window". S5_WINDOW_REF: siedler-sim-rs ref to build (default clean/integration). Prints only names and numbers.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/s5-env.sh"
REF=${S5_WINDOW_REF:-clean/integration}; DR=Kofi55533/siedler-data; rel=window
mkdir -p /tmp/win/up
cd "$S5_REPO" && git fetch -q origin "+refs/heads/$REF:refs/remotes/origin/$REF" && git checkout -q --detach "origin/$REF" || { echo "checkout failed"; exit 1; }
if ! "$D/s5-build.sh" >/dev/null; then echo "build failed"; grep -E -A12 "^error" $S5/build.log | head -n 150 > /tmp/win/up/window-build.log; fi
for tok in "$@"; do
  [ -x "$S5_REPO/recomp/target/release/s5verify" ] || break
  IFS=: read -r f div lead tail <<<"$tok"; tail=${tail:-60}
  [[ "$f" =~ ^[a-z0-9][a-z0-9-]*$ && "$div" =~ ^[0-9]+$ && "$tail" =~ ^([0-9]+|end)$ ]] || { echo "bad token"; continue; }
  "$D/s5-fixture-install.sh" "$f" >/dev/null 2>&1 || true
  sc=$S5_V/fixtures/$f/scenario.json
  case $f in
    bc-full) img=$S5_ARTIFACTS/bc/gs_h.img ;;
    build|production) img=$S5_V/images/wsecon/start.img ;;
    wsecon) img=$S5_V/images/wsecon-direct/start.img ;;
    wintersturm|economy|combat) img=$S5_V/images/$f/start.img ;;
    *) sc=$S5_NF/fixtures/$f/scenario.json
       img=$(python -c "import json;print(json.load(open('$sc'))['image'])" 2>/dev/null) ;;
  esac
  [ -f "$sc" ] && [ -f "$img" ] || { echo "$f: FAILED not installed"; continue; }
  start=$(python -c "import json;print(json.load(open('$sc'))['start_turn'])")
  if [ -z "${lead:-}" ]; then if [ $(( div - start )) -le 1000 ]; then lead=$(( div - start )); else lead=60; fi; fi
  T=$(( div - lead )); [ $T -lt $start ] && T=$start
  # tail "end": record to the scenario end; if the original does not get that far, fall back to tail 60.
  if [ "$tail" = end ]; then turns=$(python -c "import json;print(json.load(open('$sc'))['turns'])"); n=$(( start + turns - T )); fb=$(( div - T + 60 )); else n=$(( div - T + tail )); fb=; fi
  out=/tmp/win/$f-win; rm -rf "$out"; t0=$(date +%s)
  cd "$S5_REPO/recomp"
  export S5_REMAP=$(python -c "import verify; print(verify.remap_for('$img', 'target/verify/window-$f'))")
  timeout "${S5_LIVE_TIMEOUT:-7000}" ./target/release/s5verify window ../data/scope/schema.json "$sc" "$img" "$T" "$n" "$out" > "/tmp/win/$f.log" 2>&1; rc=$?
  if { [ $rc != 0 ] || [ ! -s "$out/ref.jsonl.gz" ]; } && [ -n "$fb" ] && [ "$fb" -lt "$n" ]; then
    echo "$f: full length failed rc=$rc, falling back to $fb turns"; n=$fb; rm -rf "$out"
    timeout "${S5_LIVE_TIMEOUT:-7000}" ./target/release/s5verify window ../data/scope/schema.json "$sc" "$img" "$T" "$n" "$out" > "/tmp/win/$f.log" 2>&1; rc=$?
  fi
  if [ $rc != 0 ] || [ ! -s "$out/ref.jsonl.gz" ]; then
    echo "$f: FAILED window rc=$rc"; tail -n 30 "/tmp/win/$f.log" > "/tmp/win/up/window-$f.log"; continue; fi
  rm -f "$out/start.img.state.json"
  tar -C /tmp/win -cf - "$f-win" | zstd -q -T0 -6 -o "/tmp/win/up/$f-win.tzst"
  echo "$f: window from turn $T, $n turns, size $(stat -c %s "/tmp/win/up/$f-win.tzst"), $(( $(date +%s)-t0 ))s"
  rm -rf "$out"
done
gh release view $rel -R $DR >/dev/null 2>&1 || gh release create $rel -R $DR --title $rel --notes "reference windows (private)" >/dev/null 2>&1 || true
for x in /tmp/win/up/*; do [ -e "$x" ] || continue
  ok=0; for i in 1 2 3; do gh release upload $rel -R $DR --clobber "$x" >/dev/null 2>&1 && { ok=1; break; }; sleep 10; done
  [ $ok = 1 ] || echo "upload failed: $(basename "$x" | sed 's/\..*//')"
done
echo "uploaded $(ls /tmp/win/up | wc -l) files to $rel"
