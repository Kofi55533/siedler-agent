#!/usr/bin/env bash
# s5-mapgen.sh <out:pack:kind:subpath:catalogue> ...
# Captures a map-start image (S5IMG003, turn ~25, no commands) for each token with the recomp fast port and uploads
# <out>.tzst (fixtures/<out>/{scenario.json,commands.jsonl} + images/<out>/start.img{,.state.json}) to the PRIVATE release
# Kofi55533/siedler-data tag "mapimg". Recipe: docs/clean/new-fixtures/MAPSTART_CAPTURE.md (+ 08_unexploredland.md).
#   kind: mp  -> Framework.StartMap(name, 2, nil)      sp -> (name, 0, nil)      camp -> (name, -1, <catalogue>)
#   pack: base|extra1|extra2 (extra1 maps are copied into the extra2 pack with <Key>2</Key>: the gold exe mounts extra2 only)
# Needs: s5-setup.sh done (S5_GAME/S5_ARTIFACTS/recomp gen), gold install at $GOLD, GH_TOKEN. Prints only names/numbers.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/s5-env.sh"
GOLD=${GOLD:-$S5/goldfull}; R=$S5_REPO/recomp; export WGPU_BACKEND=vulkan
mkdir -p /tmp/mg/up
for tok in "$@"; do
  IFS=: read -r out pack kind sub cat <<<"$tok"
  name=${sub##*/}; W=/tmp/mg/$out; rm -rf "$W"; mkdir -p "$W/overlay" "$W/png"; cp -r "$S5_ARTIFACTS/boot/user" "$W/user"
  log=$W/capture.log
  if [ ! -d "$GOLD/$pack/shr/maps/$sub" ] && [ ! -d "$S5_GAME/$pack/shr/maps/$sub" ] && [ "$pack" != base ]; then echo "$out: FAILED no map dir; have: $(ls "$GOLD/$pack/shr/maps/$(dirname "$sub")" 2>&1 | head -30 | tr '\n' ' ')"; continue; fi
  case $kind in
    mp) args="2, nil"; rel=$sub ;;
    sp) args="0, nil"; rel=$sub ;;
    camp) args="-1, \"$cat\""; rel=$sub ;;
  esac
  if [ "$pack" = extra1 ]; then   # relocate into the mounted extra2 pack, Key 2
    case $kind in camp) dst=Campaign/Extra2_1/$name ;; *) dst=$rel ;; esac
    mkdir -p "$W/overlay/extra2/shr/maps/$(dirname "$dst")"
    cp -r "$( [ -d "$GOLD/extra1/shr/maps/$sub" ] && echo $GOLD || echo $S5_GAME )/extra1/shr/maps/$sub" "$W/overlay/extra2/shr/maps/$dst"
    python3 - "$W/overlay/extra2/shr/maps/$dst/Info.xml" <<'PY'
import re, sys
p = sys.argv[1]; t = open(p, encoding="utf-8-sig").read()
t = re.sub(r"<Key>\d+</Key>", "<Key>2</Key>", t) if "<Key>" in t else t.replace("</root>", " <Key>2</Key>\n</root>")
open(p, "w", encoding="utf-8").write(t)
PY
  fi
  python3 - "$W/inject.json" "$name" "$args" <<'PY'
import json, sys
out, name, args = sys.argv[1:4]
close = ("if Cutscene_INTRO_Finished then Cutscene_INTRO_Finished() end; "
         "if briefingState and Action_Briefing then briefingState.page=99; Action_Briefing() end; "
         "return tostring(briefingState and briefingState.page)")
chunks = [{"code": f'Framework.StartMap("{name}", {args})', "tag": "startmap", "result": False},
          {"code": close, "tag": "close", "state": "XGlobalScript", "at": 0, "result": True}]
json.dump(chunks, open(out, "w"))
PY
  ( cd "$R" && BOOT=$GOLD,$S5_ARTIFACTS/boot/runtime,$W/overlay,$W/user INJECT=$W/inject.json STOP_TURN=25 \
      DUMP_MEM=$W/start.mem PNG_EVERY=100000 timeout 900 ./target/release/s5render - 2000 "$W/png" ) > "$log" 2>&1; rc=$?
  if [ $rc != 0 ] || [ ! -s "$W/start.mem" ]; then
    echo "$out: FAILED s5render rc=$rc $(grep -m1 -oE 'MessageBoxA: .{0,100}|panicked at .{0,80}' "$log" | tr -d '\r')"
    cp "$log" /tmp/mg/up/mapgen-$out.log; continue; fi
  python3 "$R/mem2img.py" "$W/start.mem" "$S5_ARTIFACTS/bc/gs_h.img" "$W/start.img" >> "$log" 2>&1 || { echo "$out: FAILED mem2img"; continue; }
  rm -f "$W/start.mem"
  ck=$( cd "$R" && : > "$W/none.jsonl" && ./target/release/s5verify checksum "$W/start.img" "$W/none.jsonl" 0 2>>"$log" )
  turn=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['turn'])" "$ck" 2>/dev/null)
  if [ -z "$turn" ]; then echo "$out: FAILED checksum"; cp "$log" /tmp/mg/up/mapgen-$out.log; continue; fi
  T=$W/pk; rm -rf "$T"; mkdir -p "$T/fixtures/$out" "$T/images/$out"
  : > "$T/fixtures/$out/commands.jsonl"
  python3 - "$T/fixtures/$out/scenario.json" "$out" "$turn" <<'PY'
import json, sys
p, out, turn = sys.argv[1:4]
json.dump({"name": out, "image": f"/opt/s5/data/newfix/images/{out}/start.img", "start_turn": int(turn), "turns": 1200,
           "commands": "commands.jsonl", "detail_every": 20, "wide_every": 200, "wide_v": 1}, open(p, "w"), indent=2)
open(p, "a").write("\n")
PY
  mv "$W/start.img" "$W/start.img.state.json" "$T/images/$out/"
  tar -C "$T" -cf - fixtures images | zstd -q -T0 -3 -o /tmp/mg/up/$out.tzst
  cp "$log" /tmp/mg/up/mapgen-$out.log
  echo "$out: captured turn $turn size $(stat -c %s /tmp/mg/up/$out.tzst)"
  rm -rf "$W"
done
rel=mapimg; DR=Kofi55533/siedler-data
gh release view $rel -R $DR >/dev/null 2>&1 || gh release create $rel -R $DR --title $rel --notes "map-start images (private)" >/dev/null 2>&1 || true
for f in /tmp/mg/up/*; do [ -e "$f" ] || continue
  ok=0; for i in 1 2 3; do gh release upload $rel -R $DR --clobber "$f" >/dev/null 2>&1 && { ok=1; break; }; sleep 10; done
  [ $ok = 1 ] || echo "upload failed: $(basename "$f" | sed 's/\..*//')"
done
echo "uploaded $(ls /tmp/mg/up | wc -l) files to $rel"
