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

case "$mode" in
  animal-smoke|animal-full) ;;
  *) exit 2 ;;
esac
export CARGO_TARGET_DIR="$S5_REPO/recomp/target"
(cd crates/s5clean && cargo build --locked --release -j 2 --example observe) >> "$log" 2>&1
diff="$CARGO_TARGET_DIR/release/examples/observe"
if [ "$mode" = animal-smoke ]; then
  run_ticks=192
  python tools/oracle_observe.py --snapshot "$snapshot" --game "$S5_GAME" --scenario "$sc" --runtime "$S5_ARTIFACTS/boot/runtime" --output "$record" --ticks "$run_ticks" "${hasher[@]}" >> "$log" 2>&1
else
  tag=oracle-bc-full-windows-43301884274
  release="repos/Kofi55533/siedler-sim-rs/releases/tags/$tag"
  for attempt in $(seq 1 90); do
    asset=$(gh api "$release" --jq '.assets[] | select(.name == "bc-full-oracle.jsonl.gz") | .url' 2>/dev/null || true)
    [ -n "$asset" ] && break
    sleep 10
  done
  test -n "$asset"
  curl --fail --silent --show-error --location --retry 3 -H "Authorization: Bearer $GH_TOKEN" -H 'Accept: application/octet-stream' "$asset" -o "$record" >> "$log" 2>&1
  run_ticks=23900
fi
python tools/oracle_observe.py show "$record" 266 | jq -e '[.seed[] | select(.owner.kind == "animal_wander" and .owner.entity == 80404)] | length == 2' >> "$log" 2>&1
echo "Oracle entering tick 266: both animal draws identify entity 80404" >> "$log"
cd crates/s5clean
set +e
/usr/bin/time -f 'wall=%e user=%U sys=%S' "$diff" "$sc" "$img" "$record" "$run_ticks" --rng-all >> "$log" 2>&1
status=$?
set -e
if [ "$mode" = animal-smoke ]; then
  [ "$status" -eq 0 ]
  grep -Eq '^bc-full: IDENTICAL 192 ticks' "$log"
  grep -Eq '^42 verified matching draw recipients$' "$log"
else
  [ "$status" -eq 1 ]
  grep -Eq '^bc-full: DIVERGENT turn 18007:' "$log"
  grep -Eq '^27703 verified matching draw recipients before the difference$' "$log"
fi
