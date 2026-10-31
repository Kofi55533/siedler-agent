#!/usr/bin/env bash
# attribute.sh <old-sha> <regressions.json>: after a census found (kind, scenario) pairs that compared clean on
# <old-sha> and differ on the current $S5_REPO checkout, find the xc merge in old..HEAD that broke each one and
# revert it (PR into clean/integration, squash-merged). A pair no xc merge explains (another lane's change) becomes
# an issue in the pipeline repo. Candidates are tried newest first: revert, rebuild, compare that kind in that
# scenario; the first revert that makes it clean again is the culprit. Prints counts and short shas only.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/lib.sh"
OLD=$1; REG=$2
PROJECT=${XC_PROJECT:-Kofi55533/siedler-sim-rs}; AGENT=${XC_AGENT:-Kofi55533/siedler-agent}
MAXC=${XC_ATTRIBUTE_MAX:-10}   # a census covers the merges of up to ~30 min
cd "$S5_REPO" || exit 2
HEAD0=$(git rev-parse HEAD)
git fetch -q --deepen 300 origin clean/integration 2>/dev/null || true
git merge-base --is-ancestor "$OLD" "$HEAD0" 2>/dev/null || { echo "old census $OLD not an ancestor of ${HEAD0:0:8}"; exit 2; }
mapfile -t cands < <(git log --format='%H %s' "$OLD..$HEAD0" | awk '$2 == "xc:" {print $1}' | head -n "$MAXC")
echo "attribute: $(python3 -c 'import json,sys;print(len(json.load(open(sys.argv[1]))))' "$REG") regressions, ${#cands[@]} xc merges in ${OLD:0:8}..${HEAD0:0:8}"
declare -A culprit repro
pairs=$(python3 -c 'import json,sys
for r in json.load(open(sys.argv[1])): print(r["kind"], r["scenario"], sep="\t")' "$REG")
# clean <kind> <scenario line>: 1 when a kind-only run of the current build compares <kind> clean there
clean(){
  rm -rf "$XC/attr"; echo "$2" | xc_runs "$XC/attr" "$1" >&2
  python3 -c 'import json,sys
g=json.load(open(sys.argv[1]))["groups"].get(sys.argv[2],{"failing":1,"samples":0})
print(int(g["samples"]>0 and g["failing"]==0))' "$XC/attr/${2%% *}.json" "$1" 2>/dev/null
}
# each candidate is judged by a kind-only run: a pair that is clean in one at HEAD already would blame the newest
# candidate whatever it changed, so only a pair that differs there is attributed (2026-10-30: FN_583FC8 in combat
# and wsecon, identical in its kind-only runs, got the same placement-check merge reverted twice)
git reset -q --hard "$HEAD0"
if xc_build; then
  while IFS=$'\t' read -r kind scen; do
    line=$(xc_scenarios | awk -v n="$scen" '$1 == n')
    [ -n "$line" ] && [ "$(clean "$kind" "$line")" != 1 ] && repro["$kind $scen"]=1
  done <<<"$pairs"
else
  while IFS=$'\t' read -r kind scen; do repro["$kind $scen"]=1; done <<<"$pairs"
fi
while IFS=$'\t' read -r kind scen; do
  line=$(xc_scenarios | awk -v n="$scen" '$1 == n')
  [ -n "$line" ] || continue
  if [ -z "${repro["$kind $scen"]:-}" ]; then
    echo "$kind in $scen: compares clean in a kind-only run at ${HEAD0:0:8}, not attributed"; continue
  fi
  found=""
  for c in "${cands[@]}"; do
    [ -n "${culprit[$c]:-}" ] && { found=$c; break; }   # an already identified culprit is reverted anyway
    git reset -q --hard "$HEAD0"
    git revert --no-commit "$c" > /dev/null 2>&1 || { git revert --abort > /dev/null 2>&1; continue; }
    xc_build || continue
    if [ "$(clean "$kind" "$line")" = 1 ]; then found=$c; break; fi
  done
  git reset -q --hard "$HEAD0"
  if [ -n "$found" ]; then
    culprit[$found]="${culprit[$found]:-}$kind in $scen; "
    echo "$kind in $scen: broken by ${found:0:8}"
  else
    echo "$kind in $scen: no xc merge explains it"
    gh api "repos/$AGENT/issues" -f title="[xc-regression] $kind in $scen since ${OLD:0:8}" \
      -f body="The census found task kind \`$kind\` differing in scenario \`$scen\` on $PROJECT@${HEAD0:0:12}; it compared clean on ${OLD:0:12}. No xc merge in the range explains it (another change did). Counts only; the diffs are in the private work list (siedler-data release xc-worklist)." > /dev/null || true
  fi
done <<<"$pairs"

rc=0
for c in "${!culprit[@]}"; do
  subj=$(git log -1 --format=%s "$c"); br="xc/revert-${c:0:10}"
  git fetch -q origin clean/integration && git checkout -q -B "$br" origin/clean/integration
  if ! git -c user.name=siedler-agent -c user.email=Kofi55533@users.noreply.github.com revert --no-edit "$c" > /dev/null 2>&1; then
    git revert --abort > /dev/null 2>&1; echo "revert of ${c:0:8} conflicts"; rc=1; continue
  fi
  git push -q -f origin "HEAD:refs/heads/$br" || { rc=1; continue; }
  pr=$(gh api "repos/$PROJECT/pulls" -f head="$br" -f base=clean/integration -f title="Revert \"$subj\"" \
       -f body="The post-merge census (siedler-agent scripts/xc) found other kinds newly differing after ${c:0:12}: ${culprit[$c]}reverting it; the kind is picked again by the next worker wave." --jq .number) || { rc=1; continue; }
  if gh api -X PUT "repos/$PROJECT/pulls/$pr/merge" -f merge_method=squash -f sha="$(git rev-parse HEAD)" > /dev/null 2>&1; then
    echo "reverted ${c:0:8} via $PROJECT#$pr"
  else
    echo "revert PR $PROJECT#$pr not mergeable"; rc=1
  fi
done
git checkout -q --detach "$HEAD0"
exit $rc
