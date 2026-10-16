#!/usr/bin/env bash
# s5-gate.sh <pr-number> [--dry-run]
# Muse self-merge gate for Kofi55533/siedler-sim-rs PRs into clean/integration.
# Merges (squash) only if: base is clean/integration, head is agent/*, no claimed path/topic touched (muse-claims.txt),
# recomp s5verify builds, crates/s5clean cargo test passes, and for EVERY live fixture in $S5_GATE_FIXTURES the
# first-divergence turn of (PR merged onto current clean/integration) >= the same on clean/integration itself.
# Never merges into main. Prints only fixture names and turn numbers (no log contents). Needs GH_TOKEN.
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
: > "$G/report.txt"
j=$(gh pr view "$PR" -R $R --json baseRefName,headRefName,headRefOid,state,isCrossRepository,files) || fail "pr lookup"
read -r b h hs st x < <(python -c "import json,sys;d=json.loads(sys.argv[1]);print(d['baseRefName'],d['headRefName'],d['headRefOid'],d['state'],d['isCrossRepository'])" "$j")
[ "$b" = $BASE ] || fail "base is $b, not $BASE"
[ "$st" = OPEN ] && [ "$x" = False ] || fail "state=$st cross=$x"
case $h in agent/*) ;; *) fail "head $h is not agent/*" ;; esac
cd $S5_REPO
git fetch -q origin "+refs/heads/$BASE:refs/remotes/origin/$BASE" "+refs/pull/$PR/head:refs/gate/pr" || fail "fetch"
bs=$(git rev-parse origin/$BASE); [ "$(git rev-parse refs/gate/pr)" = "$hs" ] || fail "head moved"
echo "pr=#$PR head=$h@${hs:0:8} base=$BASE@${bs:0:8}" >> "$G/report.txt"
# claims
git diff --name-only "$bs...$hs" > "$G/files.txt"
grep -vE '^(#|@@|$)' "$D/muse-claims.txt" > "$G/paths.re"; grep '^@@ ' "$D/muse-claims.txt" | sed 's/^@@ //' > "$G/topics.re"
c=$(grep -Ef "$G/paths.re" "$G/files.txt" | head -5 | tr '\n' ' ')
[ -n "$c" ] && fail "touches claimed area (Opus/Codex): $c"
t=$(git diff "$bs...$hs" | grep '^+' | grep -cEf "$G/topics.re"); [ "$t" != 0 ] && fail "adds claimed topic markers (69657/extraction): $t lines"
echo "claims: ok ($(wc -l < "$G/files.txt") files)" >> "$G/report.txt"
suite(){ # $1=label -> $G/$1.tsv (fixture turn)
  : > "$G/$1.tsv"
  for f in $S5_GATE_FIXTURES; do
    out=$("$D/s5-live.sh" "$f" 2>/dev/null); n=$(grep -oE 'DIVERGENT turn [0-9]+' <<<"$out" | grep -oE '[0-9]+$')
    if [ -z "$n" ]; then k=$(grep -oE 'IDENTICAL [0-9]+ ticks' <<<"$out" | grep -oE '[0-9]+'); [ -n "$k" ] && n=$((k+1000000)) || n=0; fi
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
[ $reg = 0 ] || fail "first divergence earlier on at least one fixture"
[ "$(git ls-remote origin refs/heads/$BASE | cut -f1)" = "$bs" ] || fail "$BASE moved during gate; re-run"
if [ $DRY = 1 ]; then echo "all gates green" >> "$G/report.txt"; finish 0; fi
gh pr ready "$PR" -R $R >/dev/null 2>&1
gh pr merge "$PR" -R $R --squash --match-head-commit "$hs" >/dev/null 2>&1 || fail "gh pr merge refused"
echo "merged into $BASE" >> "$G/report.txt"
# announce on the shared board (Opus > Codex > Muse); retry on concurrent pushes
t=$(gh pr view "$PR" -R $R --json title -q .title)
for i in 1 2 3; do
  git fetch -q origin "+refs/heads/$BASE:refs/remotes/origin/$BASE" && git checkout -q --detach origin/$BASE || break
  printf '\n## %s Muse self-merge\n\n#%s `%s` (%s) squash-merged by s5-gate: build+s5clean tests green, no first divergence earlier.\n%s\n' \
    "$(TZ=Europe/Berlin date '+%Y-%m-%d %H:%M') Berlin —" "$PR" "$h" "$t" \
    "$(grep -E ' (better|same)$' "$G/report.txt" | awk '$NF=="better"{printf "%s %s->%s; ",$1,$3,$5}')" >> docs/AGENT_BOARD.md
  git -c user.name=siedler-agent -c user.email=Kofi55533@users.noreply.github.com commit -qam "board: Muse self-merge #$PR" &&
    git push -q origin HEAD:refs/heads/$BASE && { echo "board: announced" >> "$G/report.txt"; break; }
  git reset -q --hard origin/$BASE; done
finish 0
