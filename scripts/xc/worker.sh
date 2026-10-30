#!/usr/bin/env bash
# worker.sh <kind>: one kind of the per-call differential (an ExecuteTask task kind, an entity tick of one type
# TICK_<CLASS>_<type>, or one native function FN_<address>: its port and binding), from measurement to merge.
#   needs: s5-setup.sh done, $XC/worklist.json (census), $XC_NATIVE (functions.jsonl + code.jsonl), $W (model
#   workspace), GH_TOKEN (PAT; never passed to the model), opencode installed with its auth.
# A small function to port takes up to XC_BUNDLE-1 (default 2) more of them along (xc.py bundle): one base measurement, model session
# and verdict for all, shipped as one change.
# 1. measure the kind on the current clean/integration (all scenarios where it runs) -> nothing to do if identical
# 2. context pack (measured diffs, native decompilation, clean location) -> model rounds, each checked by verify.sh;
#    a failed verdict goes back to the model with its lines
# 3. PASS -> commit, push xc/<kind>, PR into clean/integration, squash-merge (re-verified on a moved base)
# Exit 0: merged or nothing to do; 1: no passing change (attempt recorded); 2: infrastructure.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/lib.sh"
KIND=$1; BR=$(python3 "$D/xc.py" branch "$KIND"); WL=$XC/worklist.json
PROJECT=${XC_PROJECT:-Kofi55533/siedler-sim-rs}; AGENT=${XC_AGENT:-Kofi55533/siedler-agent}
MODEL=${XC_MODEL:-opencode/deepseek-v4.1-flash-free}; ROUNDS=${XC_ROUNDS:-3}; ROUND_MIN=${XC_ROUND_MIN:-40}
# no new model round once it could not end inside this budget (minutes since start): of 168 jobs (2026-10-24) none that
# ran past 3 h merged, the latest merge came after 162 min, so a fresh kind gets the slot instead
BUDGET_MIN=${XC_BUDGET_MIN:-170}
log(){ echo "[$(date -u +%H:%M:%S)] $*"; }
sum(){ [ -n "${GITHUB_STEP_SUMMARY:-}" ] && echo "$*" >> "$GITHUB_STEP_SUMMARY"; log "$*"; }
wl_sha(){ python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['sha'])" "$WL"; }
# the pick job claimed the kind; every exit but a recorded attempt (1) or a merge hands it back
MERGED=0; KINDS=$KIND
unclaim(){ for k in "$@"; do python3 "$D/xc.py" issue "$k" unclaim; done; }
trap 'rc=$?; [ "$rc" != 1 ] && [ $MERGED = 0 ] && unclaim ${KINDS//,/ }' EXIT

cd "$S5_REPO" || exit 2
# the checkout carries no credential (the model reads it); fetch and push name the authenticated URL explicitly
AUTH="https://x-access-token:${GH_TOKEN}@github.com/$PROJECT.git"
fetch_base(){ git fetch -q "$AUTH" "+refs/heads/clean/integration:refs/remotes/origin/clean/integration"; }
# a job works several kinds in a row: an earlier kind's unshipped change (or an aborted merge) leaves the checkout,
# and the workspace starts empty, so the model never sees the previous task's notes
git reset -q; git merge --abort 2>/dev/null
xc_changed | while read -r p; do
  if git cat-file -e "HEAD:$p" 2>/dev/null; then git checkout -q HEAD -- "$p"; else rm -rf -- "$p"; fi
