#!/usr/bin/env bash
# s5-gate.sh <pr-number> [--dry-run]
# Muse self-merge gate for Kofi55533/siedler-sim-rs PRs into clean/integration.
# Merge rule (Opus decision 2026-10-18): bit-exact unit tests (crates/s5clean cargo test green) AND no regression on the
# full live matrix ($S5_GATE_FIXTURES, plus any installed "Frontier-fixture: <name>" named in the PR body). No exceptions
# (the 2026-10-17 "Port:" regression exception is revoked). Merges (squash) only if: base is clean/integration, head is
# agent/*, no HARD path touched (scripts/muse-claims.txt: recomp/, oracle/, s5verify/s5diff, .github/), no fresh (<6 h)
# Opus/Codex LANE-CLAIM on docs/AGENT_BOARD.md names a touched file (scripts/lane-claims.py), recomp s5verify builds,
# s5clean tests pass, every fixture's first divergence on (PR merged onto clean/integration) >= clean/integration itself,
# and, if a SOFT path (former claimed area, e.g. economy/, world/commands.rs) or topic marker is touched, at least one
# fixture's first divergence moved later. Opus 2026-10-18 20:40: a source-free scenario/coverage/fixture PR needs a body line
# "New-code-path: ..." or "New-unmodelled: ..." (pure stay-IDENTICAL extensions no longer merge), and a frontier PR
# (title "Diverge ..." or a "Frontier-fixture:" line) needs live before -> after turn numbers in its body. A moved base re-checks merge+build+tests on the new base instead of failing.
# Merge mechanics (Dominic 2026-10-20): the PR's docs/AGENT_BOARD.md hunks are dropped by a gate commit (board lines go directly
# to clean/integration), conflicts name the files, Codex lane claims hold only while the Codex loop heartbeat is active, and a
# "Frontier-fixture:" is installed from git/private releases (scripts/s5-fixture-install.sh) and measured on base and PR.
# Exact-tree verdicts (Codex strategy 2026-10-20 §4.3/§9, tightening only): live results are typed (no "DIVERGENT turn N" /
# "IDENTICAL N ticks" = ERROR/TIMEOUT, never turn 0; a base-side error is DEFERRED, a PR-side error FAILs), every comparison
# is recorded against the merged tree hash ($S5/gate-receipt-<pr>.tsv), a code-changing base move re-measures the new merged
# tree (no reuse), and a named Frontier-fixture that cannot be installed FAILs instead of being skipped.
# Never merges into main. Prints only fixture names and turn numbers (no log contents). Needs GH_TOKEN.
# Code no fixture executes (Dominic 2026-10-20, whole game bit-exact): a "Subsystem <id>:" PR whose newly cited native
# addresses in crates/s5clean/src include logic functions that no fixture executes (docs/subsystems/subsystems.json flags +
# the current coverage map, scripts/subsys-xcheck.py) additionally needs green differential tests against the reference
# (crates/s5xcheck, body line "Differential-tests: <cargo test args>", >= 1 passed / 0 failed on the merged candidate; each
# such address in a crates/s5xcheck/tests file with a reference call; harness src/Cargo.toml unchanged) and a "Fixture:" or
# "Fixture-request:" line. All other criteria are unchanged (no fixture earlier, Frontier-fixture included); only a soft-area
# PR whose newly cited logic functions are ALL unexecuted may replace the frontier move by those green differential tests.
# Speed (2026-10-20, same merge criteria): live fixtures run in parallel (S5_GATE_PARALLEL, default min(nproc, MemAvailable/3 GB));
# the baseline (clean/integration) matrix is cached as asset base-<key>.tsv on release gate-baseline of siedler-agent,
# key = code tree of the base without docs/*.md (the same "code unchanged" rule the moved-base check uses) + data-v2 assets
# + live/build/env script hashes + S5_LIVE_TIMEOUT (S5_GATE_CACHE=0 disables); PR side fails fast on the first fixture that
# diverges earlier than the baseline (S5_GATE_FAILFAST=0 disables; remaining fixtures are then reported as not run).
# S5_GATE_BASELINE_ONLY=1 (+ S5_GATE_KEY_ONLY=1 for the key only): only compute/upload the baseline of current clean/integration (warm-up, PR arg ignored).
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/s5-env.sh"
PR=$1; DRY=0; [ "${2:-}" = --dry-run ] && DRY=1
R=Kofi55533/siedler-sim-rs; BASE=clean/integration
G=$S5/gate/$PR; rm -rf "$G"; mkdir -p "$G"
fail(){ echo "GATE FAIL: $*" | tee -a "$G/report.txt"; finish 1; }
finish(){
  local rc=$1 v; [ $rc = 0 ] && v=PASS || v=FAIL; [ $DRY = 1 ] && v="$v (dry-run, no merge)"
  { echo "### Muse self-merge gate: $v"; echo '```'; cat "$G/report.txt"; echo '```'; } > "$G/comment.md"
  [ "${S5_GATE_COMMENT:-1}" = 1 ] && gh pr comment "$PR" -R $R --body-file "$G/comment.md" >/dev/null 2>&1
  cat "$G/comment.md"; exit $rc; }
