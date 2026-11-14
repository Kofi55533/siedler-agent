#!/usr/bin/env bash
# s5-cover-upload.sh <dir> <fixture ...>: uploads the executed-block bitmaps s5-live.sh wrote with S5_COVER_DIR=<dir>
# (<dir>/<fixture>.cover.gz) to the private siedler-data release rm-<ref> as cover-[<tag>]<fixture>.gz, the release
# and naming of the remeasure's live-*.txt details. Downloaded, siedler-sim-rs `tools/mechanics_coverage.py report
# --cover <dir>` turns them into docs/clean/MECHANICS.md. Prints counts only (this repository is public): never
# fixture names, addresses or files.
set -uo pipefail
dir=$1; shift
ref=$(grep -v '^#' remeasure-ref.txt 2>/dev/null | grep -m1 . || echo clean/integration); tag=""
[ "$ref" = clean/integration ] || tag=$(echo "$ref" | tr '/' '-')-
up=$(mktemp -d); n=0; miss=0
for x in "$@"; do
  f=$dir/$x.cover.gz
  # a bitmap starts with the gzip magic; anything else (an interrupted write) is left out
  if [ -s "$f" ] && [ "$(head -c2 "$f" | od -An -tx1 | tr -d ' ')" = 1f8b ]; then
    cp "$f" "$up/cover-$tag$x.gz"; n=$((n+1))
  else miss=$((miss+1)); fi
done
echo "coverage bitmaps: $n of $(( n + miss ))"
[ $n -gt 0 ] || exit 0
rel=rm-$(echo "$ref" | tr '/' '-'); D=Kofi55533/siedler-data
gh release view $rel -R $D > /dev/null 2>&1 || gh release create $rel -R $D --title $rel --notes "remeasure details for $ref" > /dev/null 2>&1 || true
ok=0; for i in 1 2 3; do gh release upload $rel -R $D --clobber "$up"/cover-*.gz > /dev/null 2>&1 && { ok=1; break; }; sleep 10; done
rm -rf "$up"
[ $ok = 1 ] && echo "coverage: uploaded $n" || echo "coverage: upload failed ($n files)"