done
rm -rf "$W"
fetch_base && git checkout -q -B "$BR" origin/clean/integration || exit 2
git rev-parse HEAD > "$XC/base.sha"; BASE=$(cat "$XC/base.sha")
# the kind is compared in the scenarios where it failed plus a few where it was clean (at most 9: each verdict runs them
# all); the next census compares every scenario and reverts a merge that broke one of the others
python3 "$D/xc.py" scenarios "$WL" "$KIND" | cut -d' ' -f1-9 > "$XC/scenarios-all.txt"
python3 "$D/xc.py" failing "$WL" "$KIND" | cut -d' ' -f1-3 > "$XC/scenarios-failing.txt"
grep -q . "$XC/scenarios-failing.txt" || { sum "$KIND: not failing in the work list"; exit 0; }
# small functions to port, each called most in one of this kind's full-run scenarios, join it (claimed for this job)
# (trial 2026-10-25: 4 functions in one 61-min job, 2 model rounds of ~22 min, against ~18 min per single kind; every
# kind of a bundle has to pass in the same round, so the default stays at 3)
KINDS=$(python3 "$D/xc.py" bundle "$WL" "$KIND" "${XC_BUNDLE:-3}" "$XC_NATIVE") && [ -n "$KINDS" ] || KINDS=$KIND
# the companions' own calling scenarios join the kind-only runs (this kind's first, at most 12 in all)
if [ "$KINDS" != "$KIND" ]; then
  for k in ${KINDS//,/ }; do python3 "$D/xc.py" scenarios "$WL" "$k" | cut -d' ' -f1-9; done \
    | tr ' ' '\n' | awk 'NF && !seen[$0]++' | head -12 | xargs > "$XC/scenarios-all.txt"
fi
log "build + baseline of $KINDS on ${BASE:0:8}: failing in $(cat "$XC/scenarios-failing.txt")"
t0=$(date +%s)
xc_build || exit 2
# the base build stays runnable: verify.sh measures the base's full runs only when the change seems to break another
# kind against the work list (most changes do not), instead of measuring them up front for every kind
xc_keep_bins "$XC/base-bin"
BIND=$(xc_binding "$KIND")
base_only(){
  rm -rf "$XC/base"; mkdir -p "$XC/base/only"
  xc_scenarios | awk -v s=" $(cat "$XC/scenarios-all.txt") " 'index(s, " "$1" ")' | xc_runs "$XC/base/only" "$KINDS"
}
# <field> summed over the base runs of the kind (or of <kind>)
shows(){
  python3 -c 'import json,sys;from pathlib import Path;print(sum(json.loads(f.read_text())["groups"].get(sys.argv[2],{}).get(sys.argv[3],0) for f in Path(sys.argv[1]).glob("*.json")))' \
    "$XC/base/only" "${2:-$KIND}" "$1"
}
# the census plays XC_TURNS (1200), but a verdict at that length took ~30 min instead of ~6 at 120 (2026-10-24: no merge
# for an hour with 14 workers): the work (base, model checks, verdicts) plays XC_TURNS_SHORT when the kind shows there
# (a bound kind differs, an unbound one is called), else the full length; the census still compares every merge at 1200
FULL_TURNS=$XC_TURNS; export XC_TURNS=$XC_TURNS_SHORT
base_only
if [ -n "$BIND" ] && [ ! -f "$BIND" ]; then
  [ "$(shows entries)" -gt 0 ] || export XC_TURNS=$FULL_TURNS
  # a bundled function the short play does not call leaves the bundle (its verdict would need the full length); a hand
  # trial keeps it and plays the full length instead, since the scope check admits only the bundle's binding files
  if [ "$XC_TURNS" != "$FULL_TURNS" ] && [ "$KINDS" != "$KIND" ] && [ -n "${XC_HAND:-}" ]; then
    for k in ${KINDS//,/ }; do [ "$(shows entries "$k")" -gt 0 ] || { export XC_TURNS=$FULL_TURNS; break; }; done
  elif [ "$XC_TURNS" != "$FULL_TURNS" ] && [ "$KINDS" != "$KIND" ]; then
    keep=$KIND; for k in ${KINDS//,/ }; do
      [ "$k" = "$KIND" ] && continue
      if [ "$(shows entries "$k")" -gt 0 ]; then keep=$keep,$k; else unclaim "$k"; fi
    done; KINDS=$keep
  fi
  # a function without binding: its base runs would only count its calls, which the work list already holds
  rm -rf "$XC/base"; mkdir -p "$XC/base/only"
  python3 "$D/xc.py" stub "$WL" "$KINDS" "$XC/base/only" $(cat "$XC/scenarios-all.txt")
else
  [ "$(shows failing)" -gt 0 ] || { export XC_TURNS=$FULL_TURNS; base_only; }
  python3 "$D/xc.py" check "$XC/base" "$XC/base" "$KIND" > "$XC/base.verdict"
  # nothing left for this census (fixed by another merge, or an old worker closed the issue): no re-pick until it re-measures
  if grep -q 'VERDICT PASS' "$XC/base.verdict"; then
    sum "$KIND: already identical on ${BASE:0:8}"; MERGED=1; python3 "$D/xc.py" issue "$KIND" merged "$(wl_sha)"; exit 0
  fi
  grep -q 'no sampled call' "$XC/base.verdict" && { sum "$KIND: no sampled call at base (runs crashed?)"; exit 2; }
fi
N=$(tr ',' '\n' <<<"$KINDS" | wc -l)
log "base measured in $(( $(date +%s) - t0 ))s, work at $XC_TURNS turns$([ "$N" = 1 ] || echo ", a bundle of $N functions")"

mkdir -p "$W"; ln -sfn "$S5_REPO" "$W/siedler-sim-rs"
if [ "$N" = 1 ]; then
  python3 "$D/context.py" "$KIND" "$XC/base" "$XC_NATIVE" "$S5_REPO" "$W/TASK.md" || exit 2
else
  printf '%s\n' "# Bundle of $N small native functions: ${KINDS//,/, }" "" \
    "Port every one of them: each section below is one function with its own binding file. The check and the" \
    "pipeline's verdict compare all of them; the change ships only when every one is identical." "" > "$W/TASK.md"
  for k in ${KINDS//,/ }; do
    python3 "$D/context.py" "$k" "$XC/base" "$XC_NATIVE" "$S5_REPO" "$W/TASK-part.md" || exit 2
    cat "$W/TASK-part.md" >> "$W/TASK.md"; rm -f "$W/TASK-part.md"
  done
fi
cat > "$W/check" <<C
#!/usr/bin/env bash
XC_TURNS=$XC_TURNS exec "$D/check.sh" "$KINDS" "\${1:-failing}"
C
chmod +x "$W/check"
cat > "$W/opencode.json" <<'J'
{"$schema":"https://opencode.ai/config.json","permission":{"edit":"allow","bash":"allow","webfetch":"deny","external_directory":"allow"}}
J
EDIT="crates/s5clean/"
case $KIND in
  TICK_*) WHAT="the ordinary per-turn tick of entity type ${KIND#TICK_*_} (TICK_<class>_<type>)"; POINT="the entity tick (crates/s5xcheck/tests/capture-ticks.rs)" ;;
  FN_*) WHAT="the native function 0x${KIND#FN_} (its clean implementation and its binding $BIND, see TASK.md)"
        POINT="the function census (crates/s5xcheck/tests/capture-functions.rs)"; EDIT="crates/s5clean/ and the binding $BIND"
        if [ "$N" -gt 1 ]; then
          WHAT="the $N native functions $(sed 's/FN_/0x/g; s/,/, /g' <<<"$KINDS") (each its clean implementation and its binding, see TASK.md)"
          EDIT="crates/s5clean/ and the bindings $(xc_binding "$KINDS" | xargs)"
        fi ;;
  *) WHAT="task kind $KIND"; POINT="ExecuteTask 0x57BF33" ;;
esac
PROMPT="You are porting one piece of behaviour of the original game Die Siedler: Das Erbe der Koenige into its clean, hand-written
Rust reimplementation, so that it behaves exactly like the original at the original's own calls.
Workspace: $W. The Rust repo is $W/siedler-sim-rs (crate crates/s5clean). Read $W/TASK.md first: it holds the measured
differences between the original and the clean port at real calls of $WHAT (\`-\` original, \`+\` clean), the
original's decompiled functions and callees, and where the clean code handles the kind today.
Goal: \`$W/check\` reports 0 differing calls for ${KINDS//,/, } (it builds and runs the comparison, several minutes; \`$W/check all\`
runs every scenario where the kind appears). Work loop: read the difference, find the native logic that produces the
original's value (decompilation in TASK.md; more functions: grep the address in $XC_NATIVE/functions.jsonl, read the
record at its code_offset in code.jsonl), change the clean code, run the check, repeat.
Rules: edit only $EDIT (and docs/logic/ if a spec statement changes). Write idiomatic Rust in the style of the
surrounding code; cite native addresses in doc comments the way the existing code does. Never hide differences: no
hand-written Debug/PartialEq, no removed fields or tests, the importer crates/s5clean/src/reference.rs may only be
extended (when the original's value lives in a native field the importer does not read yet). Do not copy generated code
from recomp/. Do not commit, push or open PRs; the pipeline verifies and ships your change. Finish when the check is clean."

# the base's full runs of the failing scenarios (verify.sh compares the change against them, 150-600 s of every verdict on
# 2026-10-25) play while the model works: its rounds mostly wait on the model's API, and at nice 19 the model's own
# checks keep the cores. The base build and BASE stay fixed for the kind, so every verdict of it reuses them.
( renice -n 19 "$BASHPID" > /dev/null
  xc_scenarios | awk -v s=" $(cat "$XC/scenarios-failing.txt") " 'index(s, " "$1" ")' \
    | XC_BINS=$XC/base-bin/bins.txt xc_runs "$XC/base/full" ) > /dev/null 2>&1 &
AHEAD=$!
# model <round>: one opencode session turn on $PROMPT (round 1 starts the session, later ones continue it)
model(){
  log "model round $1"
  local args=(opencode run -m "$MODEL"); [ "$1" != 1 ] && args+=(--continue)
  ( cd "$W" && env -u GH_TOKEN -u GITHUB_TOKEN -u T -u OPENCODE_API_KEY -u PRIVATE_REPO_TOKEN \
      timeout -k 30 "$((ROUND_MIN * 60))" "${args[@]}" "$PROMPT" ) > "$XC/model-$1.log" 2>&1
  log "model round $1 exit=$? ($(wc -l < "$XC/model-$1.log") log lines, private)"
}
ok=0
rounds=0
for round in $(seq 1 "$ROUNDS"); do
  if [ "$round" -gt 1 ] && [ $(( SECONDS + ROUND_MIN * 60 )) -gt $(( BUDGET_MIN * 60 )) ]; then
    log "budget: no round $round after $(( SECONDS / 60 )) min"; break
  fi
  rounds=$round; model "$round"
  git -C "$S5_REPO" reset -q --soft "$BASE"   # a model commit is folded back into the working change
  # the base runs write the same per-run files ($XC/{rc,secs}-<scenario>.*) as the verdict's full runs
  t=$SECONDS; wait "$AHEAD" 2>/dev/null; [ $((SECONDS - t)) -gt 0 ] && log "waited $((SECONDS - t))s for the base's full runs"
  "$D/verify.sh" "$KINDS" > "$XC/verify-$round.txt" 2>&1
  sed 's/^/  /' "$XC/verify-$round.txt" | grep -E 'scope|identical|VERDICT|regression|missing|tests|tick|build|timing' | head -14
  if grep -q 'VERDICT PASS' "$XC/verify-$round.txt"; then ok=1; break; fi
  PROMPT="The pipeline's verification of your change failed:
$(grep -v '^VERDICT' "$XC/verify-$round.txt" | head -40)
Fix this (run $W/check to see the remaining differences) and finish when the check is clean and nothing else regressed."
done

record_attempt(){ for k in ${KINDS//,/ }; do python3 "$D/xc.py" issue "$k" attempt "$(wl_sha)"; done; }
if [ $ok = 0 ]; then sum "$KINDS: no passing change after $rounds rounds"; record_attempt; exit 1; fi

# ship: one commit on the measured base, PR, squash-merge; a moved base is merged in and re-verified once
cd "$S5_REPO"
git add -A -- $(xc_paths "$KIND")
summary=$(grep -E 'identical|scope ok' "$XC/verify-$round.txt" | head -6)
TITLE="xc: ${KINDS//,/, } identical to the original at its real calls"
git -c user.name=siedler-agent -c user.email=Kofi55533@users.noreply.github.com commit -q -m "$TITLE

Measured by the per-call differential (crates/s5xcheck capture, $POINT) in the
recorded scenarios; verified by siedler-agent scripts/xc/verify.sh (scope, kind, no other kind
regressed, s5clean tests, building tick).
$summary" || exit 2
# GitHub refuses a squash merge while it recomputes the mergeability after the base moved: 13 of 68 merges on 2026-10-25,
# 8 of them merging cleanly in git, each paid a re-verify (~10 min). The verdict's base moves on most merges anyway (the
# census guards the moved base), so a refusal waits for GitHub and retries; only a PR GitHub calls unmergeable (a real
# conflict) or a refusal that persists merges the base in and re-verifies
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
# a merge of the moved base that conflicts (another merge changed the same lines: 11 of 114 attempts on 2026-10-25, each
# losing a passing change) goes back to the model once: it resolves the markers keeping both sides, the moved-base
# verdict below judges the result
resolve(){
  local files; files=$(git diff --name-only --diff-filter=U | xargs)
  [ -n "$files" ] && [ $(( SECONDS + ROUND_MIN * 60 )) -le $(( BUDGET_MIN * 60 )) ] || return 1
  log "conflict with the moved base in $(wc -w <<<"$files") file(s): the model resolves it"
  PROMPT="Your change passed, but clean/integration moved meanwhile and merging it into your change conflicts in: $files
Resolve every conflict marker (<<<<<<< ======= >>>>>>>) in those files so that both sides stay: the other merge's
behaviour and yours. Change nothing else, do not commit; run $W/check and finish when it is clean."
  model conflict
  grep -lE '^(<<<<<<<|>>>>>>>)( |$)' $files 2>/dev/null | grep -q . && return 1
  git rev-parse -q --verify MERGE_HEAD > /dev/null || return 1   # the model committed or aborted the merge itself
  git add -- $files && git -c user.name=siedler-agent -c user.email=Kofi55533@users.noreply.github.com commit -q --no-edit
}
# a moved base can hold a change that compiles alone but not beside this one (2026-10-25: #1344 and #1345 each added the
# same World method, and clean/integration stopped compiling for every job and census): the merge result's s5clean has
# to compile first (cargo check of the library and its tests, its own target directory: ~1 min, incremental after)
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
  pr=$(gh api "repos/$PROJECT/pulls?state=open&head=${PROJECT%%/*}:$BR" --jq '.[0].number // empty')
  [ -z "$pr" ] && pr=$(gh api "repos/$PROJECT/pulls" -f head="$BR" -f base=clean/integration \
      -f title="$TITLE" \
      -f body="Per-call differential ($POINT, kind \`${KINDS//,/\`, \`}\`), verified by the pipeline:
\`\`\`
$(grep -vE '^VERDICT' "$XC/verify-$round.txt" | head -20)
\`\`\`" --jq .number)
  [ -n "$pr" ] || exit 2
  if compiles_on_base && merge_pr "$pr"; then
    sum "$KINDS: merged $PROJECT#$pr"; MERGED=1
    for k in ${KINDS//,/ }; do python3 "$D/xc.py" issue "$k" merged "$(wl_sha)"; done
    # no census per merge (it held 8 of the 20 job slots without pause): the watchdog's next census, at most 30 min
    # after the last one, compares every scenario and reverts this merge if another kind broke anywhere
    exit 0
  fi
  [ $try = 2 ] && break
  log "not merged: merging clean/integration and re-verifying"
  fetch_base
  git -c user.name=siedler-agent -c user.email=Kofi55533@users.noreply.github.com merge -q --no-edit origin/clean/integration \
    || resolve || { git merge --abort; sum "$KINDS: conflict with the moved base"; break; }
  git rev-parse origin/clean/integration > "$XC/base.sha"; BASE=$(cat "$XC/base.sha")
  git reset -q --soft "$BASE"
  "$D/verify.sh" "$KINDS" > "$XC/verify-merge.txt" 2>&1 || { sum "$KINDS: fails on the moved base"; break; }
  git add -A -- $(xc_paths "$KIND")
  git -c user.name=siedler-agent -c user.email=Kofi55533@users.noreply.github.com commit -q -m "$TITLE (on ${BASE:0:8})"
  round=merge
done
[ -n "${pr:-}" ] && gh api -X PATCH "repos/$PROJECT/pulls/$pr" -f state=closed > /dev/null
record_attempt; exit 1
