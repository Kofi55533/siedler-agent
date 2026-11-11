#!/usr/bin/env bash
# import.sh <kind> <head sha>: one import of the own-repo pool (xc-import.yml, main account, main token): the pool's
# passed change for <kind> (the one commit of branch xc/<kind> in XC_OWN_RESULTS, see README.md) is applied to the main
# project's clean/integration and handed to the unchanged scripts/xc/worker.sh as its model's change: worker.sh measures
# the base, runs verify.sh, and only on PASS commits, opens the PR and squash-merges, as for any worker.
# Exit codes as worker.sh: 0 merged or nothing to do, 1 attempt recorded, 2 infrastructure; 3 skipped (the branch moved,
# its change is out of scope or does not apply to the current base: no attempt recorded, the claim is released).
# Needs: the runner layout of scripts/s5-setup.sh plus what worker.sh needs except opencode; GH_TOKEN of the main account
# (worker.sh writes the main project as always); XC_IMPORT_FETCH_TOKEN, if set, reads XC_OWN_RESULTS instead of GH_TOKEN.
set -uo pipefail
KIND=${1:?kind}; WANT=${2:?head sha}
D=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); XCD=$D/../xc
source "$XCD/lib.sh"
RES=${XC_OWN_RESULTS:?XC_OWN_RESULTS unset}; PROJECT=${XC_PROJECT:-Kofi55533/siedler-sim-rs}
[ "${RES%%/*}" != "${PROJECT%%/*}" ] || { echo "XC_OWN_RESULTS must be the pool account's repo"; exit 2; }
BR=$(python3 "$XCD/xc.py" branch "$KIND")
P=${XC_IMPORT_DIR:-${RUNNER_TEMP:-/tmp}}/xc-import-$KIND.patch
skip(){ echo "import $KIND: $1 (skipped, no attempt)"; python3 "$XCD/xc.py" issue "$KIND" unclaim; exit 3; }
url(){ echo "https://x-access-token:${GH_TOKEN}@github.com/$1.git"; }
# the results repo shares no commit with this checkout: a blob-less fetch (commits and trees of two snapshots), the
# changed files' blobs come with the diff. Its URL lands in .git/config as promisor remote, so the token goes in a
# header for these calls only (the model's stand-in runs in this checkout later)
rgit(){ git -c "http.https://github.com/.extraheader=AUTHORIZATION: basic $(printf 'x-access-token:%s' "${XC_IMPORT_FETCH_TOKEN:-$GH_TOKEN}" | base64 -w0)" "$@"; }
cd "$S5_REPO" || exit 2
# the pool's branch head and its parent (the snapshot of the main project's clean/integration it was verified on)
rgit fetch -q --depth 2 --filter=blob:none "https://github.com/$RES.git" "+refs/heads/$BR:refs/xc-own/head" || exit 2
[ "$(git rev-parse refs/xc-own/head)" = "$WANT" ] || skip "branch $BR moved since the discovery"
[ "$(git rev-list --count refs/xc-own/head~1..refs/xc-own/head)" = 1 ] || skip "branch $BR is not one commit"
bad=$(git diff --name-only refs/xc-own/head~1 refs/xc-own/head | grep -vE '^(crates/s5clean/|docs/|crates/s5xcheck/src/functions/)' | head -3 | xargs)
[ -z "$bad" ] || skip "changes outside the worker scope: $bad"
# shellcheck disable=SC2046  # xc_paths lists pathspecs, one per word
rgit diff --binary --full-index refs/xc-own/head~1 refs/xc-own/head -- $(xc_paths "$KIND") > "$P" || exit 2
[ -s "$P" ] || skip "empty change"
rm -f "$P.applied"
# it has to apply to the current base (a throwaway index; worker.sh checks out the same base right after)
git fetch -q "$(url "$PROJECT")" "+refs/heads/clean/integration:refs/remotes/origin/clean/integration" || exit 2
idx=$(mktemp); trap 'rm -f "$idx"' EXIT
GIT_INDEX_FILE=$idx git read-tree origin/clean/integration \
  && GIT_INDEX_FILE=$idx git apply --cached --3way --whitespace=nowarn "$P" > /dev/null 2>&1 \
  && ! GIT_INDEX_FILE=$idx git ls-files -u | grep -q . || skip "the change does not apply to clean/integration $(git rev-parse --short origin/clean/integration)"
echo "import $KIND: $(git diff --shortstat refs/xc-own/head~1 refs/xc-own/head) from $RES $BR ${WANT:0:12}"
export PATH="$D:$PATH" XC_IMPORT_PATCH=$P
# one round: the stand-in applies the change, verify.sh judges it; no bundle (the pool's change is for <kind> alone)
XC_ROUNDS=1 XC_BUNDLE=1 "${XC_WORKER:-$XCD/worker.sh}" "$KIND"
