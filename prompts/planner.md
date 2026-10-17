# PLANNER RUN (task generator, not a coding run)

This run overrides the generic "every run MUST target a divergence / write code" instruction of the wrapper:
you write NO code, push NO branch, open NO PR, and do NOT edit siedler-sim-rs, with ONE exception: the DS-REQ
markers in `docs/AGENT_BOARD.md` on clean/integration (section 0).
Your only output is N new task issues in Kofi55533/siedler-agent, each started with a `/opencode` comment.
N is given in the trigger comment as `N=<number>` (default 3, hard max 15).

## 0. DS-REQ first (top priority, before inventing anything)
Before choosing any other task, read `git show origin/clean/integration:docs/AGENT_BOARD.md`, section
`## Für DeepSeek (Aufträge von Opus)`. Every line `- [ ] DS-REQ <id>: <task> (from Opus, <date>)` is an open request
from Opus (or Codex) and MUST become a task issue in this run, ahead of everything in section 2 (they count towards N;
if there are more open DS-REQs than N, take them oldest first; never skip one for an own idea).
- Skip a DS-REQ only if it is already `[~]`/`[x]` or an open issue/active run already carries its id in the title.
- Title: `DS-REQ <id>: <short task>`; the `/opencode` comment carries the full request text verbatim plus the context/rules
  of sections 3-4, and tells the run to mark the board line `[x] #<issue>` (optionally `-> PR #<n>` / one-line result) when done.
- After creating the issue, mark the board line started: change only `- [ ]` to `- [~] #<issue>` (never edit the request text):
  ```
  cd /opt/s5/siedler-sim-rs && git fetch -q origin clean/integration && git checkout -q -B ds-board origin/clean/integration
  # edit docs/AGENT_BOARD.md: only the marker(s) of the started DS-REQ line(s)
  git commit -qam "board: DS-REQ <ids> started (#<issues>)" && git push -q origin HEAD:clean/integration
  ```
  On a push rejection: `git pull --rebase origin clean/integration` and push again (max 3 tries). Never force-push, never touch other lines or files.
- Also read the recent Codex and Opus board entries (last ~24h) before inventing own tasks: their findings, wiring requests,
  handoffs and requests addressed to DeepSeek/Muse take precedence over own ideas and must not be duplicated.

## 1. Gather state (read-only)
Use `gh` (GH_TOKEN is set) and the local checkout /opt/s5/siedler-sim-rs (`git fetch -q origin clean/integration`).
- `git show origin/clean/integration:docs/AGENT_BOARD.md` (read the last ~200 lines carefully: claims, lanes, wiring requests, Opus/Codex notes).
- `docs/agent-queue/*.md` and `docs/agent-queue/started/` on origin/clean/integration; `docs/OPUS_NOTES.md` if present.
- Policy: `/home/runner/work/siedler-agent/siedler-agent/scripts/muse-claims.txt` (claimed paths) and the rules below.
- Open PRs: `gh pr list -R Kofi55533/siedler-sim-rs --state open --limit 200 --json number,title,headRefName,baseRefName,author,updatedAt`.
- Open/recent issues: `gh issue list -R Kofi55533/siedler-agent --state all --limit 80 --json number,title,state,createdAt`.
- Active runs: `gh run list -R Kofi55533/siedler-agent -w opencode.yml --limit 60 --json status,displayTitle` (status queued/in_progress = active task; its displayTitle is the issue title).
- Current first-divergence boundaries per live scenario (bc-full wintersturm economy wsecon build production combat combat-waves
  combat-big combat-long ai-long late-ai late-economy camp-crawford map-winterisland): take the latest numbers from the board.
  If a number is missing or stale and time allows, measure: `source $S5_SCRIPTS/s5-env.sh; $S5_SCRIPTS/s5-live.sh <fixture>` (never paste log contents anywhere).

## 2. Choose N tasks (priority order)
0. Open DS-REQ board entries (section 0), always first.
1. `docs/agent-queue/opus-*` items not yet started, then other (Codex) queue items not yet started. For a queue item, the task text is its file content
   (you may NOT move queue files yourself; the run that takes the task claims/moves it).
2. Boundary-moving WIRED fixes: one task per scenario whose current first divergence is not covered by an active run/open issue (last 12h)/open PR.
3. Clearly different angles on the same boundary (e.g. a different subsystem/record at that turn, an earlier deep-diff field visible before the live
   boundary, wiring an already-merged unwired helper into World tick/dispatch, reworking a PR that Codex/Opus commented on).
Every task must be DISTINCT from each other and from every active run, every open issue of the last 12h and every open agent/* PR
(compare fixture + turn + subsystem). If you cannot find N genuinely distinct tasks, create fewer. Never invent topics from other Settlers games
(S5 has no bakery/woodcutter/forester); cite recomp/ functions or game data when naming a subsystem.

## 3. Rules every task text must carry (state them in the task)
- Work in Kofi55533/siedler-sim-rs, branch `agent/<short-topic>` based on origin/clean/integration (or on a named open PR), DRAFT PR to `clean/integration`. Never main, never merge manually.
- Land a WIRED fix (hooked into World tick/dispatch) that moves the named fixture's first divergence later; no doc-only/analysis-only PR, no unwired helpers.
- Never edit recomp/. Files/areas claimed in scripts/muse-claims.txt or by Opus/Codex lanes on the board: do NOT edit them; put the change as a patch in the PR body + a wiring request on docs/AGENT_BOARD.md (helper, call site, fixture+turn).
- Never change, revert or overwrite code that Opus pushed; Opus has final say (Opus > Codex > Muse). Codex instructions override Muse.
- Read docs/AGENT_BOARD.md first, post a claim line, post real findings/handoffs; agents may talk to each other freely on the board.
- s5-live before and after; report `live <fixture>: before X -> after Y`.

## 4. Task format (follow exactly, like earlier refill tasks)
- Title: one line, specific, e.g. `Diverge wsecon-233: <subsystem> root cause and wired fix at the live first divergence`.
- Issue body: ONE short generic sentence (e.g. `Muse task (planner). Details in the /opencode comment.`) — no private code, no log content.
- Comment: starts with `/opencode ` then one dense paragraph: target fixture + current turn, angle, what to trace read-only in recomp/,
  base branch/PR, branch name, success criterion; then a ` Context (re-measure yourself, board = docs/AGENT_BOARD.md on clean/integration,
  read it first and post your claim/result there): ...` paragraph listing latest relevant merges and open drafts not to duplicate; then the rules of section 3 in short form.

## 5. Create and start them
For each task:
```
n=$(gh issue create -R Kofi55533/siedler-agent --label planner-task --title "$TITLE" --body "Muse task (planner). Details in the /opencode comment." | grep -oE '[0-9]+$')
gh issue comment "$n" -R Kofi55533/siedler-agent --body-file comment.txt     # comment.txt starts with "/opencode "
```
Create them sequentially, verify each comment exists (`gh issue view $n -R Kofi55533/siedler-agent --comments`). Do not comment `/opencode` on this planner issue or on any other existing issue.
If `gh issue comment` fails, still keep the issue and append the full comment text to the issue body under a line `---PLANNER-COMMENT---`
(`gh issue edit $n --body-file ...`); the box refill script will post it.

## 6. Summary
Write to $W/summary.txt (max 5 lines): `planner: created #a #b ...` (list `DS-REQ <id> -> #n` first) plus one line on which boundaries were used and why fewer than N if so.
