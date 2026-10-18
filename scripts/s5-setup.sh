#!/usr/bin/env bash
# Quiet setup of game data, toolchain and repo at fixed paths. Usage: s5-setup.sh <sim-rs-ref> [with-build-cache 0|1]
# Needs env T (PAT). Prints only sizes/timings, never file contents.
set -euo pipefail
REF=$1; CACHE=${2:-1}
D=$(dirname "$0"); source "$D/s5-env.sh"
t0=$(date +%s)
sudo rm -rf /usr/share/dotnet /usr/local/lib/android /opt/ghc /opt/hostedtoolcache/CodeQL /usr/local/share/boost >/dev/null 2>&1 || true
sudo mkdir -p $S5 && sudo chown "$(id -u):$(id -g)" $S5
(sudo apt-get -qq update && sudo DEBIAN_FRONTEND=noninteractive apt-get -qq install -y zstd mesa-vulkan-drivers vulkan-tools) >/dev/null 2>&1 &
APT=$!
rustup toolchain install 1.99.0 --profile minimal >/dev/null 2>&1 &
RUST=$!
# Keep credentials out of .git/config: model processes can inspect their checkout.
export GH_TOKEN=$T
ASKPASS=$(mktemp)
chmod 700 "$ASKPASS"
cat > "$ASKPASS" <<'ASK'
#!/bin/sh
case "$1" in *Username*) printf '%s\n' x-access-token ;; *) printf '%s\n' "$GH_TOKEN" ;; esac
ASK
export GIT_ASKPASS=$ASKPASS GIT_TERMINAL_PROMPT=0
trap 'rm -f "$ASKPASS"' EXIT
git clone -q --depth 1 --no-single-branch "https://github.com/Kofi55533/siedler-sim-rs.git" $S5_REPO
wait $APT
mkdir -p $S5/dl $S5/data/game $S5/data/art $S5/data/fixtures
export GH_TOKEN=$T
td=$(date +%s)
# opus-extra is not used by this setup; avoid downloading 2.55 GB per worker.
gh release download data-v2 -R Kofi55533/siedler-data -D $S5/dl \
  -p 'game.tzst.*' -p art.tzst -p fixtures.tzst -p gen.tzst -p newfix.tzst >/dev/null
cat $S5/dl/game.tzst.* | zstd -dcq | tar -xf - -C $S5/data/game & P1=$!
zstd -dcq $S5/dl/art.tzst | tar -xf - -C $S5/data/art & P2=$!
zstd -dcq $S5/dl/fixtures.tzst | tar -xf - -C $S5/data/fixtures & P3=$!
zstd -dcq $S5/dl/gen.tzst | tar -xf - -C $S5_REPO/recomp & P4=$!
mkdir -p $S5_NF && zstd -dcq $S5/dl/newfix.tzst | tar -xf - -C $S5_NF & P5=$!
wait $P1 $P2 $P3 $P4 $P5
# box-absolute image paths in the extra fixtures -> runner paths
sed -i -e "s#/workspace/siedler/artifacts/new-fixtures#$S5_NF#g" -e "s#/workspace/siedler/artifacts/codex-verify-infra#$S5_V#g" $S5_NF/fixtures/*/scenario.json
rm -f $S5/dl/*.tzst*
echo "s5-setup: data-v2 download+extract secs=$(( $(date +%s)-td ))"
cp $S5_ARTIFACTS/lift/gen.closure.pkl $S5_REPO/recomp/ 2>/dev/null || true
(python3.11 -m venv $S5/py311 2>/dev/null || python3 -m venv $S5/py311) && $S5/py311/bin/pip -q install unicorn==2.1.4 >/dev/null 2>&1
wait $RUST 2>/dev/null || true
CSHA=""
if [ "$CACHE" = 1 ] && gh release view build-cache -R Kofi55533/siedler-data >/dev/null 2>&1; then
  gh release download build-cache -R Kofi55533/siedler-data -D $S5/dl >/dev/null
  CSHA=$(cat $S5/dl/cache-sha.txt)
  git -C $S5_REPO fetch -q --depth 1 origin "$CSHA" && git -C $S5_REPO checkout -q "$CSHA"
  cat $S5/dl/target.tzst.* | zstd -dcq | tar -xf - -C /
  rm -f $S5/dl/target.tzst.*
fi
# old mtimes for everything = cache-consistent; later checkouts give changed files a fresh mtime
find $S5_REPO -path $S5_REPO/.git -prune -o -path $S5_REPO/recomp/target -prune -o -type f -print0 | xargs -0 touch -h -d '2000-01-01 00:00:00'
git -C $S5_REPO checkout -q "$REF"
echo "s5-setup: ref=$REF cache=${CSHA:0:8} secs=$(( $(date +%s)-t0 )) disk_free=$(df -h / | awk 'NR==2{print $4}')"
