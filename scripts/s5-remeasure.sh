#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/s5-env.sh"
mode=$1
work="$RUNNER_TEMP/oracle-observation"
mkdir -p "$work"
log="$work/result.txt"
: > "$log"
save() {
  rc=$?
  trap - EXIT
  path="repos/Kofi55533/siedler-sim-rs/contents/results/oracle-observation-$GITHUB_RUN_ID-$mode.txt"
  branch=codex/replay-observation-results
  old=$(gh api "$path?ref=$branch" --jq .sha 2>/dev/null || true)
  base64 -w0 "$log" | jq -Rs --arg branch "$branch" --arg old "$old" \
    '{message:"Record Oracle replay observation",branch:$branch,content:.}+ (if $old=="" then {} else {sha:$old} end)' > "$work/report.json"
  gh api --method PUT "$path" --input "$work/report.json" >/dev/null 2>&1 || echo "Private report upload failed"
  echo "Oracle observation $mode exit=$rc"
  exit "$rc"
}
trap save EXIT
cd "$S5_REPO"
for attempt in $(seq 1 150); do
  git fetch -q origin "$S5_REMEASURE_REF" >> "$log" 2>&1
  git checkout -q --detach FETCH_HEAD >> "$log" 2>&1
  grep -q 's5_entity_rows' tools/oracle_task_hashes.c 2>/dev/null && break
  sleep 10
done
grep -q 's5_entity_rows' tools/oracle_task_hashes.c
rev=$(git rev-parse HEAD)
echo "candidate $rev" >> "$log"
cc -O3 -shared -fPIC tools/oracle_task_hashes.c -o "$work/task-hasher.so" >> "$log" 2>&1
hasher=(--task-hasher "$work/task-hasher.so" --entity-reader "$work/task-hasher.so")
python -m pip install --quiet pefile capstone >> "$log" 2>&1
snapshot="$S5_ARTIFACTS/lift/snap_gs_h.bin"
if [ ! -s "$snapshot" ]; then
  echo "Fetching original Oracle start snapshot"
  mkdir -p "$(dirname "$snapshot")"
  curl --fail --silent --show-error --location --retry 3 \
    -H "Authorization: Bearer $GH_TOKEN" -H 'Accept: application/vnd.github.raw+json' \
    "https://api.github.com/repos/Kofi55533/siedler-forschung/contents/cloud-re/artifacts/recomp/lift/snap_gs_h.bin" \
    -o "$snapshot" >> "$log" 2>&1
fi
test "$(git hash-object "$snapshot")" = f6561c747709e308c2e0581fa366b243da2f7bfb
# data-v2 intentionally omits some media. Restore every original game file that
# the canonical snapshot still holds open, using the same installation it came from.
python - "$snapshot" "$S5_GAME" > "$work/missing-install.txt" 2>> "$log" <<'PY'
from pathlib import Path
import pickle
import sys

with Path(sys.argv[1]).open("rb") as file:
    snapshot = pickle.load(file)
install = Path(sys.argv[2])
paths = [value[1] for value in snapshot["handles"].values() if value[0] == "file"]
paths += [value[1] for value in snapshot["mods"].values()]
marker = "/cloud-re/inputs/game/"
missing = set()
for path in paths:
    path = path.replace("\\", "/")
    if marker in path:
        relative = Path(path.split(marker, 1)[1])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe snapshot installation path")
        if not (install / relative).is_file():
            missing.add(relative.as_posix())
print(*sorted(missing), sep="\n")
PY
while IFS= read -r path; do
  [ -n "$path" ] || continue
  url_path=$(python -c 'from urllib.parse import quote; import sys; print(quote(sys.argv[1]))' "$path")
  mkdir -p "$(dirname "$S5_GAME/$path")"
  curl --fail --silent --show-error --location --retry 3 \
    -H "Authorization: Bearer $GH_TOKEN" -H 'Accept: application/vnd.github.raw+json' \
    "https://api.github.com/repos/Kofi55533/siedler-forschung/contents/cloud-re/inputs/game/$url_path" \
    -o "$work/install-file" >> "$log" 2>&1
  mv "$work/install-file" "$S5_GAME/$path"
done < "$work/missing-install.txt"
sc="$S5_V/fixtures/bc-full/scenario.json"
img="$S5_ARTIFACTS/bc/gs_h.img"
record="$work/bc-full-oracle.jsonl.gz"


export CARGO_TARGET_DIR="$S5_REPO/recomp/target"
(cd crates/s5clean && cargo build --locked --release -j 2 --example observe) >> "$log" 2>&1 &
compile=$!
case "$mode" in
  early-movement) turn=121 ;;
  late-movement)
    turn=10000
    gh release download oracle-bc-full-rng-1f5fd5735587262c776fa6b48decd05d0c587630 -R Kofi55533/siedler-sim-rs \
      -p "turn-$turn.bin" -D "$work" >> "$log" 2>&1
    snapshot="$work/turn-$turn.bin"
    img="$work/start.img"
    python recomp/export_image.py "$snapshot" "$img" --mem-only >> "$log" 2>&1 ;;
  *) exit 2 ;;
esac
ticks=128
python tools/oracle_observe.py --scenario "$sc" --snapshot "$snapshot" --game "$S5_GAME" \
  --runtime "$S5_ARTIFACTS/boot/runtime" --output "$record" --from-turn "$turn" --ticks "$ticks" \
  --movement --player-resources "${hasher[@]}" >> "$log" 2>&1
python - "$sc" "$work/window.json" "$turn" "$ticks" "$record" >> "$log" 2>&1 <<'PY'
import gzip
import json
from pathlib import Path
import sys
source, target = map(Path, sys.argv[1:3])
start, ticks = map(int, sys.argv[3:5])
commands = target.with_suffix(".jsonl")
scenario = json.loads(source.read_text())
with commands.open("w") as out:
    if scenario.get("commands"):
        for line in (source.parent / scenario["commands"]).read_text().splitlines():
            if line.strip() and start <= json.loads(line)["turn"] < start + ticks:
                print(line, file=out)
scenario.update(start_turn=start, turns=ticks, commands=commands.name)
target.write_text(json.dumps(scenario))

with gzip.open(sys.argv[5], "rt") as stream:
    next(stream)
    rows = [json.loads(line) for line in stream]
assert len(rows) == ticks + 1 and rows[0]["movement_updates"]
assert any(part[4] for row in rows for _, parts in row["movement_updates"] for part in parts)
for keys in (("movement_updates", "movement_removed"), ("player_resources",)):
    data = b"".join(json.dumps([row[key] for key in keys], separators=(",", ":")).encode() + b"\n" for row in rows)
    print(f"{keys}: {len(gzip.compress(data, compresslevel=1))} compressed bytes in {len(rows)} rows")
PY
wait "$compile"
cd crates/s5clean
for lane in movement resources; do
  rc=0
  /usr/bin/time -f 'wall %e s' "$CARGO_TARGET_DIR/release/examples/observe" "$work/window.json" "$img" \
    "$record" "--$lane" >> "$log" 2>&1 || rc=$?
  [ "$rc" -le 1 ]
done
grep -E '^bc-full: (IDENTICAL|DIVERGENT)' "$log" | cut -d: -f1-2
