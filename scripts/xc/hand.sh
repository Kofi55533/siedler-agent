#!/usr/bin/env bash
# hand.sh <ref> <kinds> [pr]: verify a hand-written port already on a siedler-sim-rs branch and
# squash-merge it into clean/integration on VERDICT PASS (Codex / Opus hand path).
#   <ref>   branch on XC_PROJECT (e.g. xc/fn-57d214 or claude/hand-fn-444c9c)
#   <kinds> comma-separated kinds the change covers (e.g. FN_57D214,FN_57D10D)
#   [pr]    optional existing PR number into clean/integration; found / opened when omitted
# Needs: s5-setup.sh done on clean/integration, $XC/worklist.json, GH_TOKEN (PAT). No model.
# Exit 0: merged; 1: verify FAIL (PR commented, left open); 2: infrastructure.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/lib.sh"
REF=${1:?ref}; KINDS=${2:?kinds}; PR_IN=${3:-}
PROJECT=${XC_PROJECT:-Kofi55533/siedler-sim-rs}; AGENT=${XC_AGENT:-Kofi55533/siedler-agent}
WL=$XC/worklist.json
KIND=${KINDS%%,*}; BR=$REF
POINT=$(case $KIND in TICK_*) echo "the entity tick (crates/s5xcheck/tests/capture-ticks.rs)" ;;
  FN_*) echo "the function census (crates/s5xcheck/tests/capture-functions.rs)" ;;
  *) echo "ExecuteTask 0x57BF33" ;; esac)
log(){ echo "[$(date -u +%H:%M:%S)] $*"; }
sum(){ [ -n "${GITHUB_STEP_SUMMARY:-}" ] && echo "$*" >> "$GITHUB_STEP_SUMMARY"; log "$*"; }
wl_sha(){ python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['sha'])" "$WL"; }
MERGED=0
unclaim(){ for k in "$@"; do python3 "$D/xc.py" issue "$k" unclaim; done; }
trap 'rc=$?; [ "$rc" != 1 ] && [ $MERGED = 0 ] && unclaim ${KINDS//,/ }' EXIT

cd "$S5_REPO" || exit 2
AUTH="https://x-access-token:${GH_TOKEN}@github.com/$PROJECT.git"
fetch_base(){ git fetch -q "$AUTH" "+refs/heads/clean/integration:refs/remotes/origin/clean/integration"; }
git reset -q; git merge --abort 2>/dev/null
xc_changed | while read -r p; do
  if git cat-file -e "HEAD:$p" 2>/dev/null; then git checkout -q HEAD -- "$p"; else rm -rf -- "$p"; fi
done
fetch_base && git checkout -q -B hand-verify origin/clean/integration || exit 2
git rev-parse HEAD > "$XC/base.sha"; BASE=$(cat "$XC/base.sha")
sum "hand: ref=$REF kinds=$KINDS base=${BASE:0:8} pr=${PR_IN:-find}"

