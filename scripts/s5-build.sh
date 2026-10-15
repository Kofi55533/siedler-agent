#!/usr/bin/env bash
# incremental release build of s5verify (+s5check); quiet, log at $S5/build.log
source "$(dirname "$0")/s5-env.sh"
cd $S5_REPO/recomp && t0=$(date +%s)
cargo build --release -j "$(nproc)" -p s5verify > $S5/build.log 2>&1; rc=$?
echo "s5-build exit=$rc secs=$(( $(date +%s)-t0 ))"; exit $rc