# GitHub API rate limit (2026-10-20 13:00-13:26 the shared account hit 5000/5000 REST calls/h): a GitHub call that fails while
# the core (REST) or GraphQL limit is exhausted gives NO verdict: neither PASS nor FAIL, no merge, no claim decision. The gate
# posts "### Muse self-merge gate: DEFERRED" (GraphQL comment, if that still works) and exits 3; refill.yml re-gates PRs
# whose last gate comment is DEFERRED (like lane-held PRs). Merge criteria are unchanged. (rate_limit itself costs nothing.)
rl_hit(){ local r; r=$(gh api rate_limit -q '"\(.resources.core.remaining) \(.resources.graphql.remaining)"' 2>/dev/null) || return 1
  [ "${r%% *}" = 0 ] || [ "${r##* }" = 0 ]; }
defer(){ local rs; rs=$(gh api rate_limit -q '[.resources.core, .resources.graphql]|map(select(.remaining==0).reset)|max // empty' 2>/dev/null)
  { echo "### Muse self-merge gate: DEFERRED (no verdict)"; echo '```'; cat "$G/report.txt"
    echo "deferred: $* failed during a GitHub API rate limit${rs:+ (resets $(TZ=Europe/Berlin date -d "@$rs" '+%H:%M') Berlin)}; merge criteria not evaluated, refill re-gates this PR"; echo '```'; } > "$G/comment.md"
  [ "${S5_GATE_COMMENT:-1}" = 1 ] && gh pr comment "$PR" -R $R --body-file "$G/comment.md" >/dev/null 2>&1
  cat "$G/comment.md"; exit 3; }
apifail(){ rl_hit && defer "$1"; fail "$1"; }   # GitHub call failed: rate limit -> DEFERRED, otherwise FAIL as before
: > "$G/report.txt"; GT0=$(date +%s)
PAR=${S5_GATE_PARALLEL:-}; if [ -z "$PAR" ]; then
  m=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo); PAR=$(( m / ${S5_GATE_GB_PER_FIXTURE:-3} )); [ $PAR -gt $(nproc) ] && PAR=$(nproc); [ $PAR -lt 1 ] && PAR=1; fi
BREL=gate-baseline; BREPO=Kofi55533/siedler-agent
DV2=$(gh release view data-v2 -R Kofi55533/siedler-data --json assets -q '.assets[]|select(.name|test("tzst"))|"\(.name) \(.size) \(.updatedAt)"' 2>/dev/null | sort) && [ -n "$DV2" ] || DV2=""
bkey(){ # $1 = commit -> cache key of its live matrix (fixture list is part of the file, missing fixtures are measured)
  { git ls-tree -r "$1" | grep -vP '\tdocs/(.*\.md|subsystems/(DS[0-9]{2}-[^/]+/[^/]+|domains\.json))$'
    echo "$DV2"
    cat "$D/s5-live.sh" "$D/s5-env.sh" "$D/s5-build.sh"; echo "timeout=${S5_LIVE_TIMEOUT:-1800}"; } | sha256sum | cut -c1-16; }
if [ "${S5_GATE_BASELINE_ONLY:-0}" = 1 ]; then
  cd $S5_REPO; git fetch -q origin "+refs/heads/$BASE:refs/remotes/origin/$BASE" || fail "fetch"
  bs=$(git rev-parse origin/$BASE); FIX="$S5_GATE_FIXTURES"
  # S5_GATE_BASE_REF=<sha>: measure/cache that commit instead of the branch tip (post-hoc verification of a merge)
  if [ -n "${S5_GATE_BASE_REF:-}" ]; then git fetch -q --depth 2 origin "${S5_GATE_BASE_REF}" 2>/dev/null; bs=$(git rev-parse "${S5_GATE_BASE_REF}") || fail "unknown ref"; fi
  # S5_GATE_KEY_ONLY=1: print "<key> <base sha>" and whether the cache has it (warm-up pre-check, no build needed)
  if [ "${S5_GATE_KEY_ONLY:-0}" = 1 ]; then
    # data-v2 or cache listing unreadable (rate limit): "skip" = do not start a warm-up that could neither key nor upload
    [ -z "$DV2" ] && { echo "nokey $bs skip"; exit 0; }
    k=$(bkey "$bs"); names=$(gh release view $BREL -R $BREPO --json assets -q '.assets[].name' 2>/dev/null) || { echo "$k $bs skip"; exit 0; }
    grep -qx "base-$k.tsv" <<<"$names" && echo "$k $bs hit" || echo "$k $bs miss"; exit 0; fi
