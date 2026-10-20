#!/usr/bin/env bash
# Codex loop heartbeat publisher (box). Dominic 2026-10-20 11:14: a Codex LANE-CLAIM blocks a DeepSeek PR only while the
# Codex loop is really active. Every 60 s this derives the loop state from the processes and run_loop.status and publishes
# codex-heartbeat.json (owner, state, updated_utc, until) on release `heartbeat` of Kofi55533/siedler-agent: on every state
# change and every 5 min. s5-gate / regate / planner (scripts/lane-claims.py --heartbeat) treat Codex claims as stale unless
# state=active and the file is < 25 min old. On entering blocked/stopped it appends one board note on clean/integration
# (append-only, direct path) saying the Codex claims are stale until the loop runs again.
# States: active   = codex exec running, or the loop is between successful iterations (END exit=0 < 15 min ago)
#         blocked  = usage limit (status "usage limit: sleeping until", or the last log says "try again at ...")
#         waiting  = transient error / failure retry pause
#         stopped  = no run_loop.sh process, or LOOP EXIT / stopping
# Single instance (pidfile). Started by run_loop.sh; safe to start by hand: nohup bash codex-heartbeat.sh >/dev/null 2>&1 &
D=/workspace/siedler/codex-box
HB=/workspace/fix/siedler-agent/scripts/heartbeat.sh; [ -x $D/heartbeat.sh ] && HB=$D/heartbeat.sh
PIDF=$D/codex-heartbeat.pid
if [ -f $PIDF ] && kill -0 "$(cat $PIDF)" 2>/dev/null && [ "$(cat $PIDF)" != $$ ]; then exit 0; fi
echo $$ > $PIDF
log(){ echo "$(date '+%F %T') $*" >> $D/codex-heartbeat.log; }
until_of(){ # $1 = "try again at ..." text -> ISO time (Berlin) or empty; handles "1:46 PM" and "Oct 24th, 2026 10:43 PM"
  local t ts now; t=$(sed -E 's/^try again at //; s/\.$//; s/([0-9])(st|nd|rd|th)/\1/; s/,//g' <<<"$1")
  ts=$(date -d "$t" +%s 2>/dev/null) || return 0; now=$(date +%s)
  [[ "$t" =~ ^[0-9]{1,2}:[0-9]{2} ]] && [ "$ts" -le "$now" ] && ts=$((ts+86400))
  date -d @$ts +%FT%T%:z; }
state(){ # prints "<state> <until-or-empty>"
  local last lg m
  pgrep -f '^bash run_loop.sh' >/dev/null || { echo "stopped"; return; }
  pgrep -f 'codex exec .*-o /workspace/siedler/codex-box/' >/dev/null && { echo "active"; return; }
  last=$(tail -n1 $D/run_loop.status)
  case "$last" in
    *"usage limit: sleeping until "*) m=$(sed -E 's/.*sleeping until ([^ ]+).*/\1/' <<<"$last"); echo "blocked $(date -d "$m" +%FT%T%:z 2>/dev/null)"; return ;;
    *"LOOP EXIT"*|*stopping*) echo "stopped"; return ;;
  esac
  lg=$(ls -t $D/run_*_attempt*.log 2>/dev/null | head -1)
  m=$(grep -oE 'try again at [A-Z][a-z]{2} [0-9]{1,2}(st|nd|rd|th)?,? [0-9]{4} [0-9]{1,2}:[0-9]{2} ?[AP]M|try again at [0-9]{1,2}:[0-9]{2} ?[AP]M' "$lg" 2>/dev/null | tail -1)
  case "$last" in
    *"real failure"*|*"transient"*|*"retry"*) [ -n "$m" ] && { echo "blocked $(until_of "$m")"; return; }; echo "waiting"; return ;;
    *"END "*"exit=0"*) local te; te=$(grep -oE '[0-9-]{10}T[0-9:]{8}$' <<<"$last"); [ $(( $(date +%s) - $(date -d "$te" +%s) )) -lt 900 ] && { echo active; return; }; echo waiting; return ;;
    *"START "*|*"runner started"*) echo "active"; return ;;
  esac
  echo "waiting"; }
