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
The goal is the same as Opus': a bit-identical clean Rust port (s5clean) of Settlers 5, verified turn by turn against the
reference. You can load any map, run any mechanic, generate your own command streams/fixtures on the runner and test
everything. Decide yourself, smartly, which mix of tasks moves the project fastest right now: fixing known first
divergences, deeper angles, or new targeted fixtures that expose untested mechanics. Justify the mix in one line of
summary.txt.
0. Open DS-REQ board entries (section 0), always first.
1. `docs/agent-queue/opus-*` items not yet started, then other (Codex) queue items not yet started. For a queue item, the task text is its file content
   (you may NOT move queue files yourself; the run that takes the task claims/moves it).
Then fill the remaining slots (use all N with distinct tasks) from any mix of:
- Boundary-moving WIRED fixes: one task per scenario whose current first divergence is not covered by an active run/open issue (last 12h)/open PR.
- Clearly different angles on the same boundary (e.g. a different subsystem/record at that turn, an earlier deep-diff field visible before the live
  boundary, wiring an already-merged unwired helper into World tick/dispatch, reworking a PR that Codex/Opus commented on).
- COVERAGE tasks (optional, no quota): generate new reference fixtures on the runner with targeted player command streams for a game
  area that is not yet covered or where the clean port is suspected weak (e.g. taxes/levies 69646, alarm 69680/81, research 69658,
  trade, upgrades, serf buy, military move/attack/recruit, building placement incl. invalid ones, campaign maps, other MP maps),
  using the documented tooling in `docs/clean/new-fixtures/RUNNER.md` and the generators from PRs #411/#412/#413
  (`tools/genb_fixtures.py`, `docs/clean/new-fixtures/gen_commands.py`, gen-c). The run exports per-turn reference state, uploads
  as private release asset `fixtures-gen-<date>-<topic>` in Kofi55533/siedler-sim-rs (or as the earlier gen runs did), runs
  s5clean live, posts first divergence + root-cause hint on the board, and if the cause is small and unclaimed, lands a wired fix.
  Coverage tasks must each pick a distinct area/map from each other and from active runs.
- FUNCTION-BACKLOG tasks (fixed share, Dominic 17.10.2026 21:12): about one third of N (with N=19: 6) must work through native
  logic functions of SettlersHoK.exe that are not yet ported in s5clean. Sources: the "uncited" ranking in
  `docs/clean/COVERAGE.md` (origin/clean/integration) first, then the full logic list
  `/home/runner/work/siedler-agent/siedler-agent/scripts/scoreboard/function_inventory.json` (29,723 logic functions;
  ported = its native address `0x[0-9A-F]{6}` cited in `crates/s5clean/src`). Each task takes ONE coherent group (one class or
  subsystem, e.g. one CxxBehavior's handlers, 5-25 functions), named with addresses, and must: port them cleanly from the Ghidra
  decompile (siedler-forschung `cloud-re/results/rust_handoff/source/functions.jsonl`) cross-checked against `recomp/` (the lifted
  reference is the truth where they differ), cite each native address in the code, add tests that compare against the reference
  (recomp/ behaviour or reference fixture state) wherever possible, wire them into World tick/dispatch once they are bit-exact against
  recomp/ (that is the acceptance criterion); if a live scenario then diverges earlier, find and post the real cause on the board
  (usually another missing function) instead of holding back a bit-exact port, and land a PR. Groups must not overlap with each other, active runs, open PRs or Opus' claimed areas (board/claims);
  prefer groups that run early in the game (COVERAGE first-turn) and are not part of combat-167 formation code.
Every task must be DISTINCT from each other and from every active run, every open issue of the last 12h and every open agent/* PR
(compare fixture + turn + subsystem; for coverage: area/map). If you cannot find N genuinely distinct tasks, create fewer. Never invent topics from other Settlers games
(S5 has no bakery/woodcutter/forester); cite recomp/ functions or game data when naming a subsystem.
Anti-stuck: before creating a task for a fixture+boundary, check recent issues/runs (last ~24h) on the same fixture+turn. If 2–3 runs already ended without moving that boundary, do NOT create another task for it; instead post a short handoff line on `docs/AGENT_BOARD.md` addressed to Opus (fixture, turn, what was tried, links) and use the slot for something else (different boundary or a new targeted fixture).

## 3. Rules every task text must carry (state them in the task)
- Goal: a bit-identical clean Rust port (s5clean) of Settlers 5, verified turn by turn against the reference. You may choose your own approach, including generating new fixtures, if it serves the goal.
- Work in Kofi55533/siedler-sim-rs, branch `agent/<short-topic>` based on origin/clean/integration (or on a named open PR), DRAFT PR to `clean/integration`. Never main, never merge manually.
- Land a WIRED fix (hooked into World tick/dispatch) that moves the named fixture's first divergence later; no doc-only/analysis-only PR, no unwired helpers.
  (Coverage / new-fixture tasks: generate fixtures + s5clean live + board post first; wired fix only if the cause is small and unclaimed.)
- Never edit recomp/. Files/areas claimed in scripts/muse-claims.txt or by Opus/Codex lanes on the board: do NOT edit them; put the change as a patch in the PR body + a wiring request on docs/AGENT_BOARD.md (helper, call site, fixture+turn).
- Never change, revert or overwrite code that Opus pushed; Opus has final say (Opus > Codex > Muse). Codex instructions override Muse.
- The four combat scenarios at turn 167 (formation solver 0x4F7962, states 14/15, leader state 22) are Opus' domain — do not create further combat-167 boundary tasks unless a DS-REQ asks for it.
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
Write to $W/summary.txt (max 5 lines): `planner: created #a #b ...` (list `DS-REQ <id> -> #n` first) plus one line justifying the mix (boundaries / angles / coverage) and why fewer than N if so.