fi
XC=""; XCALL=0; XCOK=0   # subsystem rule state (set by the PR-only checks below)
if [ "${S5_GATE_BASELINE_ONLY:-0}" != 1 ]; then
j=$(gh pr view "$PR" -R $R --json baseRefName,headRefName,headRefOid,state,isCrossRepository,files,createdAt) || apifail "pr lookup"
read -r b h hs st x < <(python -c "import json,sys;d=json.loads(sys.argv[1]);print(d['baseRefName'],d['headRefName'],d['headRefOid'],d['state'],d['isCrossRepository'])" "$j")
[ "$b" = $BASE ] || fail "base is $b, not $BASE"
[ "$st" = OPEN ] && [ "$x" = False ] || fail "state=$st cross=$x"
case $h in agent/*) ;; *) fail "head $h is not agent/*" ;; esac
cd $S5_REPO
[ -f .git/shallow ] && { git fetch -q --unshallow origin 2>/dev/null || git fetch -q --deepen=5000 origin; }
git fetch -q origin "+refs/heads/$BASE:refs/remotes/origin/$BASE" "+refs/pull/$PR/head:refs/gate/pr" || fail "fetch"
bs=$(git rev-parse origin/$BASE); [ "$(git rev-parse refs/gate/pr)" = "$hs" ] || fail "head moved"
echo "pr=#$PR head=$h@${hs:0:8} base=$BASE@${bs:0:8}" >> "$G/report.txt"
# board hunks never merge with a PR (Dominic 2026-10-20 11:14): docs/AGENT_BOARD.md is append-only on clean/integration and
# board lines go there directly, so a PR's board edits only cause conflicts (22 of 76 gate FAILs 2026-10-19/20). The gate adds
# one commit on top of the PR head that restores the board to the merge-base version (integration side wins, code hunks
# untouched), pushes it to the agent/* branch (not in --dry-run) and measures/merges that head.
BOARDDROP=0
mb0=$(git merge-base "$bs" refs/gate/pr) || fail "no merge base with $BASE"
if ! git diff --quiet "$mb0" refs/gate/pr -- docs/AGENT_BOARD.md; then
  export GIT_INDEX_FILE="$G/drop.idx"; git read-tree refs/gate/pr
  if git cat-file -e "$mb0:docs/AGENT_BOARD.md" 2>/dev/null; then git update-index --cacheinfo "100644,$(git rev-parse "$mb0:docs/AGENT_BOARD.md"),docs/AGENT_BOARD.md"
  else git update-index --force-remove docs/AGENT_BOARD.md; fi
  tr0=$(git write-tree); unset GIT_INDEX_FILE
  dc=$(git -c user.name=siedler-agent -c user.email=Kofi55533@users.noreply.github.com commit-tree "$tr0" -p refs/gate/pr \
       -m "gate: drop docs/AGENT_BOARD.md changes from this PR" -m "Board lines go directly to $BASE (append-only board); the PR keeps its code. Added by s5-gate.sh.") || fail "board drop commit"
  if [ $DRY = 1 ]; then echo "board: PR edits docs/AGENT_BOARD.md; dropped locally for this dry-run (a real gate pushes the drop commit)" >> "$G/report.txt"
  else git push -q origin "$dc:refs/heads/$h" 2>/dev/null || fail "board drop: push to $h refused (branch moved?)"
    hs=$dc; echo "board: dropped the PR's docs/AGENT_BOARD.md hunks (commit ${dc:0:8} pushed to $h; integration board kept)" >> "$G/report.txt"; fi
  git update-ref refs/gate/pr "$dc"; BOARDDROP=1
fi
# cheap conflict pre-check (same criterion as the merge below, but before the ~40 min baseline suite; 2026-10-18/19:
# 108 of 355 gates failed on a conflict only after the full baseline). Needs git >= 2.38 (merge-tree --write-tree).
if git merge-tree --write-tree "$bs" refs/gate/pr >/dev/null 2>&1; then :
elif [ $? = 1 ]; then
  cf=$(git merge-tree --write-tree --name-only --no-messages "$bs" refs/gate/pr 2>/dev/null | sed 1d | awk 'NF' | sort -u | head -8 | tr '\n' ' ')
  fail "merge conflict with $BASE in: ${cf:-?} (rebase the branch onto origin/$BASE and resolve these files)"; fi
# claims
# authoritative file list/diff from GitHub (independent of local history depth)
python -c "import json,sys;[print(f['path']) for f in json.loads(sys.argv[1])['files']]" "$j" > "$G/files.txt"
[ -s "$G/files.txt" ] || fail "empty file list"
if ! gh pr diff "$PR" -R $R > "$G/pr.diff"; then
  # REST call; under a rate limit take the same diff (merge base -> PR head) from the unshallowed local clone
  rl_hit || fail "pr diff"; git diff "$mb0" refs/gate/pr > "$G/pr.diff" || fail "pr diff (local)"; echo "pr diff: local git (GitHub API rate limit)" >> "$G/report.txt"; fi
# after a board drop the measured/merged change is the local drop head: take files and diff from git (board no longer in it)
if [ $BOARDDROP = 1 ]; then git diff --name-only "$mb0" refs/gate/pr > "$G/files.txt"; git diff "$mb0" refs/gate/pr > "$G/pr.diff"
  [ -s "$G/files.txt" ] || fail "PR only changed docs/AGENT_BOARD.md (board lines go directly to $BASE; nothing to merge)"; fi
sed -n 's/^hard //p' "$D/muse-claims.txt" > "$G/hard.re"; sed -n 's/^soft //p' "$D/muse-claims.txt" > "$G/soft.re"
grep '^@@ ' "$D/muse-claims.txt" | sed 's/^@@ //' > "$G/topics.re"
c=$(grep -Ef "$G/hard.re" "$G/files.txt" | head -5 | tr '\n' ' ')
[ -n "$c" ] && fail "touches hard-blocked path (recomp/oracle/s5verify/s5diff/.github; Opus/Codex review): $c"
# lane claims: a fresh (<6 h) Opus/Codex LANE-CLAIM line on the board naming a touched file keeps the PR draft
git show "origin/$BASE:docs/AGENT_BOARD.md" > "$G/board.md" 2>/dev/null || : > "$G/board.md"
# claim granularity (Dominic 2026-10-20 10:22): file::item claims hold only PRs that change that item; a plain file claim does not
# hold a PR opened before the claim once the owner pushed his work on that file (never merge onto possibly uncommitted work).
git fetch -q origin '+refs/heads/codex/*:refs/remotes/origin/codex/*' '+refs/heads/opus/*:refs/remotes/origin/opus/*' 2>/dev/null || true
prc=$(python -c "import json,sys;print(json.loads(sys.argv[1]).get('createdAt',''))" "$j")
LCARGS=(--owners Opus,Codex --hours 6 --repo "$S5_REPO" --diff "$G/pr.diff" --old-rev "$(git merge-base "$bs" refs/gate/pr)" --new-rev refs/gate/pr --pr-created "$prc")
# a Codex claim holds only while the Codex box loop is active (heartbeat, Dominic 2026-10-20 11:14; scripts/heartbeat.sh)
# (heartbeat.sh reads the asset download URL, no API call; "--heartbeat-unknown" = github.com unreachable)
read -r -a HB <<<"$("$D/heartbeat.sh" fetch "$G/heartbeat.json")"; LCARGS+=("${HB[@]}")
lc=$(python3 "$D/lane-claims.py" "$G/board.md" "$G/files.txt" "${LCARGS[@]}" 2>"$G/lc.err" | head -5 | tr '\n' ';')
grep -m3 '^stale claim ignored' "$G/lc.err" >> "$G/report.txt"
# heartbeat unknown + a claim that would hold: undecided -> DEFERRED under a rate limit (no FAIL verdict, no merge)
[ -n "$lc" ] && [ "${HB[0]:-}" = --heartbeat-unknown ] && rl_hit && defer "heartbeat fetch (claim hold undecided: $lc)"
[ -n "$lc" ] && fail "fresh lane claim on a touched file (kept draft; wait for expiry or ask the owner on the board): $lc"
soft=$(grep -Ef "$G/soft.re" "$G/files.txt" | head -5 | tr '\n' ' ')
t=$(grep '^+' "$G/pr.diff" | grep -cEf "$G/topics.re"); [ "$t" != 0 ] && soft="$soft topic-markers($t lines)"
echo "claims: ok ($(wc -l < "$G/files.txt") files)${soft:+; former claimed area touched, frontier move required: $soft}" >> "$G/report.txt"
# Opus decision 2026-10-18 20:40: scenario PRs merge only with a new code path / new unmodelled marker; frontier PRs must
# state live before -> after turn numbers. Checked from title/body/files before the (expensive) live suite.
# (an unreadable title/body used to become empty and could FAIL the before/after or new-code-path rule: now FAIL/DEFERRED explicitly)
gh pr view "$PR" -R $R --json title -q .title > "$G/title.txt" 2>/dev/null || apifail "pr title lookup"
gh pr view "$PR" -R $R --json body -q .body > "$G/body.txt" 2>/dev/null || apifail "pr body lookup"
if ! grep -qE '^crates/' "$G/files.txt" && { grep -qE '(^|/)(new-fixtures|fixtures-gen)/' "$G/files.txt" || grep -qiE '^[[:space:]]*(coverage|fixtures?|scenarios?)\b' "$G/title.txt"; }; then
  grep -qiE '^[[:space:]]*(new-code-path|new-unmodelled(-marker)?):[[:space:]]*[^[:space:]]' "$G/body.txt" ||
    fail "scenario/coverage/fixture PR without a new code path or new unmodelled marker (a pure stay-IDENTICAL extension no longer merges; add a body line 'New-code-path: <native address/subsystem first reached>' or 'New-unmodelled: <World::unmodelled marker>')"
  echo "scenario PR: body names a new code path / unmodelled marker" >> "$G/report.txt"
fi
if grep -qiE '^[[:space:]]*diverge\b' "$G/title.txt" || grep -qiE '^[[:space:]]*frontier-fixture:' "$G/body.txt"; then
  grep -qiE 'before[^0-9]{0,40}[0-9]+.{0,80}(->|→)[^0-9]{0,40}[0-9]+' "$G/body.txt" ||
    fail "frontier PR without live before/after turn numbers in the PR body (add 'live <fixture>: before turn X -> after turn Y', measured with s5-live.sh)"
  echo "frontier PR: body states live before -> after turns" >> "$G/report.txt"
fi
# optional extra frontier fixtures named in the PR body ("Frontier-fixture: gen-campaign"), only if installed on this runner
FIX="$S5_GATE_FIXTURES"
for x in $(cat "$G/body.txt" | grep -oiE '^[[:space:]]*frontier-fixture:[[:space:]]*[a-z0-9-]+' | sed -E 's/.*:[[:space:]]*//' | sort -u); do
  case " $FIX " in *" $x "*) continue ;; esac
  # install it (git fixtures-gen/<x> on the PR/base/fixture branches, or the private fixtures-gen-* release named in the body);
  # base and PR are both measured on it (base.cached never has it), so the no-regression rule covers it too
  inst=$("$D/s5-fixture-install.sh" "$x" "$G/body.txt" refs/gate/pr 2>/dev/null | tail -n1)
  if [ -f "$S5_NF/fixtures/$x/scenario.json" ]; then FIX="$FIX $x"; echo "extra frontier fixture: ${inst:-$x}" >> "$G/report.txt"
  elif rl_hit; then defer "Frontier-fixture $x install (release lookup)"
  else fail "Frontier-fixture $x is named in the PR body but could not be installed (${inst:-not installable}): a required fixture is never skipped"; fi
