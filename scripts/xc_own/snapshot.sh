#!/usr/bin/env bash
# snapshot.sh: point clean/integration of the pool's results repo (XC_OWN_RESULTS) at the tree of the main project's
# clean/integration, read-only from the main project. The job's checkout is shallow, and a shallow history cannot be
# pushed into another repo, so the results repo gets a snapshot commit instead: the main tree, its parent the results
# repo's previous clean/integration (a fast-forward, no force), the main commit named in the message. worker.sh then
# measures and works on exactly the main project's code, and every result branch xc/<kind> is one commit on it.
# Needs GH_TOKEN of the pool account (reads the main project, writes XC_OWN_RESULTS only) and the s5-setup.sh checkout.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../s5-env.sh"
MAIN=${XC_MAIN_PROJECT:-Kofi55533/siedler-sim-rs}; RES=${XC_OWN_RESULTS:?XC_OWN_RESULTS unset}
[ "${RES%%/*}" != "${MAIN%%/*}" ] || { echo "XC_OWN_RESULTS must be a repo of the pool's own account"; exit 2; }
# the token goes in a header for this call only: the blob-less fetch records its URL in .git/config (promisor remote),
# and the checkout is the model's working directory later
url(){ echo "https://github.com/$1.git"; }
git(){ command git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)" "$@"; }
cd "${XC_SNAPSHOT_REPO:-$S5_REPO}"
git fetch -q --depth 1 "$(url "$MAIN")" "+refs/heads/clean/integration:refs/xc-own/main"
src=$(git rev-parse refs/xc-own/main); tree=$(git rev-parse "refs/xc-own/main^{tree}")
for try in 1 2 3; do
  parent=$(git ls-remote "$(url "$RES")" refs/heads/clean/integration | cut -f1)
  if [ -n "$parent" ]; then
    # the previous snapshot's commit and trees only; its blobs are mostly the main tree's, already here
    git fetch -q --depth 1 --filter=blob:none "$(url "$RES")" "+refs/heads/clean/integration:refs/xc-own/results"
    [ "$(git rev-parse "$parent^{tree}")" = "$tree" ] && { git update-ref refs/remotes/origin/clean/integration "$parent"; echo "snapshot: $RES clean/integration is ${src:0:12} already"; exit 0; }
  fi
  c=$(GIT_AUTHOR_NAME=siedler-agent GIT_AUTHOR_EMAIL=snapshot@xc-own.invalid GIT_COMMITTER_NAME=siedler-agent \
      GIT_COMMITTER_EMAIL=snapshot@xc-own.invalid GIT_AUTHOR_DATE="$(git log -1 --format=%cI "$src")" \
      GIT_COMMITTER_DATE="$(git log -1 --format=%cI "$src")" \
      git commit-tree "$tree" ${parent:+-p "$parent"} -m "snapshot of $MAIN clean/integration $src")
  if git push -q "$(url "$RES")" "$c:refs/heads/clean/integration"; then
    git update-ref refs/remotes/origin/clean/integration "$c"
    echo "snapshot: $RES clean/integration = ${src:0:12} (${c:0:12})"; exit 0
  fi
  sleep $((try * 5))   # another job of the pool pushed meanwhile
done
echo "snapshot: push to $RES refused 3 times"; exit 1
