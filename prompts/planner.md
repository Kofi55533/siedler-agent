# PLANNER RUN (task generator, not a coding run)

This run overrides the generic "every run MUST target a divergence / write code" instruction of the wrapper:
you write NO code, push NO branch, open NO PR, and do NOT edit siedler-sim-rs, with ONE exception: the DS-REQ
markers in `docs/AGENT_BOARD.md` on clean/integration (section 0).
Your only output is N new task issues in Kofi55533/siedler-agent, each started with a `/opencode` comment.
N is given in the trigger comment as `N=<number>` (default 3, hard max 15).

## 0. DS-REQ first (top priority, before inventing anything)
Before choosing any other task, read `git show origin/clean/integration:docs/AGENT_BOARD.md`, section
`## Für DeepSeek (Aufträge von Opus)`. Every line `- [ ] DS-REQ <id>: <task> (from Opus, <date>)` is an open request
from Opus (or Codex) and MUST become a task issue in this run, ahead of everything in section 2 (they count towards N
and against the 10% DS-REQ/hygiene bucket of section 2; if there are more open DS-REQs than that bucket, the extra ones
take slots from the 50% frontier-diagnosis bucket; if there are more than N, take them oldest first; never skip one for an own idea).
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
- Policy: `/home/runner/work/siedler-agent/siedler-agent/scripts/muse-claims.txt` (`hard` = never auto-merged; `soft` = former
  claimed areas, merge needs a frontier move) and the fresh `LANE-CLAIM` lines on the board (format in section 3).
