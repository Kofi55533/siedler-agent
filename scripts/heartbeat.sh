#!/usr/bin/env bash
# heartbeat.sh fetch <out.json>   -> prints the lane-claims.py argument: "--heartbeat <out.json>" (asset download URL readable, no API call; a missing
#                                     asset is written as [] = no owner active) or "--heartbeat-unknown" (GitHub unreachable:
#                                     fail-safe, claims keep holding as before).
# heartbeat.sh put <owner> <state> [until-iso]   (box loop) upload {"owner","state","updated_utc","until"} as <owner>-heartbeat.json
#   states: active (loop iteration running) | blocked (usage limit) | waiting (retry pause) | stopped
# Release `heartbeat` on Kofi55533/siedler-agent (public: the file holds only owner, state and times).
set -uo pipefail
R=Kofi55533/siedler-agent; REL=heartbeat
case "${1:-}" in
  fetch)
    # Read via the public release download URL (github.com/.../releases/download/...): no REST API call, so the
    # account's API rate limit (5000/h, exhausted 2026-10-20 13:00-13:26) cannot turn a heartbeat into "unknown".
    # 404 = no asset for that owner (= not active, as before); any other failure = unknown (fail-safe as before).
    out=$2; tmp=$(mktemp -d); echo '[' > "$out"; sep=""
    for o in $(tr ',' ' ' <<<"${HB_OWNERS:-Codex}"); do
      n=$(tr 'A-Z' 'a-z' <<<"$o")-heartbeat.json
      code=$(curl -sS -L -m 30 -o "$tmp/$n" -w '%{http_code}' "https://github.com/$R/releases/download/$REL/$n" 2>/dev/null)
      case "$code" in
        200) printf '%s' "$sep" >> "$out"; cat "$tmp/$n" >> "$out"; sep="," ;;
        404) ;;
        *) rm -rf "$tmp"; echo "--heartbeat-unknown"; exit 0 ;;
      esac
    done
    echo ']' >> "$out"; rm -rf "$tmp"
    python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$out" 2>/dev/null && echo "--heartbeat $out" || echo "--heartbeat-unknown" ;;
  put)
    owner=$2 state=$3 until=${4:-}; f=$(mktemp -d)/$(tr 'A-Z' 'a-z' <<<"$owner")-heartbeat.json
    printf '{"owner":"%s","state":"%s","updated_utc":"%s","until":"%s","host":"box"}\n' "$owner" "$state" "$(date -u +%FT%TZ)" "$until" > "$f"
    # upload first (the release exists); create it only if the upload failed and it is really missing (saves 1 API call/put)
    gh release upload $REL -R $R --clobber "$f" >/dev/null 2>&1; rc=$?
    if [ $rc != 0 ] && gh api "repos/$R/releases/tags/$REL" 2>&1 | grep -q '"status": *"404"\|HTTP 404'; then
      gh release create $REL -R $R --target main --prerelease -t $REL \
        -n "Agent loop heartbeats (owner, state, time). A Codex LANE-CLAIM holds PRs only while codex-heartbeat.json says active and is < 25 min old (scripts/lane-claims.py)." >/dev/null 2>&1 &&
      { gh release upload $REL -R $R --clobber "$f" >/dev/null 2>&1; rc=$?; }
    fi
    rm -rf "$(dirname "$f")"; exit $rc ;;
  *) echo "usage: heartbeat.sh fetch <out> | put <owner> <state> [until]" >&2; exit 2 ;;
esac
