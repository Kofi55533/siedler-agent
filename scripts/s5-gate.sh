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
# (title "Diverge ..." or a "Frontier-fixture:" line) needs live before -> after turn numbers in its body. A changed code base requires a fresh full gate (exit 75).
# Never merges into main. Prints only fixture names and turn numbers (no log contents). Needs GH_TOKEN.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); source "$D/s5-env.sh"
PR=$1; DRY=0; [ "${2:-}" = --dry-run ] && DRY=1
R=Kofi55533/siedler-sim-rs; BASE=clean/integration
G=$S5/gate/$PR; rm -rf "$G"; mkdir -p "$G"
fail(){ echo "GATE FAIL: $*" | tee -a "$G/report.txt"; finish 1; }
retry(){ echo "GATE RETRY: $*" | tee -a "$G/report.txt"; finish 75; }
finish(){
  local rc=$1 v; [ $rc = 0 ] && v=PASS || v=FAIL; [ $DRY = 1 ] && v="$v (dry-run, no merge)"
  { echo "### Muse self-merge gate: $v"; echo '```'; cat "$G/report.txt"; echo '```'; } > "$G/comment.md"
  [ "${S5_GATE_COMMENT:-1}" = 1 ] && gh pr comment "$PR" -R $R --body-file "$G/comment.md" >/dev/null 2>&1
  cat "$G/comment.md"; exit $rc; }
