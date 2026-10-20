#!/usr/bin/env bash
# Codex continuous runner (box). Phases: resume -> task5 -> loop. Phase kept in phase.state so a restart continues.
# 2026-10-20 (Dominic, bottleneck fix 1): usage limits in both formats ("try again at 1:46 PM" and "try again at Oct 24th,
# 2026 10:43 PM") sleep until that time (60 s slices; wakes early on $D/WAKE or a Codex re-login = ~/.codex/auth.json changed)
# instead of counting as a "real failure" and probing every 10 min; an unparseable limit probes hourly. The heartbeat
# publisher codex-heartbeat.sh (started here if not running) marks the loop blocked, so Codex lane claims stop holding PRs.
D=/workspace/siedler/codex-box; W=$D/wt
source /workspace/siedler/box/env.sh
export GH_TOKEN="$GH_TOKEN_DOMINIC" GITHUB_TOKEN="$GH_TOKEN_DOMINIC"
st(){ echo "$* $(date +%FT%T)" >> $D/run_loop.status; }
hb_up(){ { [ -f $D/codex-heartbeat.pid ] && kill -0 "$(cat $D/codex-heartbeat.pid)" 2>/dev/null; } || (cd $D && nohup bash $D/codex-heartbeat.sh >/dev/null 2>&1 &); }
limit_until(){ # $1 = log -> epoch of "try again at ..." (+120 s), empty if the log has no usage-limit message
  local m t ts now
  m=$(grep -oE 'try again at [A-Z][a-z]{2} [0-9]{1,2}(st|nd|rd|th)?,? [0-9]{4},? [0-9]{1,2}:[0-9]{2} ?[AP]M|try again at [0-9]{1,2}:[0-9]{2} ?[AP]M' "$1" | tail -1)
  if [ -z "$m" ]; then grep -qiE 'usage limit|hit your .{0,20}limit|rate.?limit.{0,40}try again' "$1" && echo $(( $(date +%s) + 3600 )); return; fi
  t=$(sed -E 's/^try again at //; s/([0-9])(st|nd|rd|th)/\1/; s/,//g' <<<"$m"); ts=$(date -d "$t" +%s 2>/dev/null) || { echo $(( $(date +%s) + 3600 )); return; }
  now=$(date +%s); [[ "$t" =~ ^[0-9]{1,2}:[0-9]{2} ]] && [ $ts -le $now ] && ts=$((ts+86400))
  echo $((ts+120)); }
nap(){ # $1 = wake epoch: sleep in 60 s slices, early on $D/WAKE or a changed ~/.codex/auth.json
  local a0; a0=$(stat -c %Y ~/.codex/auth.json 2>/dev/null)
  while [ "$(date +%s)" -lt "$1" ]; do
    sleep 60
    [ -f $D/WAKE ] && { rm -f $D/WAKE; st "woken by WAKE"; return; }
    [ "$(stat -c %Y ~/.codex/auth.json 2>/dev/null)" != "$a0" ] && { st "woken: codex auth changed (re-login)"; return; }
  done; }
phase=$(cat $D/phase.state 2>/dev/null || echo resume); n=$(cat $D/iter.state 2>/dev/null || echo 1); fails=0
st "runner started pid=$$ phase=$phase"
while true; do
  [ -f $D/DONE.flag ] && { st "DONE.flag found, stopping"; break; }
  hb_up
  case $phase in
    resume) P=$D/prompt_resume.txt; C=$W/codex-clean-loader; tag=resume;;
    task5)  P=$D/prompt_task5.txt;  C=$W/codex-clean-loader; tag=task5;;
    *)      P=$D/prompt_loop.txt;   C=$W/clean-integration;  tag=loop$n;;
  esac
  log=$D/run_${tag}_attempt$(date +%m%d%H%M).log
  st "START $tag ($log)"
  (cd $C && cat $D/box_context.txt $P | codex exec -m gpt-6.1-sol --dangerously-bypass-approvals-and-sandbox -o $D/final/final_message_$tag.md - > $log 2>&1)
  code=$?
  st "END $tag exit=$code"
  if [ $code -eq 0 ]; then
    fails=0
    case $phase in resume) phase=task5;; task5) phase=loop;; *) n=$((n+1)); echo $n > $D/iter.state;; esac
    echo $phase > $D/phase.state; sleep 120; continue
  fi
  ts=$(limit_until "$log")
  if [ -n "$ts" ]; then
    fails=0   # a usage limit is not a real failure
    st "usage limit: sleeping until $(date -d @$ts +%FT%T)"; nap "$ts"; continue
  fi
  if grep -qE 'stream disconnect|disconnected|high demand|stream error' $log; then st "transient error, retry in 10 min"; sleep 600; continue; fi
  fails=$((fails+1)); st "real failure $fails/5"
  [ $fails -ge 5 ] && { st "5 consecutive real failures, stopping"; break; }
  sleep 600
done
st "LOOP EXIT"
