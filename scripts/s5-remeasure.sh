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
  grep -q -- --rng-streams crates/s5clean/examples/observe.rs && break
  sleep 10
done
grep -q -- --rng-streams crates/s5clean/examples/observe.rs
rev=$(git rev-parse HEAD)
echo "candidate $rev" >> "$log"
cc -O3 -shared -fPIC tools/oracle_task_hashes.c -o "$work/task-hasher.so" >> "$log" 2>&1
hasher=(--task-hasher "$work/task-hasher.so")
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

python tools/oracle_observe.py --scenario "$sc" --snapshot "$snapshot" --game "$S5_GAME" \
  --runtime "$S5_ARTIFACTS/boot/runtime" --output "$record" --ticks 64 "${hasher[@]}" 2>> "$log" | tee -a "$log"
python - "$record" >> "$log" <<'PY'
import gzip
import json
import sys
with gzip.open(sys.argv[1], "rt") as stream:
    header = json.loads(next(stream))
    row = json.loads(next(stream))
    print("Recorded profile", header["observation_profile"], "start", header["start"])
    print("Initial Lua seed", row["lua_crt_seed"], "initial entities", len(row["entity_updates"]))
PY
S5_INSTALL="$S5_GAME" ORACLE_SCRATCH="$work/assembly" ORACLE_RUNTIME="$S5_ARTIFACTS/boot/runtime" \
  RESTORE="$snapshot" MODS=1 python - >> "$log" 2>&1 <<'PY'
import sys
sys.path.insert(0, "oracle")
import run
from capstone import Cs, CS_ARCH_X86, CS_MODE_32
engine = run.build()
for instruction in Cs(CS_ARCH_X86, CS_MODE_32).disasm(engine.rb(0x4A9D67, 0x144), 0x4A9D67):
    print(f"{instruction.address:08x} {instruction.mnemonic} {instruction.op_str}")
PY
export CARGO_TARGET_DIR="$S5_REPO/recomp/target"
cd crates/s5clean
cargo build --locked --release -j 2 --example observe >> "$log" 2>&1
cargo test --locked --release -j 2 --lib lua_seed_and_failed_draw_are_observed_in_command_order >> "$log" 2>&1
for scope in --rng-streams --entities; do
  echo "$scope" >> "$log"
  rc=0
  /usr/bin/time -f 'wall %e s' "$CARGO_TARGET_DIR/release/examples/observe" "$sc" "$img" "$record" 64 "$scope" \
    > "$work/compare.txt" 2>&1 || rc=$?
  cat "$work/compare.txt" >> "$log"
  [ "$rc" -le 1 ]
  grep -oE '^bc-full: (DIVERGENT turn [0-9]+|IDENTICAL [0-9]+ ticks)' "$work/compare.txt"
done
