#!/usr/bin/env bash
# census.sh <shard> <shards> <out-dir>: the full per-call comparison (every task kind) of every <shards>-th scenario
# on the current $S5_REPO checkout; <out-dir>/<scenario>.json each. Prints counts only.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/lib.sh"
I=$1; N=$2; OUT=$3; mkdir -p "$OUT"
xc_build || exit 2
xc_scenarios | awk -v i="$I" -v n="$N" '(NR - 1) % n == i' > "$XC/shard.txt"
echo "shard $I/$N: $(wc -l < "$XC/shard.txt") scenarios on $(git -C "$S5_REPO" rev-parse --short HEAD)"
t0=$(date +%s); xc_runs "$OUT" < "$XC/shard.txt"
while read -r name _; do
  [ -s "$OUT/$name.json" ] && python3 -c "import json,sys;d=json.load(open(sys.argv[1]));g=d['groups'].values()
print(f\"{sys.argv[2]}: {d['entries']} calls, {len(d['groups'])} kinds, {sum(x['failing'] for x in g)}/{sum(x['samples'] for x in g)} differ, {len(d['import_errors'])} import errors\")" "$OUT/$name.json" "$name" \
  || { rc=$(cat "$XC/rc-$name" 2>/dev/null); echo "$name: no result (exit ${rc:-?}$([ "$rc" = 124 ] && echo ", timeout"))"
       # the run log may name game state: it goes only into the private shard upload, never to this log
       mkdir -p "$OUT/_logs" && tail -n 80 "$XC/run-$name.log" > "$OUT/_logs/$name.log" 2>/dev/null; }
done < "$XC/shard.txt"
echo "shard secs=$(( $(date +%s) - t0 ))"