board_note(){ # $1 state $2 until: one append-only note on clean/integration (direct board path), retries on concurrent pushes
  local tmp; tmp=$(mktemp -d)
  for i in 1 2 3 4; do
    rm -rf "$tmp/r"; git clone -q --depth 1 --filter=blob:none --no-checkout -b clean/integration https://github.com/Kofi55533/siedler-sim-rs.git "$tmp/r" 2>/dev/null || { sleep 20; continue; }
    ( cd "$tmp/r" && git sparse-checkout set --no-cone /docs/AGENT_BOARD.md >/dev/null 2>&1 && git checkout -q clean/integration 2>/dev/null &&
      printf '\n## %s Codex loop heartbeat\n\nCodex box loop is %s%s. Codex LANE-CLAIM lines are stale until the loop is active again: they hold no PR in s5-gate/regate and do not block planner tasks (scripts/lane-claims.py --heartbeat, release `heartbeat` of siedler-agent). Codex re-claims when it resumes.\n' \
        "$(TZ=Europe/Berlin date '+%Y-%m-%d %H:%M') Berlin —" "$1" "${2:+ until $(TZ=Europe/Berlin date -d "$2" '+%Y-%m-%d %H:%M') Berlin}" >> docs/AGENT_BOARD.md &&
      git -c user.name=siedler-agent -c user.email=Kofi55533@users.noreply.github.com commit -qm "board: Codex loop $1, Codex claims stale" docs/AGENT_BOARD.md &&
      git push -q origin HEAD:refs/heads/clean/integration 2>/dev/null ) && { log "board note posted ($1)"; rm -rf "$tmp"; return 0; }
    sleep $((15*i))
  done; rm -rf "$tmp"; log "board note FAILED ($1)"; return 1; }
prev=""; lastpub=0; noted=$(cat $D/codex-heartbeat.noted 2>/dev/null)
# API budget (2026-10-20 13:00-13:26 the account hit 5000/5000 REST calls/h): the state is derived locally every 60 s (no API);
# a publish (~3 REST calls) happens on a state change and otherwise every 5 min. After a failed publish the next try waits
# 5 min, or until the rate-limit reset if the core limit is exhausted (was: retry every 60 s, 13:05-13:26 ~60 tries/h).
nexttry=0
log "started pid=$$"
while true; do
  read -r s u < <(state)
  now=$(date +%s)
  if { [ "$s $u" != "$prev" ] || [ $((now-lastpub)) -ge 300 ]; } && [ "$now" -ge "$nexttry" ]; then
    if "$HB" put Codex "$s" "$u"; then lastpub=$now; nexttry=0; [ "$s $u" != "$prev" ] && log "published $s ${u}"; prev="$s $u"
    else
      rl=$(gh api rate_limit -q '"\(.resources.core.remaining) \(.resources.core.reset)"' 2>/dev/null)  # rate_limit itself is free
      if [ "${rl%% *}" = 0 ]; then nexttry=$(( ${rl##* } + 5 )); log "publish failed ($s): API rate limit, next try $(date -d @$nexttry '+%H:%M')"
      else nexttry=$((now+300)); log "publish failed ($s), next try in 5 min"; fi
    fi
  fi
  # one board note per non-active episode (blocked/stopped); re-armed when the loop is active again
  if [ "$s" = active ]; then [ -n "$noted" ] && { noted=""; rm -f $D/codex-heartbeat.noted; }
  elif [ "$s" = blocked ] || [ "$s" = stopped ]; then
    [ -z "$noted" ] && board_note "$s" "$u" && { noted="$s"; echo "$s" > $D/codex-heartbeat.noted; }
  fi
  sleep 60
done
