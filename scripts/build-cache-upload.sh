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
W=$(mktemp -d); cd "$W"; echo "$SHA" > cache-sha.txt
find $S5_REPO/recomp/target/release -maxdepth 1 -type f -perm -u+x ! -name s5verify ! -name s5check -delete
rm -rf $S5_REPO/recomp/target/verify
tar -cf - -P $S5_REPO/recomp/target $S5_REPO/recomp/gen $HOME/.cargo/registry | zstd -q -T0 -3 | split -b 1900M - target.tzst.
echo "build cache bytes $(cat target.tzst.* | wc -c) at ${SHA:0:8}"
# upload under a side tag, then swap it in: a setup downloading meanwhile sees the old cache or, for a second, none
N=build-cache-next
gh release delete $N -R $R -y --cleanup-tag >/dev/null 2>&1 || true
gh release create $N -R $R -t build-cache -n "target cache @ $SHA" cache-sha.txt target.tzst.* >/dev/null
id=$(gh api repos/$R/releases/tags/$N --jq .id)
gh release delete build-cache -R $R -y --cleanup-tag >/dev/null 2>&1 || true
gh api -X PATCH repos/$R/releases/$id -f tag_name=build-cache >/dev/null
gh api -X DELETE repos/$R/git/refs/tags/$N >/dev/null 2>&1 || true
cd /; rm -rf "$W"; echo "build cache uploaded"
