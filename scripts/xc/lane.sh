#!/usr/bin/env bash
# lane.sh <lane> [<first kind>]: one worker loop of a fix job (xc-worker.yml, started by lanes.sh). It works its first
# kind (or claims one), then claims the next kind of a fresh work list while the job's 330 min leave room for one
# (median 24 min, the budget of worker.sh at most 170 min, plus its verdict and merge), so a lane never idles until the
# watchdog's next wave. The kind a lane works stands in $RUNNER_TEMP/kind-<lane> (the job's "Release the claim" step).
#   needs: the job's setup (s5-setup.sh, worklist, decompilation, opencode), XC_T0, L, XC_DATA, XC_CLAIM_TOKEN.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd)
LANE=$1; kind=${2:-}; n=0; infra=0
KF=$RUNNER_TEMP/kind-$LANE
say(){ echo "lane $LANE: $*" | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"; }
left(){ echo $(( 330 - ($(date +%s) - XC_T0) / 60 )); }
while :; do
  if [ -z "$kind" ]; then
    [ "$(left)" -ge 90 ] || break
    # a waiting xc-try trial of the main account gets this slot before the next fleet kind
    if [ "${GITHUB_REPOSITORY:-}" = Kofi55533/siedler-agent ] && python3 "$D/xc.py" trial-waiting; then
      say "a trial waits for a slot: this lane ends"; break
    fi
    gh release download xc-worklist -R "$XC_DATA" -D /opt/s5/xc -p worklist.json --clobber || break
    # L + 1: this job's own slot counts too (its lanes share one claim token), and this lane's last claim is already
    # released (a wave started in that gap must not end the loop; the pool is then one over for this kind, and the
    # watchdog waits till it is not)
    kind=$(python3 "$D/xc.py" pick /opt/s5/xc/worklist.json 1 --claim --limit=$((L + 1)) | jq -r '.[0] // empty') || kind=""
    [ -n "$kind" ] || break
    say "next kind ($((n + 1)) in this lane): $kind"
  fi
  n=$((n + 1)); echo "$kind" > "$KF"
  l=$(( $(left) - 25 ))   # minutes this kind may use (verdict + merge margin)
  # 0 merged or nothing to do, 1 attempt recorded (not a job failure), 2 infrastructure (kind handed back)
  rc=0; XC_BUDGET_MIN=$(( l < 170 ? l : 170 )) "$D/worker.sh" "$kind" || rc=$?
  rm -f "$KF"; kind=""
  if [ $rc = 2 ]; then
    # a model API that answers nothing, or a broken setup: three in a row end the lane, a single one pauses it
    infra=$((infra + 1))
    [ $infra -ge 3 ] && { say "$infra infrastructure exits in a row: the lane ends"; exit 1; }
    sleep 300
  else
    infra=0
  fi
done
say "$n kinds"
