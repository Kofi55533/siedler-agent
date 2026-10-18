#!/usr/bin/env bash
# close-stale-drafts.sh [--dry-run]  (Opus decision 2026-10-18, rule 4)
# Closes DRAFT PRs in Kofi55533/siedler-sim-rs that are older than $STALE_HOURS (48) hours AND either
#   (a) conflicted (REST mergeable_state == dirty), or
#   (b) move no boundary: at least one s5-gate comment ("### Muse self-merge gate") measured the live matrix and no
#       gate comment ever shows a fixture line ending in "better".
# PRs never gated/measured and not conflicted are kept (no evidence). Never touches PRs whose head starts with
# opus/, codex/ or haiku/, or whose base is not clean/integration. Comments the reason on each closed PR
# (gh pr close --comment). Never deletes branches. At most $STALE_MAX (25) closes per run. Needs GH_TOKEN.
set -uo pipefail
R=Kofi55533/siedler-sim-rs; BASE=clean/integration
DRY=0; [ "${1:-}" = --dry-run ] && DRY=1
H=${STALE_HOURS:-48}; MAX=${STALE_MAX:-25}
cut=$(date -u -d "-$H hours" +%FT%TZ)
L=$(gh pr list -R $R --state open --base $BASE --draft --limit 300 --json number,headRefName,baseRefName,createdAt,isDraft \
  -q ".[] | select(.isDraft and .baseRefName==\"$BASE\" and .createdAt < \"$cut\") | select(.headRefName | test(\"^(opus|codex|haiku)/\") | not) | \"\(.number) \(.headRefName) \(.createdAt)\"") \
  || { echo "pr list failed"; exit 1; }
echo "stale-drafts: $(printf '%s\n' "$L" | grep -c . ) draft PRs older than ${H}h (cutoff $cut, dry=$DRY)"
closed=0
while read -r n head created; do
  [ -z "${n:-}" ] && continue
  [ $closed -ge $MAX ] && { echo "cap $MAX reached"; break; }
  ms=$(gh api "repos/$R/pulls/$n" -q .mergeable_state 2>/dev/null)
  [ "$ms" = unknown ] && { sleep 5; ms=$(gh api "repos/$R/pulls/$n" -q .mergeable_state 2>/dev/null); }
  reason=
  if [ "$ms" = dirty ]; then
    reason="it has merge conflicts with \`$BASE\` (mergeable_state dirty)"
  else
    g=$(gh pr view "$n" -R $R --json comments -q '[.comments[].body | select(startswith("### Muse self-merge gate"))] | join("\n=====\n")' 2>/dev/null)
    meas=$(grep -cE ' base +[0-9IDENT()]+ +pr +[0-9IDENT()]+ +(same|better|REGRESSION)$' <<<"$g")
    better=$(grep -cE ' base +[0-9IDENT()]+ +pr +[0-9IDENT()]+ +better$' <<<"$g")
    [ "$meas" -gt 0 ] && [ "$better" = 0 ] && reason="the s5-gate live matrix measured it and it moves no boundary (no fixture's first divergence later)"
  fi
  if [ -z "$reason" ]; then echo "keep #$n $head (state=$ms)"; continue; fi
  if gh pr view "$n" -R $R --json comments -q '.comments[].body' 2>/dev/null | grep -qi 'needs-codex'; then
    echo "keep #$n $head (handed to Codex: needs-codex)"; continue; fi
  msg="Auto-closed by the stale-draft rule (Opus decision 2026-10-18): draft older than ${H} h and $reason. The branch \`$head\` is kept; reopen or rebase it if it moves a frontier (see siedler-agent issue \"Work split 2026-10-18 (Opus decision)\")."
  if [ $DRY = 1 ]; then echo "WOULD CLOSE #$n $head created=$created: $reason"
  else gh pr close "$n" -R $R --comment "$msg" >/dev/null && echo "closed #$n $head: $reason" || echo "close #$n failed"; fi
  closed=$((closed+1))
done <<<"$L"
echo "stale-drafts: $closed $([ $DRY = 1 ] && echo would-close || echo closed)"