done
# subsystem rule (see header): plan from the diff/body/map before the expensive suite, run the tests after the build
if grep -qiE '^[[:space:]]*subsystem\b' "$G/title.txt"; then
  # coverage map: public release asset (download URL, no API call); unreadable -> only the map's flags (stricter, never weaker)
  curl -fsSL --max-time 60 -o "$G/coverage.md" https://github.com/Kofi55533/siedler-agent/releases/download/coverage-cache/COVERAGE-full.md 2>/dev/null || : > "$G/coverage.md"
  python3 "$D/subsys-xcheck.py" plan "$S5_REPO" "$bs" refs/gate/pr "$G/pr.diff" "$G/body.txt" "$G/coverage.md" > "$G/xcheck.txt" 2>/dev/null || fail "subsystem rule: subsys-xcheck.py failed"
  if ! grep -qx none "$G/xcheck.txt"; then
    xn=$(sed -n 's/^unexecuted //p' "$G/xcheck.txt" | wc -w)
    xm=$(sed -n 's/^missing //p' "$G/xcheck.txt" | head -3 | tr '\n' ';')
    [ -n "$xm" ] && fail "subsystem PR cites $xn function(s) no fixture executes, but: $xm"
    xh=$(grep -E '^crates/s5xcheck/(src/|Cargo\.toml$|build\.rs$)' "$G/files.txt" | head -3 | tr '\n' ' ')
    [ -n "$xh" ] && fail "subsystem PR relies on differential tests but changes the harness ($xh): harness changes go in a separate PR"
    XC=$(sed -n 's/^tests //p' "$G/xcheck.txt"); XCALL=$(sed -n 's/^all-unexecuted //p' "$G/xcheck.txt")
    # only cargo-test selection arguments (the body is untrusted input): --test <name>, --lib, --exact, filters
    for w in $XC; do case "$w" in --test|--lib|--exact|--|--nocapture|--include-ignored) ;; -*) fail "Differential-tests: argument '$w' not allowed (use --test <name>, --lib, --exact, test name filters)" ;;
      *) [[ "$w" =~ ^[A-Za-z0-9_:-]+$ ]] || fail "Differential-tests: argument '$w' not allowed" ;; esac; done
    echo "subsystem rule: $xn newly cited function(s) no fixture executes -> differential tests '$XC' required" >> "$G/report.txt"
  fi
