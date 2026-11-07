#!/usr/bin/env bash
# direct.sh <scenario>: the direct-call check (crates/s5xcheck tests/direct-functions.rs) on one scenario of the current
# $S5_REPO checkout: every bound virtual method (or those of $S5_CAPTURE_ONLY) called on a few live objects at the turn
# offsets of $S5_DIRECT_AT. Builds the test unless XC_DIRECT_BUILT=1 (xc_build built it into $XC/bins.txt).
# Writes the groups to $XC_DIRECT_OUT (default $XC/direct.json) and, unless XC_DIRECT_LIVE is empty, the live virtual
# methods to $XC_DIRECT_LIVE (default $XC/live.json). Prints counts only; the JSON stays under $XC (private).
# With S5_DRIVE_METHODS set it runs drive.sh instead (only those methods called, the functions below them compared).
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/lib.sh"
OUT=${XC_DIRECT_OUT:-$XC/direct.json}; LIVE=${XC_DIRECT_LIVE-$XC/live.json}
# S5_DRIVE_METHODS (FN_<address>,...): the function is not virtual, so drive mode calls the methods reaching it instead
if [ -n "${S5_DRIVE_METHODS:-}" ]; then
  XC_DRIVE_BUILT=${XC_DIRECT_BUILT:-} XC_DRIVE_OUT=$OUT XC_DRIVE_TIMEOUT=${XC_DRIVE_TIMEOUT:-${XC_DIRECT_TIMEOUT:-1800}} \
    exec "$D/drive.sh" "$1"
fi
t0=$(date +%s)
if [ "${XC_DIRECT_BUILT:-}" != 1 ]; then
( cd "$S5_REPO" || exit 1
  [ -f crates/s5xcheck/Cargo.lock ] || cp recomp/Cargo.lock crates/s5xcheck/Cargo.lock
  CARGO_TARGET_DIR=${XC_TARGET:-$S5_REPO/recomp/target} cargo test --release --no-run -j "${CARGO_BUILD_JOBS:-3}" \
    --manifest-path crates/s5xcheck/Cargo.toml --test direct-functions --message-format=json 2> "$XC/build.log" \
  | python3 -c 'import sys,json
err=open(sys.argv[1],"w")
for l in sys.stdin:
    d=json.loads(l)
    if d.get("reason")=="compiler-artifact" and d.get("executable"): print(d["target"]["name"], d["executable"])
    m=d.get("message") or {}
    if d.get("reason")=="compiler-message" and m.get("level")=="error":
        # the headline and the sim-rs source line only, no code
        sp=[s for s in m.get("spans",[]) if s.get("is_primary")][:1]
        at="".join(" at %s:%s" % (s.get("file_name"), s.get("line_start")) for s in sp)
        err.write(m.get("message","").splitlines()[0][:200]+at+"\n")' "$XC/errors.txt" > "$XC/bins.txt"
  rc=${PIPESTATUS[0]}; rm -f crates/s5xcheck/Cargo.lock; exit $rc ) || {
  echo "build failed ($(grep -c '^error' "$XC/build.log") errors)"
  head -20 "$XC/errors.txt"; exit 2; }
fi
[ "${XC_DIRECT_BUILT:-}" = 1 ] || echo "build secs=$(( $(date +%s) - t0 ))"
read -r name img skip turns < <(xc_scenarios | awk -v n="$1" '$1==n')
[ -n "${img:-}" ] || { echo "no scenario $1"; exit 2; }
cmds=; xc_cmds; [ -s "$XC/cmds/$name.bin" ] && cmds=$XC/cmds/$name.bin
t=$(date +%s)
( cd "$S5_REPO" && env S5_XCHECK_IMAGE="$img" ${cmds:+S5_XCHECK_COMMANDS="$cmds"} S5_DIRECT_AT="${S5_DIRECT_AT:-$skip,$((skip + 100))}" \
    S5_DIRECT_JSON="$OUT" ${LIVE:+S5_DIRECT_LIVE="$LIVE"} timeout -k 30 "${XC_DIRECT_TIMEOUT:-3600}" "$(xc_bin direct-functions)" \
    --nocapture --test-threads=1 ) < /dev/null > "$OUT.log" 2>&1
echo "direct: exit $? secs=$(( $(date +%s) - t)) json=$([ -s "$OUT" ] && echo yes || echo no)"
# panics name a source line of sim-rs, never game state
grep -E "^thread '.*' panicked at|^error|Killed|memory allocation" "$OUT.log" | sed 's/:.*panicked at /: panicked at /' | head -5
python3 - "$OUT" "$LIVE" <<'P'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.is_file() or not p.stat().st_size: sys.exit(0)
d = json.loads(p.read_text())
g = d.get("groups", {})
ok = sum(1 for x in g.values() if x.get("samples") and not x.get("failing"))
bad = sum(1 for x in g.values() if x.get("failing"))
none = sum(1 for x in g.values() if not x.get("samples"))
bound = {int(x, 16) for x in d.get("function", "").split(",") if x}
print(f"virtual bound: {len(bound)}, groups {len(g)}: identical {ok}, differ {bad}, no sample {none}")
print(f"samples {sum(x.get('samples', 0) for x in g.values())}, differing {sum(x.get('failing', 0) for x in g.values())}, "
      f"observed {sum(x.get('observed', 0) or 0 for x in g.values())}")
sk = d.get("skipped", {})
print(f"skipped calls {sum(len(v) for v in sk.values())} in {len(sk)} functions, not virtual {len(d.get('not_virtual', []))}, "
      f"import errors {len(d.get('import_errors', []))}")
live = Path(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else None
if live and live.is_file() and live.stat().st_size:
    L = json.loads(live.read_text())
    print(f"live virtual methods {len(L)}, of them bound {sum(1 for k in L if int(k[3:], 16) in bound)}, "
          f"objects {sum(v.get('objects', 0) for v in L.values())}")
P
