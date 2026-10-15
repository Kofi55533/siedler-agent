#!/usr/bin/env bash
# s5-live.sh <fixture> [adapter]  -> prints IDENTICAL/DIVERGENT summary; full output in $S5/live/<fixture>.log (never upload)
# fixtures: wintersturm economy wsecon build production combat bc-full
set -uo pipefail
source "$(dirname "$0")/s5-env.sh"
f=$1; a=${2:-$1}
case $f in
  bc-full) img=$S5_ARTIFACTS/bc/gs_h.img ;;
  build|production) img=$S5_V/images/wsecon/start.img ;;
  wsecon) img=$S5_V/images/wsecon-direct/start.img ;;
  *) img=$S5_V/images/$f/start.img ;;
esac
mkdir -p $S5/live; cd $S5_REPO/recomp
export S5_REMAP=$(python -c "import verify; print(verify.remap_for('$img', 'target/verify/live-$f'))")
export S5_CLEAN_MAP_DIR=$S5_V/maps/economy/maps/externalmap
./target/release/s5verify live ../data/scope/schema.json $S5_V/fixtures/$f/scenario.json "$img" "$a" > $S5/live/$f.log 2>&1
rc=$?
echo "live $f exit=$rc $(grep -m1 -oE '(IDENTICAL|DIVERGENT)[^:]*|loader unavailable' $S5/live/$f.log)"
exit $rc
