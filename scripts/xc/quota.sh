#!/usr/bin/env bash
# quota.sh <model>: exit 1 while the free model answers FreeUsageLimitError (HTTP 429), else 0. On 2026-11-07 every
# free model refused that way from about 05:30 UTC, with and without the key, and every session after it hung silently
# for its whole round; a one-token request tells it in a fraction of a second. Prints nothing (the logs are public).
m=${1#opencode/}
key=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.local/share/opencode/auth.json")))["opencode"]["key"])' 2>/dev/null)
h=(-H 'content-type: application/json'); [ -n "$key" ] && h+=(-H "authorization: Bearer $key")
body=$(curl -sS -m 60 https://opencode.ai/zen/v1/chat/completions "${h[@]}" \
  -d "{\"model\":\"$m\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with OK.\"}],\"max_tokens\":1}" 2>/dev/null)
! grep -q FreeUsageLimitError <<<"$body"