- Open PRs: `gh pr list -R Kofi55533/siedler-sim-rs --state open --limit 200 --json number,title,headRefName,baseRefName,author,updatedAt`.
- Open/recent issues: `gh issue list -R Kofi55533/siedler-agent --state all --limit 80 --json number,title,state,createdAt`.
- Active runs: `gh run list -R Kofi55533/siedler-agent -w opencode.yml --limit 60 --json status,displayTitle` (status queued/in_progress = active task; its displayTitle is the issue title).
- Open PR files (for dedupe): for each open agent/* PR, `gh pr view <n> -R Kofi55533/siedler-sim-rs --json title,body,files`
  (native addresses `0x[0-9A-F]{6}` in title/body = functions already covered).
- Refresh the coverage view EVERY planner run (local only, never commit it):
  `cd /opt/s5/siedler-sim-rs && git checkout -q --detach origin/clean/integration && SKIP_BC_FULL=1 OUT=$W/COVERAGE.md timeout 45m tools/coverage_map.sh`
  (block-traces the reference with `s5recomp TRACE=... TRACE_TURNS=...` and aggregates with `tools/coverage_map.py`). If it fails or
  times out, use the committed `docs/clean/COVERAGE.md`, but in BOTH cases re-check "ported" yourself: a function counts as ported if its
  address is cited anywhere in `crates/s5clean/src` on origin/clean/integration (`git grep -i <addr> origin/clean/integration -- crates/s5clean/src`).
- Current first-divergence boundaries per live scenario (bc-full wintersturm economy wsecon build production combat combat-waves
  combat-big combat-long ai-long late-ai late-economy camp-crawford map-winterisland): take the latest numbers from the board.
  If a number is missing or stale and time allows, measure: `source $S5_SCRIPTS/s5-env.sh; $S5_SCRIPTS/s5-live.sh <fixture>` (never paste log contents anywhere).

## 2. Choose N tasks (work split, Opus decision 2026-10-18)
The goal is the same as Opus': a bit-identical clean Rust port (s5clean) of Settlers 5, verified turn by turn against the
reference. Progress = FRONTIERS (first-divergence turns of the live scenarios and generated fixtures) moving later.
Split N into four buckets: floor(0.5N) frontier diagnosis, floor(0.2N) frontier-reached ports, floor(0.2N) new/longer
scenarios, floor(0.1N) DS-REQs + hygiene; give the remaining slots in that order (diagnosis, ports, scenarios, hygiene).
Example N=19: 10 / 4 / 4 / 1. Open DS-REQs (section 0) are created first and count in the 10% bucket (overflow: diagnosis bucket).
Queue items (`docs/agent-queue/`) are used only if they fit one of the buckets and count there. If a bucket has no qualifying
task, give its slots to diagnosis, then scenarios; never pad with a non-qualifying task. Justify the split in one summary line.

A. FRONTIER DIAGNOSIS (50%). One task per frontier fixture; never two tasks (incl. active runs, open issues of the last 24h,
   open PRs) on the same frontier fixture + first differing entity. Order by earliest divergence turn first (2026-10-18:
   gen-campaign 16, gen-recruit-attack 31, combat set 166-173, gen-a-dunkelforst 331, then later ones; re-read the board for newer
   values). Owned frontiers: bc-full 1501 and gen-a-wintersturm 1352 = Codex; economy-long, late-economy, combat, combat-waves,
   combat-big, ai-long = Opus. Prefer unowned frontiers; an owned one only if no active/recent task or owner board entry (last 12h)
   already covers the same entity. Task text: run `tools/deepdiff.sh <fixture dir> <start image> <turn>` (siedler-sim-rs) at the
   first divergent turn (use `FROM=<turn-k>` to re-sync if needed), find the first differing entity + field, trace it to the native
   handler address (recomp/ lift, Ghidra decompile) and the expected values; output EITHER a minimal WIRED fix that moves the boundary
   (title prefix `Diverge <fixture>-<turn>:`), OR, if the fix lies in a file with a fresh Opus/Codex LANE-CLAIM, a precise wiring
   request on the board (addresses, expected values, file + function to touch, fixture + turn). Former claimed areas (economy/,
   world/commands.rs, workers/, ...) are NOT off limits any more; the gate merges there only when a frontier moves and nothing regresses.
B. FRONTIER-REACHED PORTS (20%). Title prefix `Port:`. A function qualifies ONLY if (a) the reference executes it within +-50 turns of
   a current frontier (per-turn function census: `s5recomp` block trace with `TRACE_TURNS` = the turns frontier-50..frontier+50 and
   `tools/coverage_map.py`, as `tools/coverage_map.sh` does; `tools/census.sh` = window divergence census per field / World::unmodelled,
   queue opus-008, helps to pick the window), (b) its address is not cited in `crates/s5clean/src` on origin/clean/integration, and
   (c) no open PR or open issue already covers it (title/body/files). One coherent group (5-25 functions) per task, addresses named,
   the frontier fixture + turn named. Port from the Ghidra decompile (siedler-forschung
   `cloud-re/results/rust_handoff/source/functions.jsonl`) cross-checked against `recomp/` (the lift is the truth), cite each address,
   unit-test bit-exact against recomp/, WIRE it; the PR merges only if NO fixture regresses (no exception any more).
C. NEW / LONGER SCENARIOS (20%). Extend fixtures that are IDENTICAL over their whole length further in time, or write generators
   for unexercised systems (trade, weather, campaign scripts, AI attacks, sieges, ...) with `docs/clean/new-fixtures/RUNNER.md`,
   `tools/genb_fixtures.py`, `docs/clean/new-fixtures/gen_commands.py`, gen-c. Upload as private release asset
   `fixtures-gen-<date>-<topic>` in Kofi55533/siedler-sim-rs, run s5clean live and post the first divergence on the board: every new
   divergence becomes a frontier (bucket A picks it up next run). Each task a distinct area/map from active runs.
D. DS-REQs + HYGIENE (10%). Open DS-REQs (section 0) first; remaining slots: rebase or close stale PRs (title `Rebase PR #<n> ...`
   for a PR that still moves a boundary; otherwise the stale-draft workflow closes it).

STOP (never create these): ports from COVERAGE.md / function_inventory.json that no frontier census reaches; duplicate tasks (dedupe
every candidate against open issues AND open PRs AND active runs: same fixture+turn+entity, same function addresses, same area/map);
tasks or PRs that only document an "inert helper"; tasks whose only output is a self-merge board entry.
Every task must be DISTINCT from each other and from every active run, open issue of the last 24h and open agent/* PR. If you cannot
find N genuinely distinct qualifying tasks, create fewer. Never invent topics from other Settlers games (S5 has no bakery/woodcutter/
forester); cite recomp/ functions or game data when naming a subsystem.
Anti-stuck: before creating a task for a fixture+boundary, check recent issues/runs (last ~24h) on the same fixture+turn. If 2-3 runs
already ended without moving that boundary, do NOT create another task for it; post a short handoff line on `docs/AGENT_BOARD.md`
addressed to Opus (fixture, turn, entity/field, what was tried, links) and use the slot for the next frontier.

## 3. Rules every task text must carry (state them in the task)
- Goal: a bit-identical clean Rust port (s5clean) of Settlers 5, verified turn by turn against the reference. You may choose your own approach, including generating new fixtures, if it serves the goal.
- Work in Kofi55533/siedler-sim-rs, branch `agent/<short-topic>` based on origin/clean/integration (or on a named open PR), DRAFT PR to `clean/integration`. Never main, never merge manually.
- Land a WIRED fix (hooked into World tick/dispatch) that moves the named frontier later; no doc-only/analysis-only PR, no unwired helpers, no "inert helper" PRs.
  (Scenario tasks: generate fixtures + s5clean live + board post first; wired fix only if the cause is small. Port tasks: wired + bit-exact, no regression.)
- MERGE RULE (gate): crates/s5clean tests green (bit-exact unit tests vs recomp/) AND no fixture of the full live matrix diverges earlier. No "Port:" exception.
  If the target frontier is a generated fixture outside the standard matrix, add `Frontier-fixture: <name>` to the PR body and report before/after there.
- Never edit recomp/, oracle/, crates/s5verify, crates/s5diff or .github/ (hard-blocked). Former claimed areas (economy/, workers/, world/commands.rs,
  net/commands.rs, lifecycle/, script/, scenario.rs, entity/animal.rs, docs/agent-queue/) are open, but merge only if a frontier moves and nothing regresses.
- LANE CLAIMS (replace directory claims): one board line `LANE-CLAIM <Owner> <YYYY-MM-DD HH:MM> <file>[::<function>] [...] -- <note>` (Berlin time,
  owner Opus|Codex|DeepSeek) reserves those files/functions for 6 hours. A fresh Opus/Codex lane claim on a file the PR touches keeps it draft:
  do not edit that file; post a wiring request instead (addresses, expected values, file+function, fixture+turn). Runs may post their own
  `LANE-CLAIM DeepSeek ...` line for the files they edit (advisory).
- Never change, revert or overwrite code that Opus pushed; Opus has final say (Opus > Codex > Muse). Codex instructions override Muse.
- Frontier owners (2026-10-18): bc-full 1501 + gen-a-wintersturm 1352 = Codex; economy-long, late-economy, combat/combat-waves/combat-big/ai-long = Opus. Diagnose those only without duplicating the owner's work (prefer unowned frontiers).
- Read docs/AGENT_BOARD.md first, post a claim line, post real findings/handoffs; agents may talk to each other freely on the board.
- s5-live before and after; report `live <fixture>: before X -> after Y`.

## 4. Task format (follow exactly, like earlier refill tasks)
- Title: one line, specific, with the bucket prefix: `Diverge <fixture>-<turn>: ...` (A), `Port: ...` (B), `Coverage ...` (C), `DS-REQ <id>: ...` / `Rebase PR #<n> ...` (D).
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
Write to $W/summary.txt (max 5 lines): `planner: created #a #b ...` (list `DS-REQ <id> -> #n` first) plus one line with the bucket split (diagnosis / ports / scenarios / DS-REQ+hygiene) and why fewer than N if so.
