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
# Never merges into main. Prints only fixture names and turn numbers (no log contents). Needs GH_TOKEN.
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
: > "$G/report.txt"
PAR=${S5_GATE_PARALLEL:-}; if [ -z "$PAR" ]; then
  m=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo); PAR=$(( m / ${S5_GATE_GB_PER_FIXTURE:-3} )); [ $PAR -gt $(nproc) ] && PAR=$(nproc); [ $PAR -lt 1 ] && PAR=1; fi
BREL=gate-baseline; BREPO=Kofi55533/siedler-agent
bkey(){ # $1 = commit -> cache key of its live matrix (fixture list is part of the file, missing fixtures are measured)
  { git ls-tree -r "$1" | grep -vP '\tdocs/.*\.md$'
    gh release view data-v2 -R Kofi55533/siedler-data --json assets -q '.assets[]|select(.name|test("tzst"))|"\(.name) \(.size) \(.updatedAt)"' | sort
    cat "$D/s5-live.sh" "$D/s5-env.sh" "$D/s5-build.sh"; echo "timeout=${S5_LIVE_TIMEOUT:-1800}"; } | sha256sum | cut -c1-16; }
if [ "${S5_GATE_BASELINE_ONLY:-0}" = 1 ]; then
  cd $S5_REPO; git fetch -q origin "+refs/heads/$BASE:refs/remotes/origin/$BASE" || fail "fetch"
  bs=$(git rev-parse origin/$BASE); FIX="$S5_GATE_FIXTURES"
  # S5_GATE_BASE_REF=<sha>: measure/cache that commit instead of the branch tip (post-hoc verification of a merge)
  if [ -n "${S5_GATE_BASE_REF:-}" ]; then git fetch -q --depth 2 origin "${S5_GATE_BASE_REF}" 2>/dev/null; bs=$(git rev-parse "${S5_GATE_BASE_REF}") || fail "unknown ref"; fi
  # S5_GATE_KEY_ONLY=1: print "<key> <base sha>" and whether the cache has it (warm-up pre-check, no build needed)
  if [ "${S5_GATE_KEY_ONLY:-0}" = 1 ]; then k=$(bkey "$bs")
    gh release view $BREL -R $BREPO --json assets -q '.assets[].name' 2>/dev/null | grep -qx "base-$k.tsv" && echo "$k $bs hit" || echo "$k $bs miss"; exit 0; fi
