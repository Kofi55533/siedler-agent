#!/usr/bin/env bash
# Publish recomp/target, recomp/gen and the cargo registry of the current $S5_REPO checkout as the private release
# build-cache of siedler-data (read by s5-setup.sh). Needs GH_TOKEN. Prints sizes and short shas only.
# Every unit that ever wrote recomp/target (s5verify by s5-build.sh, the capture tests by xc_build) must have been
# built at this checkout: s5-setup.sh dates all sources back, so a unit left from an older commit would pass as fresh.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/s5-env.sh"
R=Kofi55533/siedler-data
SHA=$(git -C $S5_REPO rev-parse HEAD)
[ -z "$(git -C $S5_REPO status --porcelain --untracked-files=no)" ] || { echo "build cache: checkout has local changes, not uploaded"; exit 1; }
W=$(mktemp -d); cd "$W"; echo "$SHA" > cache-sha.txt; P=target-${SHA:0:12}.tzst.
find $S5_REPO/recomp/target/release -maxdepth 1 -type f -perm -u+x ! -name s5verify ! -name s5check -delete
rm -rf $S5_REPO/recomp/target/verify
tar -cf - -P $S5_REPO/recomp/target $S5_REPO/recomp/gen $HOME/.cargo/registry | zstd -q -T0 -3 | split -b 1900M - "$P"
echo "build cache bytes $(cat "$P"* | wc -c) at ${SHA:0:8}"
# the parts carry the commit in their name and cache-sha.txt, uploaded last, switches s5-setup.sh over: a setup
# meanwhile still gets the whole previous cache, which stays one generation; older parts go
gh release view build-cache -R $R >/dev/null 2>&1 || gh release create build-cache -R $R -t build-cache -n "target cache" >/dev/null
prev=$(gh api repos/$R/releases/tags/build-cache --jq '.body' | grep -o '[0-9a-f]\{40\}' | head -1 || true)
gh release upload build-cache -R $R --clobber "$P"* >/dev/null
gh release upload build-cache -R $R --clobber cache-sha.txt >/dev/null
gh release edit build-cache -R $R --notes "target cache @ $SHA" >/dev/null
for a in $(gh api repos/$R/releases/tags/build-cache --jq '.assets[].name'); do
  case "$a" in cache-sha.txt|"$P"*|target-${prev:0:12}.tzst.*) ;; *) gh release delete-asset build-cache -R $R -y "$a" >/dev/null ;; esac
done
cd /; rm -rf "$W"; echo "build cache uploaded"
