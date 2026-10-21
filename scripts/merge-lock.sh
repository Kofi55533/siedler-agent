# merge-lock.sh: sourced by s5-gate.sh (needs gh, $BASE, $G, $GT0). Base lock for legacy merges (Dominic 2026-10-21):
# a legacy squash-merge into clean/integration never lands while a fleet-integrate-v2 run is queued or in progress, because
# that run measures the newest clean/integration and has to retest the whole batch when the base moves under it (~30 min).
# The lock only WAITS: no merge is skipped, no gate criterion changes. After a wait the gate's moved-base check re-measures
# the exact new merged tree as before; a gate that cannot wait inside its limit ends DEFERRED (exit 3, re-gated later).
# S5_INTEGRATE_WAIT_MIN (default 90) caps the wait, S5_INTEGRATE_POLL_S (default 60) is the poll interval (1 REST call each).
ML_REPO=${S5_INTEGRATE_REPO:-Kofi55533/siedler-agent}; ML_WF=${S5_INTEGRATE_WORKFLOW:-fleet-integrate.yml}
integrate_active(){ # 0 = a fleet-integrate-v2 run is queued/waiting/in progress, or the lookup failed (unknown = active); 1 = idle
  local n
  n=$(gh api "repos/$ML_REPO/actions/workflows/$ML_WF/runs?per_page=30" \
        -q '[.workflow_runs[]|select(.status!="completed")]|length' 2>/dev/null) || return 0
  [ "$n" != 0 ]; }
integrate_wait(){ # returns 0 once no run is active; 1 if still active after the wait cap or the gate budget (S5_GATE_BUDGET_MIN)
  local t0 said=0; t0=$(date +%s)
  while integrate_active; do
    [ $said = 1 ] || { echo "merge lock: fleet-integrate-v2 run active or unknown, merge into $BASE waits" >> "$G/report.txt"; said=1; }
    [ $(( $(date +%s) - t0 )) -ge $(( ${S5_INTEGRATE_WAIT_MIN:-90} * 60 )) ] && return 1
    [ "${S5_GATE_BUDGET_MIN:-0}" -gt 0 ] && [ $(( ( $(date +%s) - ${GT0:-$t0} ) / 60 + 5 )) -ge "$S5_GATE_BUDGET_MIN" ] && return 1
    sleep "${S5_INTEGRATE_POLL_S:-60}"
  done
  [ $said = 0 ] || echo "merge lock: no fleet-integrate-v2 run active after $(( ( $(date +%s) - t0 ) / 60 )) min wait" >> "$G/report.txt"; }
