#!/usr/bin/env bash
# probe_bc.sh: can later segments of the bc recording be census scenarios? Counts only: the recorded commands beside the
# bc image and in the bc-full fixture (number, turn range), then capture-functions on 1200 turns after several skips.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/lib.sh"
t0=$(date +%s); xc_build || exit 2; echo "build secs=$(( $(date +%s) - t0 ))"
img=$S5_ARTIFACTS/bc/gs_h.img
python3 - "$(dirname "$img")/commands.bin" "$S5_V/fixtures/bc-full" <<'P'
import json, os, struct, sys
p = sys.argv[1]
if os.path.isfile(p):
    b = open(p, "rb").read(); n = struct.unpack_from("<I", b, 8)[0]; i = 12; ts = []
    for _ in range(n):
        t, _e, w = struct.unpack_from("<III", b, i); i += 12; ts.append(t)
        for _ in range(w):
            _a, l = struct.unpack_from("<II", b, i); i += 8 + l
    print(f"commands.bin: {n} commands, turns {min(ts, default=0)}..{max(ts, default=0)}")
else:
    print("commands.bin: none beside the image")
d = sys.argv[2]
for f in ("scenario.json", "commands.jsonl"):
    print(f"bc-full {f}: {'yes' if os.path.isfile(os.path.join(d, f)) else 'no'}")
if os.path.isfile(os.path.join(d, "scenario.json")):
    s = json.load(open(os.path.join(d, "scenario.json"))); print(f"bc-full: start_turn {s.get('start_turn')} turns {s.get('turns')}")
if os.path.isfile(os.path.join(d, "commands.jsonl")):
    ts = [json.loads(l)["turn"] for l in open(os.path.join(d, "commands.jsonl"))]
    print(f"bc-full commands.jsonl: {len(ts)} commands, turns {min(ts)}..{max(ts)}")
P
seg(){ # seg <skip>
  local s=$1 t=$(date +%s)
  ( cd "$S5_REPO" && env S5_XCHECK_IMAGE="$img" S5_CAPTURE_SKIP="$s" S5_CAPTURE_TURNS=1200 S5_CAPTURE_MAX="$XC_MAX" \
      S5_CAPTURE_STRIDE="$XC_STRIDE" S5_CAPTURE_JSON="$XC/seg-$s.json" S5_CAPTURE_FUNCTIONS="$XC/inventory.txt" \
      timeout -k 30 3600 "$(xc_bin capture-functions)" --nocapture --test-threads=1 ) < /dev/null > "$XC/seg-$s.log" 2>&1
  local rc=$?
  python3 - "$XC/seg-$s.json" "$s" "$rc" "$(( $(date +%s) - t))" <<'P'
import json, os, sys
p, s, rc, secs = sys.argv[1:]
d = json.load(open(p)) if os.path.isfile(p) and os.path.getsize(p) else None
if d is None:
    print(f"skip {s}: exit {rc} secs={secs} no result"); sys.exit()
g = d.get("groups", {})
print(f"skip {s}: exit {rc} secs={secs} turns={d.get('turns')} entries={d.get('entries')} import_errors={len(d.get('import_errors', []))}"
      f" functions_called={sum(1 for x in g.values() if x.get('entries'))} failing={sum(1 for x in g.values() if x.get('failing'))}")
P
}
for s in 100 5000 12000 20000; do seg "$s" & done; wait
