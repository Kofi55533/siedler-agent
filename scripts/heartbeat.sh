#!/usr/bin/env bash
# heartbeat.sh fetch <out.json>   -> prints the lane-claims.py argument: "--heartbeat <out.json>" (release readable; a missing
#                                     asset is written as [] = no owner active) or "--heartbeat-unknown" (GitHub unreachable:
#                                     fail-safe, claims keep holding as before).
# heartbeat.sh put <owner> <state> [until-iso]   (box loop) upload {"owner","state","updated_utc","until"} as <owner>-heartbeat.json
#   states: active (loop iteration running) | blocked (usage limit) | waiting (retry pause) | stopped
# Release `heartbeat` on Kofi55533/siedler-agent (public: the file holds only owner, state and times).
set -uo pipefail
R=Kofi55533/siedler-agent; REL=heartbeat
case "${1:-}" in
  fetch)
    out=$2
    names=$(gh release view $REL -R $R --json assets -q '.assets[].name' 2>/dev/null) || {
      gh api "repos/$R/releases/tags/$REL" >/dev/null 2>&1; [ $? = 1 ] && gh api "repos/$R" >/dev/null 2>&1 && { echo '[]' > "$out"; echo "--heartbeat $out"; exit 0; }
      echo "--heartbeat-unknown"; exit 0; }
    tmp=$(mktemp -d); echo '[' > "$out"; sep=""
    for n in $(grep -E -- '-heartbeat\.json$' <<<"$names"); do
      if gh release download $REL -R $R -p "$n" -D "$tmp" --clobber >/dev/null 2>&1; then printf '%s' "$sep" >> "$out"; cat "$tmp/$n" >> "$out"; sep=","
      else rm -rf "$tmp"; echo "--heartbeat-unknown"; exit 0; fi
    done
    echo ']' >> "$out"; rm -rf "$tmp"
    python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$out" 2>/dev/null && echo "--heartbeat $out" || echo "--heartbeat-unknown" ;;
  put)
    owner=$2 state=$3 until=${4:-}; f=$(mktemp -d)/$(tr 'A-Z' 'a-z' <<<"$owner")-heartbeat.json
    printf '{"owner":"%s","state":"%s","updated_utc":"%s","until":"%s","host":"box"}\n' "$owner" "$state" "$(date -u +%FT%TZ)" "$until" > "$f"
    gh release view $REL -R $R >/dev/null 2>&1 || gh release create $REL -R $R --target main --prerelease -t $REL \
      -n "Agent loop heartbeats (owner, state, time). A Codex LANE-CLAIM holds PRs only while codex-heartbeat.json says active and is < 25 min old (scripts/lane-claims.py)." >/dev/null 2>&1
    gh release upload $REL -R $R --clobber "$f" >/dev/null 2>&1; rc=$?; rm -rf "$(dirname "$f")"; exit $rc ;;
  *) echo "usage: heartbeat.sh fetch <out> | put <owner> <state> [until]" >&2; exit 2 ;;
esac