fi
fi  # end of PR-only checks (baseline-only mode skips them)
# differential tests on the current checkout (merged candidate): exit 0, >= 1 passed, 0 failed
xcheck(){ local lk=0 rc p f; [ -f crates/s5xcheck/Cargo.toml ] || { echo "no crates/s5xcheck"; return 1; }
  [ -f crates/s5xcheck/Cargo.lock ] || { cp recomp/Cargo.lock crates/s5xcheck/Cargo.lock 2>/dev/null; lk=1; }
  set -f; CARGO_TARGET_DIR=$S5_REPO/recomp/target timeout -k 30 "${S5_XCHECK_TIMEOUT:-3600}" cargo test --release -q -j "$(nproc)" \
    --manifest-path crates/s5xcheck/Cargo.toml $XC > "$S5/gate-xcheck$1.log" 2>&1; rc=$?; set +f
  [ $lk = 1 ] && rm -f crates/s5xcheck/Cargo.lock
  read -r p f < <(grep -E '^test result' "$S5/gate-xcheck$1.log" | awk '{p+=$4;f+=$6} END{print p+0, f+0}')
  echo "exit=$rc, $p passed, $f failed"; [ $rc = 0 ] && [ "$p" -ge 1 ] && [ "$f" = 0 ]; }
# Typed live results (Codex strategy 2026-10-20 §4.3/§9): a run that yields neither "DIVERGENT turn N" nor "IDENTICAL N ticks"
# is ERROR/TIMEOUT, never turn 0: it gives no comparison and no PASS (base side -> DEFERRED as infrastructure, PR side -> FAIL).
# Every comparison is bound to the exact merged tree (receipt.tsv: fixture status base pr base_sha candidate_tree pr_head).
live_one(){ # $1=fixture $2=label: one s5-live run -> $G/$2.d/<fixture> "fixture turn secs" or $G/$2.d/<fixture>.err; fail-fast for label pr
  local f=$1 l=$2 t0 out rc n k b
  [ -e "$G/$l.STOP" ] && return 0
  t0=$(date +%s)
  out=$("$D/s5-live.sh" "$f" 2>/dev/null); rc=$?; n=$(grep -oE 'DIVERGENT turn [0-9]+' <<<"$out" | grep -oE '[0-9]+$' | head -n1)
  if [ -z "$n" ]; then k=$(grep -oE 'IDENTICAL [0-9]+ ticks' <<<"$out" | grep -oE '[0-9]+' | head -n1); [ -n "$k" ] && n=$((k+1000000)); fi
  [ -e "$G/$l.STOP" ] && return 0   # killed by fail-fast: not a measurement
  if ! [[ "$n" =~ ^[0-9]+$ ]] || [ "$n" -le 0 ]; then
    echo "$f $( [ $rc = 124 ] && echo TIMEOUT || echo ERROR ) exit=$rc secs=$(( $(date +%s)-t0 ))" > "$G/$l.d/$f.err"; return 0; fi
  echo "$f $n $(( $(date +%s)-t0 ))" > "$G/$l.d/$f"
  if [ "$l" = pr ] && [ "${S5_GATE_FAILFAST:-1}" = 1 ]; then
    b=$(awk -v f="$f" '$1==f{print $2}' "$G/base.tsv")
    if [ -n "$b" ] && [ "$n" -lt "$b" ]; then touch "$G/$l.STOP"; pkill -f 'target/release/s5verify live' 2>/dev/null; fi
  fi; }
