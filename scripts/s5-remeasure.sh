#!/usr/bin/env bash
# s5-remeasure.sh [fixture ...]: re-measure generated fixtures on current clean/integration without an LLM
# (Dominic 2026-10-20, fix 3: genb/genc "re-measure" tasks no longer go to DeepSeek; gate-baseline mode=remeasure runs this).
# Default fixtures: every genb-*/genc-* scenario on the scoreboard (data.json). Each is installed with s5-fixture-install.sh,
# run with s5-live.sh in parallel, and the results are appended as ONE board entry on clean/integration (direct path,
# append-only, retried on concurrent pushes) in the scoreboard format: "<fixture>: DIVERGENT turn N" / "<fixture>: IDENTICAL N".
# Prints only fixture names and turn numbers. Needs GH_TOKEN with push access to siedler-sim-rs.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/s5-env.sh"
W=$S5/remeasure; rm -rf "$W"; mkdir -p "$W/r"
cd "$S5_REPO" && git fetch -q origin "+refs/heads/clean/integration:refs/remotes/origin/clean/integration" && git checkout -q --detach origin/clean/integration || { echo "checkout failed"; exit 1; }
bs=$(git rev-parse origin/clean/integration)
FX="$*"
[ -n "$FX" ] || FX=$(gh api -H "Accept: application/vnd.github.raw" "repos/Kofi55533/siedler-agent/contents/data.json?ref=gh-pages" 2>/dev/null |
                      python3 -c "import json,sys;print(' '.join(s['name'] for s in json.load(sys.stdin).get('scenarios',[]) if s['name'].startswith(('genb-','genc-'))))")
"$D/s5-build.sh" >/dev/null || { echo "build failed on ${bs:0:8}"; exit 1; }
ok=""; for x in $FX; do "$D/s5-fixture-install.sh" "$x" >> "$W/install.txt" 2>/dev/null && ok="$ok $x"; done
cat "$W/install.txt"
PAR=${S5_GATE_PARALLEL:-}; if [ -z "$PAR" ]; then m=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo); PAR=$(( m / 3 )); [ $PAR -gt $(nproc) ] && PAR=$(nproc); [ $PAR -lt 1 ] && PAR=1; fi
export D W
one(){ local out n k; out=$("$D/s5-live.sh" "$1" 2>/dev/null); n=$(grep -oE 'DIVERGENT turn [0-9]+' <<<"$out" | grep -oE '[0-9]+$')
  k=$(grep -oE 'IDENTICAL [0-9]+ ticks' <<<"$out" | grep -oE '[0-9]+')
  if [ -n "$n" ]; then echo "$1: DIVERGENT turn $n" > "$W/r/$1"; elif [ -n "$k" ]; then echo "$1: IDENTICAL $k" > "$W/r/$1"
  else echo "no result: $(tail -n1 <<<"$out" | cut -c1-160)" >&2; fi; }   # s5-live verdict line only (exit code + message)
export -f one
t0=$(date +%s); printf '%s\n' $ok | xargs -P "$PAR" -I{} bash -c 'one "$1"' _ {}
res=$(for x in $ok; do [ -s "$W/r/$x" ] && cat "$W/r/$x"; done); nores=$(for x in $FX; do [ -s "$W/r/$x" ] || printf '%s ' "$x"; done)
echo "$res"; echo "no result: ${nores:-none}; parallel=$PAR wall $(( $(date +%s)-t0 ))s"
[ -n "$res" ] || exit 1
[ "${S5_REMEASURE_BOARD:-1}" = 1 ] || exit 0
for i in 1 2 3 4; do
  git fetch -q origin "+refs/heads/clean/integration:refs/remotes/origin/clean/integration" && git checkout -q --detach origin/clean/integration || break
  { printf '\n## %s Muse CI re-measure (gate-baseline remeasure, no LLM)\n\n' "$(TZ=Europe/Berlin date '+%Y-%m-%d %H:%M') Berlin —"
    printf 'live re-measure on clean/integration @%s (s5-live.sh, S5_LIVE_TIMEOUT=%s):\n' "${bs:0:8}" "${S5_LIVE_TIMEOUT:-1800}"
    sed 's/^/- /' <<<"$res"; [ -n "$nores" ] && printf -- '- not measured (not installable / no result): %s\n' "$nores"; } >> docs/AGENT_BOARD.md
  git -c user.name=siedler-agent -c user.email=Kofi55533@users.noreply.github.com commit -qm "board: CI re-measure genb/genc on ${bs:0:8}" docs/AGENT_BOARD.md &&
    git push -q origin HEAD:refs/heads/clean/integration && { echo "board: posted"; exit 0; }
  git reset -q --hard origin/clean/integration; sleep $((10*i))
done
echo "board: post failed"; exit 1
