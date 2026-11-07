#!/usr/bin/env bash
# drive.sh <scenario>: drive mode of the direct-call check (crates/s5xcheck tests/drive-functions.rs) on one scenario of
# the current $S5_REPO checkout: every virtual method with live objects at the turns of $S5_DIRECT_AT is called natively
# on a few of them, and every class-A function those calls reach is counted (the census inventory, xc_inventory), the
# bound ones compared. A method that never returns makes the test end itself (exit 75) and name it in its state
# directory; the test is started again without it until it finishes, $XC_DRIVE_RESTARTS (default 300) restarts or
# $XC_DRIVE_TIMEOUT seconds (default 9000) are spent. The partial reports of all starts are merged into $XC_DRIVE_OUT
# (default $XC/drive.json, census format plus "skipped" and "hung"). Builds the test unless XC_DRIVE_BUILT=1.
# Prints counts only; the JSON stays under $XC (private).
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/lib.sh"
OUT=${XC_DRIVE_OUT:-$XC/drive.json}; DIR=$XC/drive-$1
t0=$(date +%s)
if [ "${XC_DRIVE_BUILT:-}" != 1 ]; then
( cd "$S5_REPO" || exit 1
  [ -f crates/s5xcheck/Cargo.lock ] || cp recomp/Cargo.lock crates/s5xcheck/Cargo.lock
  CARGO_TARGET_DIR=${XC_TARGET:-$S5_REPO/recomp/target} cargo test --release --no-run -j "${CARGO_BUILD_JOBS:-3}" \
    --manifest-path crates/s5xcheck/Cargo.toml --test drive-functions --message-format=json 2> "$XC/build.log" \
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
echo "build secs=$(( $(date +%s) - t0 ))"
fi
xc_inventory || exit 2
read -r name img skip turns < <(xc_scenarios | awk -v n="$1" '$1==n')
[ -n "${img:-}" ] || { echo "no scenario $1"; exit 2; }
cmds=; xc_cmds; [ -s "$XC/cmds/$name.bin" ] && cmds=$XC/cmds/$name.bin
rm -rf "$DIR"; mkdir -p "$DIR"; : > "$DIR/hung.txt"
t=$(date +%s); starts=0; rc=0
while :; do
  left=$(( ${XC_DRIVE_TIMEOUT:-9000} - ($(date +%s) - t) ))
  [ "$left" -ge 60 ] && [ "$starts" -le "${XC_DRIVE_RESTARTS:-300}" ] || { echo "drive: budget spent"; break; }
  starts=$((starts + 1))
  ( cd "$S5_REPO" && env S5_XCHECK_IMAGE="$img" ${cmds:+S5_XCHECK_COMMANDS="$cmds"} \
      S5_DIRECT_AT="${S5_DIRECT_AT:-$skip,$((skip + 100))}" S5_CAPTURE_FUNCTIONS="$XC/inventory.txt" S5_DRIVE_DIR="$DIR" \
      timeout -k 30 "$left" "$(xc_bin drive-functions)" --nocapture --test-threads=1 ) < /dev/null >> "$DIR/log" 2>&1
  rc=$?
  [ $rc = 75 ] && [ -f "$DIR/hung" ] || break
  cat "$DIR/hung" >> "$DIR/hung.txt"
done
echo "drive: exit $rc, $starts starts, $(wc -l < "$DIR/hung.txt") methods hung, secs=$(( $(date +%s) - t))"
# panics name a source line of sim-rs, never game state
grep -E "^thread '.*' panicked at|^error|Killed|memory allocation" "$DIR/log" | sed 's/:.*panicked at /: panicked at /' | sort | uniq -c | head -5
grep -E "^drive attempt" "$DIR/log" | tail -1
python3 - "$DIR" "$OUT" <<'P'
import json, sys
from pathlib import Path
d, out = Path(sys.argv[1]), Path(sys.argv[2])
merged = {"groups": {}, "skipped": {}, "import_errors": [], "entries": 0, "starts": 0,
          "hung": [l.strip() for l in (d / "hung.txt").read_text().splitlines() if l.strip()]}
for p in sorted(d.glob("report-*.json")):
    try:
        r = json.loads(p.read_text())
    except ValueError:
        continue  # cut off while being written: its methods were not marked done, the next start repeated them
    merged["starts"] += 1
    merged["function"] = r.get("function", "")
    merged["entries"] += r.get("entries", 0)
    merged["import_errors"] += r.get("import_errors", [])
    for k, v in r.get("skipped", {}).items():
        merged["skipped"].setdefault(k, []).extend(v)
    for k, g in r.get("groups", {}).items():
        m = merged["groups"].setdefault(k, {"entries": 0, "samples": 0, "failing": 0, "examples": []})
        for f in ("entries", "samples", "failing"):
            m[f] += g.get(f, 0)
        m["examples"] = (m["examples"] + g.get("examples", []))[:3]
        if "observed" in g:
            m["observed"] = m.get("observed", 0) + g["observed"]
        if g.get("unbound"):
            m["unbound"] = True
        for c, n in g.get("callers", {}).items():
            m.setdefault("callers", {})[c] = m.get("callers", {}).get(c, 0) + n
out.write_text(json.dumps(merged))
g = merged["groups"]
bound = [x for x in g.values() if not x.get("unbound")]
print(f"reports {merged['starts']}, functions reached {len(g)}: counted only {len(g) - len(bound)}, bound {len(bound)} "
      f"(identical {sum(1 for x in bound if x['samples'] and not x['failing'])}, differ {sum(1 for x in bound if x['failing'])}, "
      f"no sample {sum(1 for x in bound if not x['samples'])}); skipped calls {sum(map(len, merged['skipped'].values()))}, "
      f"import errors {len(merged['import_errors'])}")
P
