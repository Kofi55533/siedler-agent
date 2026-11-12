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
  grep -q 'recorded clock, tasks, entities and RNG timeline' crates/s5clean/examples/observe.rs 2>/dev/null && break
  sleep 10
done
grep -q 'recorded clock, tasks, entities and RNG timeline' crates/s5clean/examples/observe.rs
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
fast=oracle-bc-full-rng-1f5fd5735587262c776fa6b48decd05d0c587630
prior=oracle-bc-full-acc27e172e500233a61fcb6eb177142b5db664c8
export CARGO_TARGET_DIR="$S5_REPO/recomp/target"
(cd crates/s5clean && cargo build --locked --release -j 2 --example observe) >> "$log" 2>&1
if [ "$mode" = combined-smoke ]; then
  python tools/oracle_observe.py --scenario "$sc" --snapshot "$snapshot" --game "$S5_GAME" \
    --runtime "$S5_ARTIFACTS/boot/runtime" --output "$record" --ticks 64 "${hasher[@]}" >> "$log" 2>&1
  python tools/oracle_observe.py show "$record" "$(jq -er '.start_turn + 2' "$sc")" >/dev/null 2>> "$log"
else
  echo "Waiting for the verified Oracle archive"
  until gh release download "$fast" -R "$repo" -p bc-full-oracle.jsonl.gz -D "$work" --clobber >> "$log" 2>&1; do
    sleep 45
  done
  gzip -t "$record"
  echo "Verified gzip integrity for $fast" >> "$log"
fi

cd crates/s5clean
limit=()
scope=()
case "$mode" in
  rng-owners|rng-streams|rng-all|entities) scope=("--$mode") ;;
  combined-full) ;;
  combined-smoke)
  base=27b3d69f81ba52bcccb32704d95fef1752bc049e
  git -C "$S5_REPO" fetch -q origin "$base" >> "$log" 2>&1
  git -C "$S5_REPO" worktree add --detach "$work/base" "$base" >> "$log" 2>&1
  candidate_rc=0
  base_rc=0
  cargo check --locked --no-default-features --lib > "$work/candidate-feature.txt" 2>&1 || candidate_rc=$?
  (cd "$work/base/crates/s5clean" && cargo check --locked --no-default-features --lib) > "$work/base-feature.txt" 2>&1 || base_rc=$?
  cat "$work/candidate-feature.txt" "$work/base-feature.txt" >> "$log"
  [ "$candidate_rc" = "$base_rc" ]
  diff -u <(grep '^error\[' "$work/base-feature.txt" | sort) <(grep '^error\[' "$work/candidate-feature.txt" | sort) >> "$log"
  echo "No-default-features result matches the base revision" >> "$log"
  cargo test --locked --release -j 2 --lib rng::trace::tests >> "$log" 2>&1
  cargo test --locked --release -j 2 --example observe >> "$log" 2>&1
    limit=(64)
    ;;
  *) echo "Unknown observation mode $mode"; exit 2 ;;
esac
rc=0
/usr/bin/time -f 'wall %e s' "$CARGO_TARGET_DIR/release/examples/observe" "$sc" "$img" "$record" \
  "${limit[@]}" "${scope[@]}" > "$work/compare.txt" 2>&1 || rc=$?
cat "$work/compare.txt" >> "$log"
[ "$rc" -le 1 ]
if [ "$mode" = combined-smoke ]; then
  [ "$rc" = 0 ]
  grep -E '^[1-9][0-9]* verified matching draw recipients$' "$work/compare.txt"
fi
grep -oE '^bc-full: (DIVERGENT turn [0-9]+|IDENTICAL [0-9]+ ticks)' "$work/compare.txt"