fi
if [ "${S5_GATE_BASELINE_ONLY:-0}" != 1 ]; then
j=$(gh pr view "$PR" -R $R --json baseRefName,headRefName,headRefOid,state,isCrossRepository,files) || fail "pr lookup"
read -r b h hs st x < <(python -c "import json,sys;d=json.loads(sys.argv[1]);print(d['baseRefName'],d['headRefName'],d['headRefOid'],d['state'],d['isCrossRepository'])" "$j")
[ "$b" = $BASE ] || fail "base is $b, not $BASE"
[ "$st" = OPEN ] && [ "$x" = False ] || fail "state=$st cross=$x"
case $h in agent/*) ;; *) fail "head $h is not agent/*" ;; esac
cd $S5_REPO
[ -f .git/shallow ] && { git fetch -q --unshallow origin 2>/dev/null || git fetch -q --deepen=5000 origin; }
git fetch -q origin "+refs/heads/$BASE:refs/remotes/origin/$BASE" "+refs/pull/$PR/head:refs/gate/pr" || fail "fetch"
bs=$(git rev-parse origin/$BASE); [ "$(git rev-parse refs/gate/pr)" = "$hs" ] || fail "head moved"
echo "pr=#$PR head=$h@${hs:0:8} base=$BASE@${bs:0:8}" >> "$G/report.txt"
# cheap conflict pre-check (same criterion as the merge below, but before the ~40 min baseline suite; 2026-10-18/19:
# 108 of 355 gates failed on a conflict only after the full baseline). Needs git >= 2.38 (merge-tree --write-tree).
if git merge-tree --write-tree "$bs" refs/gate/pr >/dev/null 2>&1; then :
elif [ $? = 1 ]; then fail "merge conflict with $BASE"; fi
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
  else echo "extra frontier fixture $x not installed on this runner (skipped)" >> "$G/report.txt"; fi
done
fi  # end of PR-only checks (baseline-only mode skips them)
live_one(){ # $1=fixture $2=label: one s5-live run -> $G/$2.d/<fixture> "fixture turn secs"; fail-fast vs $G/base.tsv for label pr
  local f=$1 l=$2 t0 out n k b
  [ -e "$G/$l.STOP" ] && return 0
  t0=$(date +%s)
  out=$("$D/s5-live.sh" "$f" 2>/dev/null); n=$(grep -oE 'DIVERGENT turn [0-9]+' <<<"$out" | grep -oE '[0-9]+$')
  if [ -z "$n" ]; then k=$(grep -oE 'IDENTICAL [0-9]+ ticks' <<<"$out" | grep -oE '[0-9]+'); [ -n "$k" ] && n=$((k+1000000)) || n=0; fi
  [ -e "$G/$l.STOP" ] && return 0   # killed by fail-fast: not a measurement
  echo "$f $n $(( $(date +%s)-t0 ))" > "$G/$l.d/$f"
  if [ "$l" = pr ] && [ "${S5_GATE_FAILFAST:-1}" = 1 ]; then
    b=$(awk -v f="$f" '$1==f{print $2}' "$G/base.tsv")
    if [ -n "$b" ] && [ "$n" -lt "$b" ]; then touch "$G/$l.STOP"; pkill -f 'target/release/s5verify live' 2>/dev/null; fi
  fi; }
export -f live_one; export D G S5_GATE_FAILFAST
suite(){ # $1=label [fixtures] -> $G/$1.tsv (fixture turn [secs]) in FIX order; longest first (secs of the cached baseline)
  local l=$1 fx=${2:-$FIX} f
  rm -rf "$G/$l.d" "$G/$l.STOP"; mkdir -p "$G/$l.d"
  ( while sleep 10; do free -m | awk 'NR==2{print $3}'; done ) > "$G/mem-$l.txt" & local mp=$!
  local t0=$(date +%s)
  for f in $fx; do echo "$(awk -v f="$f" '$1==f{print $3+0}' "$G/base.cached" 2>/dev/null || echo 0) $f"; done | sort -rn | awk '{print $NF}' |
    xargs -P "$PAR" -I{} bash -c 'live_one "$1" "$2"' _ {} "$l"
  kill $mp 2>/dev/null
  : > "$G/$l.tsv"; for f in $fx; do [ -s "$G/$l.d/$f" ] && cat "$G/$l.d/$f" >> "$G/$l.tsv"; done
  local nr=$(wc -l < "$G/$l.tsv")
  echo "live $l: $nr/$(wc -w <<<"$fx") fixtures, parallel=$PAR, wall $(( $(date +%s)-t0 ))s, peak used RAM $(sort -n "$G/mem-$l.txt" | tail -n1)M" >> "$G/report.txt"
  # every fixture must have a result unless the PR side stopped on fail-fast (never treat a missing run as pass)
  [ "$nr" = "$(wc -w <<<"$fx")" ] || [ -e "$G/$l.STOP" ] || { git checkout -q --detach "$bs"; fail "live $l: only $nr of $(wc -w <<<"$fx") fixtures produced a result"; }; }
show(){ [ "$1" -ge 1000000 ] && echo "IDENT($(( $1-1000000 )))" || echo "$1"; }
# baseline = current clean/integration: cached per code tree, else measured (and cached)
: > "$G/base.cached"; BK=""
if [ "${S5_GATE_CACHE:-1}" = 1 ]; then
  BK=$(bkey "$bs")
  gh release download $BREL -R $BREPO -p "base-$BK.tsv" -O "$G/base.cached" --clobber >/dev/null 2>&1 || : > "$G/base.cached"
fi
miss=""; for f in $FIX; do grep -q "^$f " "$G/base.cached" || miss="$miss $f"; done
if [ -n "${miss// /}" ]; then
  git checkout -q --detach "$bs" || fail "checkout base"
  "$D/s5-build.sh" >/dev/null || fail "base build"
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
fi
if [ "${S5_GATE_BASELINE_ONLY:-0}" = 1 ]; then git checkout -q --detach "$bs"; cat "$G/report.txt"; cp "$G/base.tsv" "$S5/baseline-${bs:0:12}.tsv"; exit 0; fi
git checkout -q --detach "$bs" || fail "checkout base"
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
for f in $FIX; do n0=$(awk -v f="$f" '$1==f{print $2}' "$G/base.tsv"); n1=$(awk -v f="$f" '$1==f{print $2}' "$G/pr.tsv")
  [ -n "$n0" ] || n0=0
  if [ -z "$n1" ]; then printf '%-22s base %-12s pr %-12s %s\n' "$f" "$(show $n0)" "-" "not run (fail-fast)" >> "$G/report.txt"; continue; fi
  if [ "$n1" -lt "$n0" ]; then s=REGRESSION; reg=1; elif [ "$n1" -gt "$n0" ]; then s=better; else s=same; fi
  printf '%-22s base %-12s pr %-12s %s\n' "$f" "$(show $n0)" "$(show $n1)" "$s" >> "$G/report.txt"
done
[ -e "$G/pr.STOP" ] && reg=1
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
    # with ~20 parallel runs the base moves during almost every 75-min gate: re-check merge + build + unit tests on the new base
    # (no second live suite; the live result above stands, later regressions are explained on the board)
    git checkout -q --detach "$nb" || fail "checkout moved base"
    git -c user.name=gate -c user.email=gate@local merge -q --no-edit --no-ff refs/gate/pr >/dev/null 2>&1 || { git merge --abort; git checkout -q --detach "$bs"; fail "merge conflict with moved $BASE"; }
    "$D/s5-build.sh" >/dev/null || { git checkout -q --detach "$bs"; fail "build on moved base failed"; }
    (cd crates/s5clean && cargo test -q -j "$(nproc)" > $S5/gate-test2.log 2>&1) || { git checkout -q --detach "$bs"; fail "s5clean tests on moved base"; }
    git checkout -q --detach "$bs"
    echo "base moved to ${nb:0:8} (code changed): merge+build+s5clean tests green on the new base" >> "$G/report.txt"
  fi
fi
# re-check lane claims on the newest board (a claim may have been posted during the ~75 min live suite)
git fetch -q origin "+refs/heads/$BASE:refs/remotes/origin/$BASE" 2>/dev/null && git show "origin/$BASE:docs/AGENT_BOARD.md" > "$G/board.md" 2>/dev/null
lc=$(python3 "$D/lane-claims.py" "$G/board.md" "$G/files.txt" --owners Opus,Codex --hours 6 2>/dev/null | head -5 | tr '\n' ';')
[ -n "$lc" ] && fail "fresh lane claim on a touched file appeared during the gate (kept draft): $lc"
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