# claim each kind so fleet workers do not race the same unit while this job runs
for k in ${KINDS//,/ }; do python3 "$D/xc.py" issue "$k" claim; done

# scenarios: union of every kind (failing first for the primary), at most 12
{
  python3 "$D/xc.py" failing "$WL" "$KIND"
  for k in ${KINDS//,/ }; do python3 "$D/xc.py" scenarios "$WL" "$k"; done
} | tr ' ' '\n' | awk 'NF && !seen[$0]++' | head -12 | xargs > "$XC/scenarios-all.txt"
python3 "$D/xc.py" failing "$WL" "$KIND" | cut -d' ' -f1-3 > "$XC/scenarios-failing.txt"
# a kind with no failing entry still needs its calling scenarios for the kind-only runs
if ! grep -q . "$XC/scenarios-failing.txt"; then
  python3 "$D/xc.py" scenarios "$WL" "$KIND" | cut -d' ' -f1-3 > "$XC/scenarios-failing.txt"
fi
grep -q . "$XC/scenarios-all.txt" || { sum "$KINDS: no scenarios in the work list"; exit 2; }

log "build + baseline of $KINDS on ${BASE:0:8}: scenarios $(cat "$XC/scenarios-all.txt")"
t0=$(date +%s)
xc_build || exit 2
xc_keep_bins "$XC/base-bin"
BIND=$(xc_binding "$KIND")
base_only(){
  rm -rf "$XC/base"; mkdir -p "$XC/base/only"
  xc_scenarios | awk -v s=" $(cat "$XC/scenarios-all.txt") " 'index(s, " "$1" ")' | xc_runs "$XC/base/only" "$KINDS"
}
shows(){
  python3 -c 'import json,sys;from pathlib import Path;print(sum(json.loads(f.read_text())["groups"].get(sys.argv[2],{}).get(sys.argv[3],0) for f in Path(sys.argv[1]).glob("*.json")))' \
    "$XC/base/only" "${2:-$KIND}" "$1"
}
FULL_TURNS=$XC_TURNS; export XC_TURNS=$XC_TURNS_SHORT
base_only
if [ -n "$BIND" ] && [ ! -f "$BIND" ]; then
  # unbound on clean/integration: short play must call it, else full length; kinds stay as the caller named them
  [ "$(shows entries)" -gt 0 ] || export XC_TURNS=$FULL_TURNS
  rm -rf "$XC/base"; mkdir -p "$XC/base/only"
  python3 "$D/xc.py" stub "$WL" "$KINDS" "$XC/base/only" $(cat "$XC/scenarios-all.txt")
else
  [ "$(shows failing)" -gt 0 ] || { export XC_TURNS=$FULL_TURNS; base_only; }
  python3 "$D/xc.py" check "$XC/base" "$XC/base" "$KIND" > "$XC/base.verdict"
  if grep -q 'VERDICT PASS' "$XC/base.verdict"; then
    sum "$KIND: already identical on ${BASE:0:8}"; MERGED=1
    for k in ${KINDS//,/ }; do python3 "$D/xc.py" issue "$k" merged "$(wl_sha)"; done
    exit 0
  fi
  grep -q 'no sampled call' "$XC/base.verdict" && { sum "$KIND: no sampled call at base"; exit 2; }
fi
log "base measured in $(( $(date +%s) - t0 ))s, work at $XC_TURNS turns"

# base full runs of the failing scenarios while the hand diff is applied (instant) and verify starts
( renice -n 19 "$BASHPID" > /dev/null
  xc_scenarios | awk -v s=" $(cat "$XC/scenarios-failing.txt") " 'index(s, " "$1" ")' \
    | XC_BINS=$XC/base-bin/bins.txt xc_runs "$XC/base/full" ) > /dev/null 2>&1 &
AHEAD=$!

log "apply clean/integration...$REF"
if ! gh api -H 'Accept: application/vnd.github.diff' "repos/$PROJECT/compare/clean/integration...$REF" \
    | git apply --3way --index; then
  sum "hand: git apply failed for $REF onto ${BASE:0:8}"
  wait "$AHEAD" 2>/dev/null
  exit 2
fi
c=$(git diff --name-only --diff-filter=U | wc -l); git reset -q
if [ "$c" -gt 0 ]; then
  sum "CONFLICT: $c file(s) of $REF conflict with clean/integration"
  wait "$AHEAD" 2>/dev/null
  pr=${PR_IN:-$(gh api "repos/$PROJECT/pulls?state=open&head=${PROJECT%%/*}:$REF" --jq '.[0].number // empty')}
  [ -n "$pr" ] && gh api "repos/$PROJECT/issues/$pr/comments" -f body="hand verify: CONFLICT applying \`$REF\` onto clean/integration (${BASE:0:8}): $c file(s). Rebase the branch and re-dispatch xc-hand." > /dev/null
  exit 1
fi
log "applied: $(git status --short | wc -l) file(s)"

t=$SECONDS; wait "$AHEAD" 2>/dev/null; [ $((SECONDS - t)) -gt 0 ] && log "waited $((SECONDS - t))s for the base's full runs"
"$D/verify.sh" "$KINDS" > "$XC/verify-hand.txt" 2>&1
sed 's/^/  /' "$XC/verify-hand.txt" | grep -E 'scope|identical|VERDICT|regression|missing|tests|tick|build|timing' | head -20
pr=${PR_IN:-$(gh api "repos/$PROJECT/pulls?state=open&head=${PROJECT%%/*}:$REF" --jq '.[0].number // empty')}

if ! grep -q 'VERDICT PASS' "$XC/verify-hand.txt"; then
  sum "$KINDS: VERDICT FAIL"
  body="hand verify (**FAIL**) of \`$REF\` on clean/integration \`${BASE:0:8}\` for \`${KINDS//,/\`, \`}\`:
\`\`\`
$(grep -vE '^VERDICT' "$XC/verify-hand.txt" | head -40)
\`\`\`
PR left open; fix and re-dispatch \`xc-hand.yml\`."
  if [ -n "$pr" ]; then
    gh api "repos/$PROJECT/issues/$pr/comments" -f body="$body" > /dev/null
  else
    log "no open PR for $REF to comment on"
  fi
  # record attempt like a worker (keeps the kind from looping forever on the same census)
  for k in ${KINDS//,/ }; do python3 "$D/xc.py" issue "$k" attempt "$(wl_sha)"; done
  exit 1
fi

# ship: one commit on the measured base, push to $REF, squash-merge (re-verify once if the base moved)
git add -A -- $(xc_paths "$KINDS")
summary=$(grep -E 'identical|scope ok' "$XC/verify-hand.txt" | head -6)
TITLE="xc: ${KINDS//,/, } identical to the original at its real calls"
git -c user.name=siedler-agent -c user.email=Kofi55533@users.noreply.github.com commit -q -m "$TITLE

Measured by the per-call differential (crates/s5xcheck capture, $POINT) in the
recorded scenarios; verified by siedler-agent scripts/xc/verify.sh (scope, kind, no other kind
regressed, s5clean tests, building tick). Hand path (scripts/xc/hand.sh) of ref $REF.
$summary" || exit 2

merge_pr(){
  local i m out
  for i in 1 2 3 4; do
    out=$(gh api -X PUT "repos/$PROJECT/pulls/$1/merge" -f merge_method=squash -f sha="$(git rev-parse HEAD)" 2>&1 >/dev/null) && return 0
    for _ in 1 2 3 4 5 6; do
      m=$(gh api "repos/$PROJECT/pulls/$1" --jq '.mergeable // "unknown"' 2>/dev/null); [ "$m" = unknown ] || break; sleep 5
    done
    log "merge refused ($(tail -n 1 <<<"$out" | cut -c1-100)), mergeable: $m"
    [ "$m" = false ] && return 1
    sleep $((i * 10))
  done
  return 1
}
compiles_on_base(){
  local rc=0
  fetch_base
  [ "$(git rev-parse origin/clean/integration)" = "$BASE" ] && return 0
  git -c user.name=siedler-agent -c user.email=Kofi55533@users.noreply.github.com merge -q --no-commit --no-ff \
    origin/clean/integration > /dev/null 2>&1 || { git merge --abort 2>/dev/null; log "conflict with the moved base"; return 1; }
  CARGO_TARGET_DIR=$XC/check-target timeout 900 cargo check -q --tests -j "${CARGO_BUILD_JOBS:-3}" \
    --manifest-path crates/s5clean/Cargo.toml > "$XC/merge-check.log" 2>&1 || rc=1
  git merge --abort
  [ $rc = 0 ] || log "the merge with the moved base does not compile ($(grep -c '^error' "$XC/merge-check.log") errors)"
  return $rc
}

for try in 1 2; do
  git push -q -f "$AUTH" "HEAD:refs/heads/$BR" || exit 2
  pr=${PR_IN:-$(gh api "repos/$PROJECT/pulls?state=open&head=${PROJECT%%/*}:$BR" --jq '.[0].number // empty')}
  [ -z "$pr" ] && pr=$(gh api "repos/$PROJECT/pulls" -f head="$BR" -f base=clean/integration \
      -f title="$TITLE" \
      -f body="Per-call differential ($POINT, kind \`${KINDS//,/\`, \`}\`), verified by the hand path:
\`\`\`
$(grep -vE '^VERDICT' "$XC/verify-hand.txt" | head -20)
\`\`\`" --jq .number)
  [ -n "$pr" ] || exit 2
  PR_IN=$pr
  if compiles_on_base && merge_pr "$pr"; then
    sum "$KINDS: merged $PROJECT#$pr"
    MERGED=1
    for k in ${KINDS//,/ }; do python3 "$D/xc.py" issue "$k" merged "$(wl_sha)"; done
    exit 0
  fi
  [ $try = 2 ] && break
  log "not merged: merging clean/integration and re-verifying"
  fetch_base
  git -c user.name=siedler-agent -c user.email=Kofi55533@users.noreply.github.com merge -q --no-edit origin/clean/integration \
    || { git merge --abort; sum "$KINDS: conflict with the moved base"; break; }
  git rev-parse origin/clean/integration > "$XC/base.sha"; BASE=$(cat "$XC/base.sha")
  git reset -q --soft "$BASE"
  "$D/verify.sh" "$KINDS" > "$XC/verify-merge.txt" 2>&1 || { sum "$KINDS: fails on the moved base"; break; }
  git add -A -- $(xc_paths "$KINDS")
  git -c user.name=siedler-agent -c user.email=Kofi55533@users.noreply.github.com commit -q -m "$TITLE (on ${BASE:0:8})"
done
[ -n "${pr:-}" ] && gh api "repos/$PROJECT/issues/$pr/comments" -f body="hand verify PASS on the measured base, but squash-merge into clean/integration failed (moved base / conflict). Re-dispatch xc-hand after a rebase." > /dev/null
for k in ${KINDS//,/ }; do python3 "$D/xc.py" issue "$k" attempt "$(wl_sha)"; done
exit 1
