#!/usr/bin/env bash
# Publish the original-start inputs for the Clean-only BC Full replay.
set -euo pipefail
repo=Kofi55533/siedler-sim-rs
tag=observation-bc-full-8d4a7e5aedb25f125346d22dc2315556847368f7
names=$(gh release view "$tag" -R "$repo" --json assets --jq '.assets[].name')
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
if ! grep -Fxq 'bc-full-expected.txt' <<<"$names"; then
  gh api -H 'Accept: application/vnd.github.raw+json' \
    "repos/$repo/contents/results/bc-full-observation-43234018862-cache.txt?ref=codex/replay-observation-results" |
    sed -n '/^bc-full: DIVERGENT turn 6842: tasks\.hurt_guard digest: /p' > "$tmp/bc-full-expected.txt"
  [ "$(wc -l < "$tmp/bc-full-expected.txt")" -eq 1 ]
  gh release upload "$tag" -R "$repo" "$tmp/bc-full-expected.txt" >/dev/null
fi
if grep -Fxq 'bc-full-start.tar.zst' <<<"$names"; then
  echo "Private BC Full start bundle is available"
  exit 0
fi
mkdir -p "$tmp/art" "$tmp/fixtures" "$tmp/bundle"
gh release download data-v2 -R Kofi55533/siedler-data -p art.tzst -p fixtures.tzst -D "$tmp"
tar --zstd -xf "$tmp/art.tzst" -C "$tmp/art"
tar --zstd -xf "$tmp/fixtures.tzst" -C "$tmp/fixtures"
cp "$tmp/art/bc/gs_h.img" "$tmp/bundle/start.img"
python3 - "$tmp" <<'PY'
import json
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1])
fixture = root / "fixtures" / "codex-verify-infra" / "fixtures" / "bc-full" / "scenario.json"
scenario = json.loads(fixture.read_text(encoding="utf-8"))
if (scenario["name"], scenario["start_turn"], scenario["turns"]) != ("bc-full", 121, 23900):
    raise ValueError("Unexpected BC Full scenario")
shutil.copyfile(fixture.parent / scenario["commands"], root / "bundle" / "commands.jsonl")
scenario["commands"] = "commands.jsonl"
scenario["image"] = "start.img"
scenario.pop("start_state", None)
(root / "bundle" / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")
PY
tar --zstd -cf "$tmp/bc-full-start.tar.zst" -C "$tmp/bundle" scenario.json start.img commands.jsonl
gh release upload "$tag" -R "$repo" "$tmp/bc-full-start.tar.zst" >/dev/null
echo "Private BC Full start bundle published"