export -f live_one; export D G S5_GATE_FAILFAST
# no verdict for an infrastructure reason (same DEFERRED protocol as the rate limit; refill re-gates DEFERRED PRs)
blocked(){ { echo "### Muse self-merge gate: DEFERRED (no verdict)"; echo '```'; cat "$G/report.txt"; echo "deferred: $*; merge criteria not evaluated, refill re-gates this PR"; echo '```'; } > "$G/comment.md"
  git checkout -q --detach "$bs" 2>/dev/null
  [ "${S5_GATE_COMMENT:-1}" = 1 ] && [ "${S5_GATE_BASELINE_ONLY:-0}" != 1 ] && gh pr comment "$PR" -R $R --body-file "$G/comment.md" >/dev/null 2>&1
  cat "$G/comment.md"; exit 3; }
suite(){ # $1=label [fixtures] -> $G/$1.tsv (fixture turn [secs]) in FIX order; longest first (secs of the cached baseline)
  local l=$1 fx=${2:-$FIX} f e
  rm -rf "$G/$l.d" "$G/$l.STOP"; mkdir -p "$G/$l.d"
  ( while sleep 10; do free -m | awk 'NR==2{print $3}'; done ) > "$G/mem-$l.txt" & local mp=$!
  local t0=$(date +%s)
  for f in $fx; do echo "$(awk -v f="$f" '$1==f{print $3+0}' "$G/base.cached" 2>/dev/null || echo 0) $f"; done | sort -rn | awk '{print $NF}' |
    xargs -P "$PAR" -I{} bash -c 'live_one "$1" "$2"' _ {} "$l"
  kill $mp 2>/dev/null
  : > "$G/$l.tsv"; for f in $fx; do [ -s "$G/$l.d/$f" ] && cat "$G/$l.d/$f" >> "$G/$l.tsv"; done
  local nr=$(wc -l < "$G/$l.tsv")
  echo "live $l: $nr/$(wc -w <<<"$fx") fixtures, parallel=$PAR, wall $(( $(date +%s)-t0 ))s, peak used RAM $(sort -n "$G/mem-$l.txt" | tail -n1)M" >> "$G/report.txt"
  e=$(cat "$G/$l.d/"*.err 2>/dev/null | head -5 | tr '\n' ';')
  if [ -n "$e" ]; then echo "live $l: no result for: $e" >> "$G/report.txt"
    [ "$l" = base ] && blocked "baseline live run produced no result ($e): a failed baseline is an infrastructure blocker, never turn 0"
    git checkout -q --detach "$bs"; fail "live $l: no result (ERROR/TIMEOUT) for $e - no comparison, no PASS"; fi
  # every fixture must have a result unless the PR side stopped on fail-fast (never treat a missing run as pass)
  [ "$nr" = "$(wc -w <<<"$fx")" ] || [ -e "$G/$l.STOP" ] || { git checkout -q --detach "$bs"; fail "live $l: only $nr of $(wc -w <<<"$fx") fixtures produced a result"; }; }
show(){ [ "$1" -ge 1000000 ] && echo "IDENT($(( $1-1000000 )))" || echo "$1"; }
measure_base(){ # baseline = $bs: cached per code tree (rows with turn 0 = legacy error sentinel are re-measured), else measured
  : > "$G/base.cached"; BK=""
  [ -z "$DV2" ] && [ "${S5_GATE_CACHE:-1}" = 1 ] && { S5_GATE_CACHE=0; echo "baseline cache off (data-v2 asset list unreadable; baseline measured here)" >> "$G/report.txt"; }
  if [ "${S5_GATE_CACHE:-1}" = 1 ]; then
    BK=$(bkey "$bs")
    gh release download $BREL -R $BREPO -p "base-$BK.tsv" -O "$G/base.raw" --clobber >/dev/null 2>&1 || : > "$G/base.raw"
    awk '$2 ~ /^[0-9]+$/ && $2 > 0' "$G/base.raw" > "$G/base.cached"
  fi
  miss=""; for f in $FIX; do grep -q "^$f " "$G/base.cached" || miss="$miss $f"; done
  if [ -n "${miss// /}" ]; then
    git checkout -q --detach "$bs" || fail "checkout base"
    "$D/s5-build.sh" >/dev/null || blocked "base build failed on ${bs:0:8} (infrastructure/base problem, not this PR)"
    suite base "$miss"
    cat "$G/base.cached" "$G/base.tsv" | awk '!s[$1]++' > "$G/base.all"
    if [ -n "$BK" ]; then
      awk -v fx=" $S5_GATE_FIXTURES " 'index(fx," "$1" ")' "$G/base.all" > "$G/base-$BK.tsv"   # cache only the standard matrix
      gh release view $BREL -R $BREPO >/dev/null 2>&1 || gh release create $BREL -R $BREPO --target main --prerelease -t $BREL \
        -n "s5-gate baseline live matrix per clean/integration code tree (s5-gate.sh). fixture turn secs; turn>=1000000 = IDENTICAL." >/dev/null 2>&1
      gh release upload $BREL -R $BREPO --clobber "$G/base-$BK.tsv" >/dev/null 2>&1 && echo "baseline cached as base-$BK" >> "$G/report.txt"
    fi
    mv "$G/base.all" "$G/base.tsv"
  else
    cp "$G/base.cached" "$G/base.tsv"; echo "baseline: cache hit base-$BK (code tree of ${bs:0:8})" >> "$G/report.txt"
  fi; }
