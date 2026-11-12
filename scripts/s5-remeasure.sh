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
  grep -q '^def join_reference' tools/oracle_observe.py 2>/dev/null && break
  sleep 10
done
grep -q '^def join_reference' tools/oracle_observe.py
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

repo=Kofi55533/siedler-sim-rs
original=oracle-bc-full-rng-1f5fd5735587262c776fa6b48decd05d0c587630
tag="oracle-bc-full-windows-$GITHUB_RUN_ID"
start=$(jq -er '.start_turn' "$sc")
end=$(jq -er '.start_turn + .turns' "$sc")
config="$GITHUB_WORKSPACE/remeasure-shards.txt"
mapfile -t windows < <(grep -E '^[0-9]+-[0-9]+$' "$config")
fetch_asset() {
  until gh release download "$1" -R "$repo" -p "$2" -D "$3" --clobber > "$work/download.txt" 2>&1; do
    sleep 30
  done
}

if [ "$mode" = wait-smoke ] || [ "$mode" = wait-compare ]; then
  if [ "$mode" = wait-smoke ]; then
    python tools/oracle_observe.py --scenario "$sc" --snapshot "$snapshot" --game "$S5_GAME" \
      --runtime "$S5_ARTIFACTS/boot/runtime" --output "$record" --ticks 64 "${hasher[@]}" >> "$log" 2>&1
    python tools/oracle_observe.py show "$record" 159 \
      | jq -e '.seed[] | select(.site == 5800394 and .owner.kind == "animation_wait" and .owner.entity == 83053) | .owner' \
      >> "$log"
  fi
  export CARGO_TARGET_DIR="$S5_REPO/recomp/target"
  cd crates/s5clean
  cargo build --locked --release -j 2 --example observe >> "$log" 2>&1
  limit=(64)
  if [ "$mode" = wait-compare ]; then
    fetch_asset "$tag" bc-full-oracle.jsonl.gz "$work"
    limit=()
  fi
  rc=0
  /usr/bin/time -f 'wall %e s' "$CARGO_TARGET_DIR/release/examples/observe" "$sc" "$img" "$record" \
    "${limit[@]}" --rng-all > "$work/compare.txt" 2>&1 || rc=$?
  cat "$work/compare.txt" >> "$log"
  if [ "$mode" = wait-smoke ]; then
    [ "$rc" -eq 0 ]
    grep -E '^[1-9][0-9]+ verified matching draw recipients$' "$work/compare.txt"
  else
    [ "$rc" -le 1 ]
  fi
  grep -oE '^bc-full: (DIVERGENT turn [0-9]+|IDENTICAL [0-9]+ ticks)' "$work/compare.txt"
  exit
fi

if [ "$mode" = join ]; then
  gh release create "$tag" -R "$repo" --target "$rev" --prerelease \
    --title "BC Full parallel Oracle recording" \
    --notes "These are repeatable windows from validated native checkpoints. bc-full-oracle.jsonl.gz appears after the join and comparison to the uninterrupted run." >> "$log" 2>&1
  segments=()
  for window in "${windows[@]}"; do
    fetch_asset "$tag" "window-$window.jsonl.gz" "$work"
    fetch_asset "$tag" "time-$window.txt" "$work"
    segments+=("$work/window-$window.jsonl.gz")
  done
  /usr/bin/time -f 'join wall %e s' python tools/oracle_observe.py join "$sc" "$record" "${segments[@]}" >> "$log" 2>&1
  gzip -t "$record"
  mkdir -p "$work/original"
  fetch_asset "$original" bc-full-oracle.jsonl.gz "$work/original"
  python - "$work/original/bc-full-oracle.jsonl.gz" "$record" "$work" >> "$log" 2>&1 <<'PY'
import gzip
from itertools import zip_longest
import json
from pathlib import Path
import sys

original, joined, work = map(Path, sys.argv[1:])
with gzip.open(original, "rt") as a, gzip.open(joined, "rt") as b:
    old, new = json.loads(next(a)), json.loads(next(b))
    for key in ("start", "turns", "scenario", "recorded_commands", "snapshot_sha256", "observation_profile"):
        if old[key] != new[key]:
            raise ValueError(f"joined recording differs in {key}")
    n = 0
    confirmed = 0
    animation_wait = 0
    wait_sites = 0
    for raw_a, raw_b in zip_longest(a, b):
        if raw_a is None or raw_b is None:
            raise ValueError("joined recording has a different number of rows")
        n += 1
        prev, have = json.loads(raw_a), json.loads(raw_b)
        for draw in have["seed"]:
            owner = draw.pop("owner", None)
            if draw["site"] == 0x5881CA:
                wait_sites += 1
                assert owner is not None and owner["kind"] == "animation_wait"
                animation_wait += int(owner["entity"] != 0)
            elif owner is not None:
                assert owner["kind"] == "building_departure" and draw["site"] == 0x4A9E53
                candidate = draw["entity_candidates"].get("edi")
                if candidate is not None:
                    assert owner["building"] == candidate
                    confirmed += 1
        if prev != have:
            raise ValueError(f"joined observations differ entering tick {prev['state']['turn']}")
    assert n == new["turns"] + 1
    print(f"{n} state rows and all recorded events equal to uninterrupted Oracle")
    print(f"{confirmed} explicit building IDs also match the earlier EDI evidence")
    print(f"Animation wait recipients: {animation_wait} entity IDs in {wait_sites} draws")
timings = {path.stem[5:]: float(path.read_text()) for path in work.glob("time-*.txt")}
slowest = max(timings, key=timings.get)
print(f"Each window, Oracle process seconds: {timings}")
print(f"Slowest {slowest}: {timings[slowest]:.2f} seconds")
print(f"Sequential process time of the same windows: {sum(timings.values()):.2f} seconds")
print(f"Joined archive {joined.stat().st_size} bytes")
PY
  gh release upload "$tag" -R "$repo" "$record" >> "$log" 2>&1
  grep -E '(^[0-9]+ state rows|^Slowest |^Sequential process time|^Joined archive)' "$log"
  exit
fi

[[ "$mode" =~ ^([0-9]+)-([0-9]+)$ ]]
from="${BASH_REMATCH[1]}"
to="${BASH_REMATCH[2]}"
[[ "$start" -le "$from" && "$from" -lt "$to" && "$to" -le "$end" ]]
if [ "$from" != "$start" ]; then
  echo "Waiting for native checkpoint $from"
  fetch_asset "$original" "turn-$from.bin" "$work"
  snapshot="$work/turn-$from.bin"
fi
until gh release view "$tag" -R "$repo" >/dev/null 2>&1; do sleep 15; done
window="$work/window-$mode.jsonl.gz"
echo "Recording $from through $to"
time_file="$work/time-$mode.txt"
/usr/bin/time -f '%e' -o "$time_file" python tools/oracle_observe.py --scenario "$sc" --snapshot "$snapshot" \
  --game "$S5_GAME" --runtime "$S5_ARTIFACTS/boot/runtime" --output "$window" --from-turn "$from" \
  --ticks "$((to - from))" "${hasher[@]}" 2>> "$log" | tee -a "$log"
gzip -t "$window"
gh release upload "$tag" -R "$repo" "$window" "$time_file" --clobber >> "$log" 2>&1