: > "$G/report.txt"
j=$(gh pr view "$PR" -R $R --json baseRefName,headRefName,headRefOid,state,isCrossRepository,files) || fail "pr lookup"
read -r b h hs st x < <(python -c "import json,sys;d=json.loads(sys.argv[1]);print(d['baseRefName'],d['headRefName'],d['headRefOid'],d['state'],d['isCrossRepository'])" "$j")
[ -z "${S5_GATE_EXPECT_HEAD:-}" ] || [ "$hs" = "$S5_GATE_EXPECT_HEAD" ] || fail "head differs from the queued worker result"
[ "$b" = $BASE ] || fail "base is $b, not $BASE"
[ "$st" = OPEN ] && [ "$x" = False ] || fail "state=$st cross=$x"
case $h in agent/*) ;; *) fail "head $h is not agent/*" ;; esac
cd $S5_REPO
[ -f .git/shallow ] && { git fetch -q --unshallow origin 2>/dev/null || git fetch -q --deepen=5000 origin; }
git fetch -q origin "+refs/heads/$BASE:refs/remotes/origin/$BASE" "+refs/pull/$PR/head:refs/gate/pr" || fail "fetch"
bs=$(git rev-parse origin/$BASE); [ "$(git rev-parse refs/gate/pr)" = "$hs" ] || fail "head moved"
echo "pr=#$PR head=$h@${hs:0:8} base=$BASE@${bs:0:8}" >> "$G/report.txt"
# claims
# authoritative file list/diff from GitHub (independent of local history depth)
python -c "import json,sys;[print(f['path']) for f in json.loads(sys.argv[1])['files']]" "$j" > "$G/files.txt"
[ -s "$G/files.txt" ] || fail "empty file list"
gh pr diff "$PR" -R $R > "$G/pr.diff" || fail "pr diff"
sed -n 's/^hard //p' "$D/muse-claims.txt" > "$G/hard.re"; sed -n 's/^soft //p' "$D/muse-claims.txt" > "$G/soft.re"
grep '^@@ ' "$D/muse-claims.txt" | sed 's/^@@ //' > "$G/topics.re"
c=$(grep -Ef "$G/hard.re" "$G/files.txt" | head -5 | tr '\n' ' ')
[ -n "$c" ] && fail "touches hard-blocked path (recomp/oracle/s5verify/s5diff/.github; Opus/Codex review): $c"
# lane claims: a fresh (<6 h) Opus/Codex LANE-CLAIM line on the board naming a touched file keeps the PR draft
git show "origin/$BASE:docs/AGENT_BOARD.md" > "$G/board.md" 2>/dev/null || : > "$G/board.md"
lc=$(python3 "$D/lane-claims.py" "$G/board.md" "$G/files.txt" --owners Opus,Codex --hours 6 2>/dev/null | head -5 | tr '\n' ';')
[ -n "$lc" ] && fail "fresh lane claim on a touched file (kept draft; wait for expiry or ask the owner on the board): $lc"
soft=$(grep -Ef "$G/soft.re" "$G/files.txt" | head -5 | tr '\n' ' ')
t=$(grep '^+' "$G/pr.diff" | grep -cEf "$G/topics.re"); [ "$t" != 0 ] && soft="$soft topic-markers($t lines)"
echo "claims: ok ($(wc -l < "$G/files.txt") files)${soft:+; former claimed area touched, frontier move required: $soft}" >> "$G/report.txt"
# Opus decision 2026-10-18 20:40: scenario PRs merge only with a new code path / new unmodelled marker; frontier PRs must
# state live before -> after turn numbers. Checked from title/body/files before the (expensive) live suite.
gh pr view "$PR" -R $R --json title -q .title > "$G/title.txt" 2>/dev/null || : > "$G/title.txt"
gh pr view "$PR" -R $R --json body -q .body > "$G/body.txt" 2>/dev/null || : > "$G/body.txt"
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
  if [ -f "$S5_NF/fixtures/$x/scenario.json" ]; then FIX="$FIX $x"; echo "extra frontier fixture: $x" >> "$G/report.txt"
  else fail "required frontier fixture $x is unavailable; cannot verify this result"; fi
done
suite(){ # $1=label -> $G/$1.tsv (fixture turn)
  : > "$G/$1.tsv"
  for f in $FIX; do
    out=$("$D/s5-live.sh" "$f" 2>/dev/null); rc=$?
    n=$(grep -oE 'DIVERGENT turn [0-9]+' <<<"$out" | grep -oE '[0-9]+$')
    if [ -n "$n" ]; then
      [ "$rc" = 1 ] || fail "invalid divergent verdict for $f (exit=$rc)"
    else
      k=$(grep -oE 'IDENTICAL [0-9]+ ticks' <<<"$out" | grep -oE '[0-9]+')
      [ "$rc" = 0 ] && [ -n "$k" ] || fail "missing/failed live verdict for $f (exit=$rc)"
      n=$((k+1000000))
    fi
    echo "$f $n" >> "$G/$1.tsv"; done; }
show(){ [ "$1" -ge 1000000 ] && echo "IDENT($(( $1-1000000 )))" || echo "$1"; }
# baseline = current clean/integration
git checkout -q --detach "$bs" || fail "checkout base"
"$D/s5-build.sh" >/dev/null || fail "base build"
suite base
# candidate = PR merged onto current base
git -c user.name=gate -c user.email=gate@local merge -q --no-edit --no-ff refs/gate/pr >/dev/null 2>&1 || { git merge --abort; fail "merge conflict with $BASE"; }
"$D/s5-build.sh" >/dev/null || { git checkout -q --detach "$bs"; fail "build (s5verify) failed"; }
(cd crates/s5clean && cargo test -q -j "$(nproc)" > $S5/gate-test.log 2>&1); trc=$?
tl=$(grep -E '^test result' $S5/gate-test.log | awk '{p+=$4;f+=$6} END{print p" passed, "f" failed"}')
echo "s5clean tests: exit=$trc ($tl)" >> "$G/report.txt"
[ $trc = 0 ] || { git checkout -q --detach "$bs"; fail "s5clean tests"; }
suite pr
git checkout -q --detach "$bs"
reg=0
while read -r f n0; do n1=$(awk -v f="$f" '$1==f{print $2}' "$G/pr.tsv")
  if [ "$n1" -lt "$n0" ]; then s=REGRESSION; reg=1; elif [ "$n1" -gt "$n0" ]; then s=better; else s=same; fi
  printf '%-22s base %-12s pr %-12s %s\n' "$f" "$(show $n0)" "$(show $n1)" "$s" >> "$G/report.txt"
done < "$G/base.tsv"
# Opus decision 2026-10-18: no regression exception (the 2026-10-17 "Port:" rule is revoked) -> any earlier first divergence fails.
[ $reg = 0 ] || fail "first divergence earlier on at least one fixture (no-regression rule, no Port: exception)"
moved=$(grep -cE ' better$' "$G/report.txt")
if [ -n "$soft" ]; then
  [ "$moved" -gt 0 ] || fail "touches a former claimed area ($soft) but moves no frontier"
  echo "former claimed area: frontier moved on $moved fixture(s), allowed" >> "$G/report.txt"
fi
# base moved meanwhile: still valid if every new base commit only touched docs/*.md (board, notes) -> measured code unchanged
nb=$(git ls-remote origin refs/heads/$BASE | cut -f1)
if [ "$nb" != "$bs" ]; then
  git fetch -q origin "+refs/heads/$BASE:refs/remotes/origin/$BASE" || fail "fetch moved base"
  nd=$(git diff --name-only "$bs" "$nb" | grep -vcE '^docs/.*\.md$')
  if [ "$nd" = 0 ]; then
    echo "base moved to ${nb:0:8} (docs/*.md only, code unchanged)" >> "$G/report.txt"
  else
    retry "code base moved to ${nb:0:8}; repeat the full live matrix on that base"
  fi
fi
# re-check lane claims on the newest board (a claim may have been posted during the ~75 min live suite)
git fetch -q origin "+refs/heads/$BASE:refs/remotes/origin/$BASE" 2>/dev/null || fail "cannot refresh final lane claims"
git show "origin/$BASE:docs/AGENT_BOARD.md" > "$G/board.md" 2>/dev/null || fail "cannot read final lane claims"
lc=$(python3 "$D/lane-claims.py" "$G/board.md" "$G/files.txt" --owners Opus,Codex --hours 6 2>/dev/null | head -5 | tr '\n' ';')
[ -n "$lc" ] && fail "fresh lane claim on a touched file appeared during the gate (kept draft): $lc"
[ "$(git ls-remote origin refs/heads/$BASE | cut -f1)" = "$nb" ] || retry "base moved during final checks"
if [ $DRY = 1 ]; then echo "all gates green" >> "$G/report.txt"; finish 0; fi
gh pr ready "$PR" -R $R >/dev/null 2>&1
gh pr merge "$PR" -R $R --squash --match-head-commit "$hs" >/dev/null 2>&1 || fail "gh pr merge refused"
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
    "$(grep -E ' (better|same)$' "$G/report.txt" | awk '$NF=="better"{printf "%s %s->%s; ",$1,$3,$5}')" >> docs/AGENT_BOARD.md
  git -c user.name=siedler-agent -c user.email=Kofi55533@users.noreply.github.com commit -qam "board: Muse self-merge #$PR" &&
    git push -q origin HEAD:refs/heads/$BASE && { echo "board: announced" >> "$G/report.txt"; break; }
  git reset -q --hard origin/$BASE; done
finish 0
