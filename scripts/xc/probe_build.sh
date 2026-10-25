#!/usr/bin/env bash
# probe_build.sh: wall seconds of the capture build after a change in crates/s5clean (what the model's check and every
# verdict pay) under build variants, and of one capture point played with each variant's binaries. Counts only.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/lib.sh"
cd "$S5_REPO"; rustc -V; cargo -V; nproc; free -g | head -2
echo "lld: $(command -v ld.lld mold 2>/dev/null | tr "\n" " ") rust-lld: $(ls $(rustc --print sysroot)/lib/rustlib/*/bin/rust-lld 2>/dev/null | wc -l)"
build(){ # build <label>: cargo test --no-run of the capture tests with $XC_CARGO_ARGS, wall seconds
  local t=$(date +%s)
  ( CARGO_TARGET_DIR=${XC_TARGET:-$S5_REPO/recomp/target} cargo test --release --no-run -j "${CARGO_BUILD_JOBS:-3}" \
      --manifest-path crates/s5xcheck/Cargo.toml --test capture-execute-task --test capture-entity-tick --test capture-ticks \
      --test capture-functions $XC_CARGO_ARGS --message-format=json 2> "$XC/pb.log" | python3 -c 'import sys,json
for l in sys.stdin:
    d=json.loads(l)
    if d.get("reason")=="compiler-artifact" and d.get("executable"): print(d["target"]["name"], d["executable"])' > "$XC/bins.txt" )
  echo "build $1: rc=$? secs=$(( $(date +%s) - t )) compiled=$(grep -c 'Compiling' "$XC/pb.log")"
}
touch_clean(){ echo "// probe $RANDOM" >> crates/s5clean/src/lib.rs; }
point(){ # point <label> <bin>: one full capture point on economy at XC_TURNS_SHORT
  read -r name img skip turns < <(xc_scenarios | awk '$1=="economy"')
  local t=$(date +%s)
  ( export S5_XCHECK_IMAGE=$img S5_CAPTURE_SKIP=$skip S5_CAPTURE_TURNS=$XC_TURNS_SHORT S5_CAPTURE_MAX=$XC_MAX \
      S5_CAPTURE_STRIDE=$XC_STRIDE S5_CAPTURE_JSON=$XC/pp.json S5_CAPTURE_FUNCTIONS=$XC/inventory.txt
    timeout 1200 "$(xc_bin "$2")" --nocapture --test-threads=1 ) < /dev/null > "$XC/pp.log" 2>&1
  echo "point $1 $2: rc=$? secs=$(( $(date +%s) - t )) json=$(stat -c %s "$XC/pp.json" 2>/dev/null)"
}
xc_inventory > /dev/null; xc_cmds
cp recomp/Cargo.lock crates/s5xcheck/Cargo.lock
v(){ # v <label> <cargo args>: warm build, then build after a clean change, then the points
  export XC_CARGO_ARGS=$2; build "$1-warm"; touch_clean; build "$1-touched"
  touch crates/s5xcheck/tests/capture-functions.rs; build "$1-test-only"
  point "$1" capture-functions; point "$1" capture-execute-task
  md5sum "$XC/pp.json" | cut -c1-12
}
v base ""
v clean-o0 "--config profile.release.package.s5clean.opt-level=0"
v clean-o1 "--config profile.release.package.s5clean.opt-level=1"
v clean-o0-cgu256 "--config profile.release.package.s5clean.opt-level=0 --config profile.release.package.s5clean.codegen-units=256 --config profile.release.package.s5xcheck.codegen-units=256"
v xcheck-o1 "--config profile.release.package.s5clean.opt-level=0 --config profile.release.package.s5xcheck.opt-level=1"
df -h / | tail -1
