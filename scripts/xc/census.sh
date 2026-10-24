#!/usr/bin/env bash
# census.sh <shard> <shards> <out-dir>: the full per-call comparison (every task kind) of every <shards>-th scenario
# on the current $S5_REPO checkout; <out-dir>/<scenario>.json each. Prints counts only.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/lib.sh"
I=$1; N=$2; OUT=$3; mkdir -p "$OUT"
xc_build || exit 2
# the shards split the scenarios by their run seconds of the previous census (XC_WEIGHTS, from the pin job): the
# 2026-10-25 census at 4 shards took 17-29 min per shard but 48 on the one holding two of the heaviest scenarios
xc_scenarios | python3 "$D/xc.py" shard "$I" "$N" > "$XC/shard.txt"
echo "shard $I/$N: $(wc -l < "$XC/shard.txt") scenarios on $(git -C "$S5_REPO" rev-parse --short HEAD)"
rm -f "$XC"/secs-*
t0=$(date +%s); xc_runs "$OUT" < "$XC/shard.txt"
# each scenario's run seconds (its points summed) go into its document, so the next census can balance its shards
while read -r name _; do
  [ -s "$OUT/$name.json" ] && python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));d["secs"]=sum(map(int,sys.argv[2:]))
json.dump(d,open(sys.argv[1],"w"))' "$OUT/$name.json" $(cat "$XC/secs-$name".* 2>/dev/null)
done < "$XC/shard.txt"
# the order of the function units (xc.py merge): static calls between class-A functions, and the native addresses the
# clean code cites; addresses only, private like the shard
mkdir -p "$OUT/_meta"
[ -f "$S5_REPO/data/scope/fn_calls.json" ] && cp "$S5_REPO/data/scope/fn_calls.json" "$OUT/_meta/"
grep -rhoiE '0x0*[4-6][0-9a-f]{5}\b' "$S5_REPO/crates/s5clean/src" | tr 'A-F' 'a-f' | sed 's/^0x0*//' | sort -u > "$OUT/_meta/cited.txt"
while read -r name _; do
  [ -s "$OUT/$name.json" ] && python3 -c "import json,sys;d=json.load(open(sys.argv[1]));g=d['groups'].values()
print(f\"{sys.argv[2]}: {d['entries']} calls, {len(d['groups'])} kinds, {sum(x['failing'] for x in g)}/{sum(x['samples'] for x in g)} differ, {len(d['import_errors'])} import errors\"+(f\", points without result: {','.join(d['missing_points'])}\" if d.get('missing_points') else ''))" "$OUT/$name.json" "$name" \
  || echo "$name: no result (exit $(cat "$XC/rc-$name" 2>/dev/null)$(grep -qx 124 "$XC/rc-$name" 2>/dev/null && echo ", timeout"))"
  # a point without result (crash, timeout): the run log may name game state, so it goes only into the private shard
  # upload, never to this log
  grep -qx 0 "$XC/rc-$name" 2>/dev/null || { mkdir -p "$OUT/_logs" && tail -n 120 "$XC/run-$name.log" > "$OUT/_logs/$name.log" 2>/dev/null; }
done < "$XC/shard.txt"
echo "shard secs=$(( $(date +%s) - t0 ))"