evaluate(){ # candidate = PR merged onto $bs: build, s5clean tests, differential tests, live matrix, comparison -> $G/cmp.txt, moved
  git checkout -q --detach "$bs" || fail "checkout base"
  git -c user.name=gate -c user.email=gate@local merge -q --no-edit --no-ff refs/gate/pr >/dev/null 2>&1 || { git merge --abort; git checkout -q --detach "$bs"; fail "merge conflict with $BASE@${bs:0:8}"; }
  CT=$(git rev-parse 'HEAD^{tree}')
  echo "candidate: PR head ${hs:0:8} merged onto $BASE@${bs:0:8}, tree ${CT:0:12}" >> "$G/report.txt"
  "$D/s5-build.sh" >/dev/null || { git checkout -q --detach "$bs"; fail "build (s5verify) failed on the merged tree ${CT:0:12}"; }
  (cd crates/s5clean && cargo test -q -j "$(nproc)" > $S5/gate-test.log 2>&1); trc=$?
  tl=$(grep -E '^test result' $S5/gate-test.log | awk '{p+=$4;f+=$6} END{print p" passed, "f" failed"}')
  echo "s5clean tests: exit=$trc ($tl)" >> "$G/report.txt"
  [ $trc = 0 ] || { git checkout -q --detach "$bs"; fail "s5clean tests"; }
  if [ -n "$XC" ]; then xr=$(xcheck ""); xrc=$?; echo "differential tests ($XC): $xr" >> "$G/report.txt"
    [ $xrc = 0 ] || { git checkout -q --detach "$bs"; fail "differential tests for code no fixture executes not green ($xr)"; }; XCOK=1; fi
  suite pr
  git checkout -q --detach "$bs"
  reg=0; : > "$G/cmp.txt"
  for f in $FIX; do n0=$(awk -v f="$f" '$1==f{print $2}' "$G/base.tsv"); n1=$(awk -v f="$f" '$1==f{print $2}' "$G/pr.tsv")
    [[ "$n0" =~ ^[0-9]+$ ]] && [ "$n0" -gt 0 ] || blocked "no baseline result for $f on ${bs:0:8} (never compared against 0)"
    if [ -z "$n1" ]; then printf '%-22s base %-12s pr %-12s %s\n' "$f" "$(show $n0)" "-" "not run (fail-fast)" >> "$G/cmp.txt"; continue; fi
    if [ "$n1" -lt "$n0" ]; then st=REGRESSION; reg=1; elif [ "$n1" -gt "$n0" ]; then st=better; else st=same; fi
    printf '%-22s base %-12s pr %-12s %s\n' "$f" "$(show $n0)" "$(show $n1)" "$st" >> "$G/cmp.txt"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$f" "$st" "$n0" "$n1" "$bs" "$CT" "$hs" >> "$G/receipt.tsv"
  done
  cat "$G/cmp.txt" >> "$G/report.txt"
  [ -e "$G/pr.STOP" ] && reg=1
  # Opus decision 2026-10-18: no regression exception (the 2026-10-17 "Port:" rule is revoked) -> any earlier first divergence fails.
  [ $reg = 0 ] || fail "first divergence earlier on at least one fixture (no-regression rule, no Port: exception)"
  moved=$(grep -cE ' better$' "$G/cmp.txt")
  if [ -n "$soft" ]; then
    if [ "$moved" -gt 0 ]; then echo "former claimed area: frontier moved on $moved fixture(s), allowed" >> "$G/report.txt"
    elif [ "$XCOK" = 1 ] && [ "$XCALL" = 1 ]; then
      echo "former claimed area: no frontier move, but every newly cited logic function is unexecuted and its differential tests pass (subsystem rule), no fixture earlier" >> "$G/report.txt"
    else fail "touches a former claimed area ($soft) but moves no frontier"; fi
  fi; }
