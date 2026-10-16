#!/usr/bin/env bash
# s5-live.sh <fixture> [adapter]
# fixtures: wintersturm economy (map recipe by default) | wsecon build production combat bc-full |
#   combat-waves combat-big combat-long ai-long late-ai late-economy camp-crawford map-winterisland (imported images) |
#   wintersturm-mapstart economy-mapstart (map recipe, explicit). adapter "image" forces image import.
# Output only a one-line verdict; full log in $S5/live/<f>.log (never paste it anywhere).
set -uo pipefail
source "$(dirname "$0")/s5-env.sh"
f=$1; base=${f%-mapstart}
case $f in wintersturm|economy|*-mapstart) d=$base ;; *) d=image ;; esac; a=${2:-$d}
sc=$S5_V/fixtures/$base/scenario.json
case $base in
  bc-full) img=$S5_ARTIFACTS/bc/gs_h.img ;;
  build|production) img=$S5_V/images/wsecon/start.img ;;
  wsecon) img=$S5_V/images/wsecon-direct/start.img ;;
  wintersturm|economy|combat) img=$S5_V/images/$base/start.img ;;
  *) sc=$S5_NF/fixtures/$base/scenario.json
     img=$(python -c "import json;print(json.load(open('$sc'))['image'])") ;;
esac
mkdir -p $S5/live; cd $S5_REPO/recomp
export S5_REMAP=$(python -c "import verify; print(verify.remap_for('$img', 'target/verify/live-$f'))")
[ "$a" = economy ] && export S5_CLEAN_MAP_DIR=$S5_V/maps/economy/maps/externalmap
timeout ${S5_LIVE_TIMEOUT:-1800} ./target/release/s5verify live ../data/scope/schema.json "$sc" "$img" "$a" > $S5/live/$f.log 2>&1
rc=$?
m=$(grep -m1 -oE "^[a-z-]+: (DIVERGENT turn [0-9]+|IDENTICAL [0-9]+ ticks)|loader unavailable" $S5/live/$f.log | sed 's/^[a-z-]*: //')
[ -z "$m" ] && m=$(grep -oE 'IDENTICAL[^:(]*' $S5/live/$f.log | tail -n1)
echo "live $f exit=$rc ${m}"
exit $rc