# Exact merged tree (Codex strategy 2026-10-20 §9.1): the verdict is only valid for the tree that is merged. If clean/integration
# moved with code changes during the gate, the live matrix is RE-MEASURED on the new merge (new baseline + new candidate), never
# reused; docs/*.md-only base moves keep the measurement (they are no build/test input: bkey excludes them the same way).
# After S5_GATE_BASE_TRIES (default 3) measurements against a still-moving base: DEFERRED (no verdict), refill re-gates.
: > "$G/receipt.tsv"; ATT=0
# S5_GATE_BUDGET_MIN (callers that wrap the gate in `timeout`): a re-measurement that would not finish inside the budget is
# DEFERRED instead of being killed by the timeout without a verdict.
while :; do
  ATT=$((ATT+1)); AT0=$(date +%s)
  measure_base
  if [ "${S5_GATE_BASELINE_ONLY:-0}" = 1 ]; then git checkout -q --detach "$bs"; cat "$G/report.txt"; cp "$G/base.tsv" "$S5/baseline-${bs:0:12}.tsv"; exit 0; fi
  : > "$G/receipt.tsv"
  evaluate
  nb=$(git ls-remote origin refs/heads/$BASE | cut -f1)
  [ -n "$nb" ] || blocked "cannot read $BASE head after the measurement"
  [ "$nb" = "$bs" ] && break
  git fetch -q origin "+refs/heads/$BASE:refs/remotes/origin/$BASE" || blocked "fetch moved base"
  nd=$(git diff --name-only "$bs" "$nb" | grep -vcE '^docs/(.*\.md|subsystems/(DS[0-9]{2}-[^/]+/[^/]+|domains\.json))$')
  if [ "$nd" = 0 ]; then echo "base moved to ${nb:0:8} (docs/*.md or docs/subsystems/DSnn-*/ ledgers only, no build/test input changed): measurement of ${CT:0:12} stands" >> "$G/report.txt"; break; fi
  echo "base moved ${bs:0:8} -> ${nb:0:8} with $nd code/data file(s): re-measuring the exact new merged tree (attempt $((ATT+1)))" >> "$G/report.txt"
  if [ "${S5_GATE_BUDGET_MIN:-0}" -gt 0 ] && [ $(( ( $(date +%s)-GT0 + $(date +%s)-AT0 ) / 60 + 2 )) -gt "${S5_GATE_BUDGET_MIN}" ]; then
    blocked "clean/integration moved with code changes; re-measuring the new merged tree does not fit the gate budget (${S5_GATE_BUDGET_MIN} min); a result for an older merged tree is not reused"; fi
  [ "$ATT" -ge "${S5_GATE_BASE_TRIES:-3}" ] && blocked "clean/integration kept moving with code changes during $ATT full measurements; a result for an older merged tree is not reused"
  bs=$nb
  git merge-tree --write-tree "$bs" refs/gate/pr >/dev/null 2>&1 || fail "merge conflict with moved $BASE@${bs:0:8}"
done
cp "$G/receipt.tsv" "$S5/gate-receipt-$PR.tsv" 2>/dev/null
# re-check lane claims on the newest board (a claim may have been posted during the ~75 min live suite)
git fetch -q origin "+refs/heads/$BASE:refs/remotes/origin/$BASE" 2>/dev/null && git show "origin/$BASE:docs/AGENT_BOARD.md" > "$G/board.md" 2>/dev/null
LCARGS=("${LCARGS[@]:0:${#LCARGS[@]}-${#HB[@]}}"); read -r -a HB <<<"$("$D/heartbeat.sh" fetch "$G/heartbeat.json")"; LCARGS+=("${HB[@]}")
lc=$(python3 "$D/lane-claims.py" "$G/board.md" "$G/files.txt" "${LCARGS[@]}" 2>/dev/null | head -5 | tr '\n' ';')
[ -n "$lc" ] && [ "${HB[0]:-}" = --heartbeat-unknown ] && rl_hit && defer "heartbeat fetch before merge (claim hold undecided: $lc)"
[ -n "$lc" ] && fail "fresh lane claim on a touched file appeared during the gate (kept draft): $lc"
if [ $DRY = 1 ]; then echo "all gates green" >> "$G/report.txt"; finish 0; fi
gh pr ready "$PR" -R $R >/dev/null 2>&1
gh pr merge "$PR" -R $R --squash --match-head-commit "$hs" >/dev/null 2>&1 || apifail "gh pr merge refused"
echo "merged into $BASE" >> "$G/report.txt"
# announce on the shared board (Opus > Codex > Muse); retry on concurrent pushes
t=$(gh pr view "$PR" -R $R --json title -q .title)
for i in 1 2 3; do
  [ -f .git/shallow ] && { git fetch -q --unshallow origin 2>/dev/null || git fetch -q --deepen=5000 origin; }
git fetch -q origin "+refs/heads/$BASE:refs/remotes/origin/$BASE" && git checkout -q --detach origin/$BASE || break
  # exactly one self-merge entry per PR (no duplicates from re-gates or retries)
  grep -qF "#$PR \`$h\` " docs/AGENT_BOARD.md && { echo "board: already announced" >> "$G/report.txt"; break; }
  printf '\n## %s Muse self-merge\n\n#%s `%s` (%s) squash-merged by s5-gate: build+s5clean tests green, no first divergence earlier.\n%s\n' \
    "$(TZ=Europe/Berlin date '+%Y-%m-%d %H:%M') Berlin —" "$PR" "$h" "$t" \
    "$(grep -E ' (better|same)$' "$G/cmp.txt" | awk '$NF=="better"{printf "%s %s->%s; ",$1,$3,$5}')" >> docs/AGENT_BOARD.md
  git -c user.name=siedler-agent -c user.email=Kofi55533@users.noreply.github.com commit -qam "board: Muse self-merge #$PR" &&
    git push -q origin HEAD:refs/heads/$BASE && { echo "board: announced" >> "$G/report.txt"; break; }
  git reset -q --hard origin/$BASE; done
finish 0
