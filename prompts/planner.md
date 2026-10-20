# PLANNER RUN (task generator, not a coding run)

This run overrides the generic "every run MUST target a divergence / write code" instruction of the wrapper:
you write NO code, push NO branch, open NO PR, and do NOT edit siedler-sim-rs, with ONE exception: the DS-REQ
markers in `docs/AGENT_BOARD.md` on clean/integration (section 0).
Your only output is N new QUEUED task issues in Kofi55533/siedler-agent (section 5; the refill workflow launches them into free slots).
N is given in the trigger comment as `N=<number>` (default 3, hard max 15).

## 0. DS-REQ first (top priority, before inventing anything)
Before choosing any other task, read `git show origin/clean/integration:docs/AGENT_BOARD.md`, section
`## Für DeepSeek (Aufträge von Opus)`. Every line `- [ ] DS-REQ <id>: <task> (from Opus, <date>)` is an open request
from Opus (or Codex) and MUST become a task issue in this run, ahead of everything in section 2 (they count towards N
and against the bucket of section 2 they fit, by default the 50% frontier-diagnosis bucket; if that bucket is full, the extra
ones take slots from the other buckets; if there are more than N, take them oldest first; never skip one for an own idea).
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
FIRST read `$W/planner_state.md`: precomputed by the workflow right before this run (frontiers from the scoreboard, all DS-REQ
and recent LANE-CLAIM board lines, every open siedler-sim-rs PR with draft/mergeable/checks/cited addresses/files, active runs,
QUEUED tasks (= not yet launched, count as existing tasks for dedupe), task issues of the last 24 h with their state
(ACTIVE / QUEUED / FINISHED) and last result line, the FRONTIER WORK MAP (per diverging fixture: holder and what is plannable),
the FILL SOURCES (port candidates, untested commands/maps, unmeasured scenarios, conflicting PRs) and which coverage map to use).
IMPORTANT: task issues are never closed after their run, so "open issue" does NOT mean pending work (there are hundreds).
Only ACTIVE and QUEUED tasks and open PRs hold a lane. FINISHED issues only feed the anti-stuck rule and tell you what was
already tried/measured; a FINISHED issue on an older boundary of the same fixture does not block the current boundary. It replaces the individual gh calls listed below; make such a call only for a detail the file
lacks (e.g. a PR body/file list beyond the shown excerpt) or to re-check right before queueing.
This planner runner has NO game data, no siedler-forschung clone and no build: never run s5-live/s5-build/s5recomp here.
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
- Coverage view: NEVER run `tools/coverage_map.sh` (or any s5recomp block trace) in a planner run. The hourly workflow
  `coverage.yml` computes it on origin/clean/integration and publishes it; planner_state.md already downloaded it and says which
  map to use. Manual equivalent (local only, never commit it):
  `gh release download coverage-cache -R Kofi55533/siedler-agent -D $W --clobber -p COVERAGE.md -p coverage-meta.txt` (gives `$W/COVERAGE.md` and `$W/coverage-meta.txt`
  with `clean_integration_sha=` and `generated_utc=`). If the download fails, a file is missing/empty, or `generated_utc` is older
  than 3 h, use the committed `docs/clean/COVERAGE.md` on origin/clean/integration instead. In BOTH cases re-check "ported" yourself:
  a function counts as ported if its address is cited anywhere in `crates/s5clean/src` on origin/clean/integration
  (`git grep -i <addr> origin/clean/integration -- crates/s5clean/src`).
- Current first-divergence boundaries per live scenario (bc-full wintersturm economy wsecon build production combat combat-waves
  combat-big combat-long ai-long late-ai late-economy camp-crawford map-winterisland): take the latest numbers from planner_state.md
  / the board (no live measurement on this runner; a task may tell its run to re-measure).

## 2. Choose N tasks (work split, Opus decision 2026-10-18 20:40)
Queue each task issue (section 5) as soon as that task is chosen; do not wait until all N are chosen.
The goal is the same as Opus': a bit-identical clean Rust port (s5clean) of Settlers 5, verified turn by turn against the
reference. Progress = FRONTIERS (first-divergence turns of the live scenarios and generated fixtures) moving later.
Split N into four buckets: floor(0.5N) frontier diagnosis, floor(0.2N) frontier-reached ports, floor(0.1N) scenarios,
floor(0.2N) hygiene; give the remaining slots in the order diagnosis, ports, hygiene, scenarios.
Examples: N=19 -> 10 / 4 / 1 / 4, N=20 -> 10 / 4 / 2 / 4, N=10 -> 5 / 2 / 1 / 2 (diagnosis / ports / scenarios / hygiene).
Open DS-REQs (section 0) are created first and count in the bucket they fit (default: diagnosis).
Queue items (`docs/agent-queue/`) are used only if they fit one of the buckets and count there. If a bucket has no qualifying
task, give its slots to diagnosis, then hygiene, then the FILL SOURCES (below); never pad with a non-qualifying task.
Justify the split in one summary line.
ALWAYS QUEUE N (Dominic 2026-10-20 10:09): a planner never ends with fewer than N tasks because frontiers/DS-REQs are exhausted
or deduped away. Fill the remaining slots from the FILL SOURCES of planner_state.md, in this order and round-robin if one runs
low: (d) rebase of conflicting agent/* PRs, (a) ports of executed-but-uncited functions, (c) measuring unmeasured scenarios,
(b) new/longer scenarios for untested commands and maps. These lists are already deduped against cited code, open PRs and
task issues; still re-check each pick. Fewer than N is allowed ONLY if every remaining candidate is a provable duplicate:
then name the skipped candidates and the issue/PR each duplicates in the summary.
WIP LIMIT: at most 1 open PR per run (= per agent lane / issue). Never create a task for a lane, fixture/scenario or function group
that already has an open agent/* PR in siedler-sim-rs; that PR is handled by a hygiene task (D) instead: a CONFLICTING one gets a
rebase task (the frontier work map marks it `REBASE ... mandatory`), a mergeable one held by a fresh Opus/Codex lane claim gets
NO task (the gate merges it after the claim expires; the map shows until when).

A. FRONTIER DIAGNOSIS (50%). One task per frontier fixture; never two tasks (incl. ACTIVE/QUEUED tasks and open PRs) on the
   same frontier fixture + first differing entity; take the FREE rows of the frontier work map. Order by earliest divergence turn first (2026-10-18:
   gen-campaign 16, gen-recruit-attack 31, combat set 166-173, gen-a-dunkelforst 331, then later ones; re-read the board for newer
   values). Owned frontiers: bc-full 1501 and gen-a-wintersturm 1352 = Codex; economy-long, late-economy, combat, combat-waves,
   combat-big, ai-long = Opus. Prefer unowned frontiers; an owned one only if no active/recent task or owner board entry (last 12h)
   already covers the same entity. Task text: run `tools/deepdiff.sh <fixture dir> <start image> <turn>` (siedler-sim-rs) at the
   first divergent turn (use `FROM=<turn-k>` to re-sync if needed), find the first differing entity + field, trace it to the native
   handler address (recomp/ lift, Ghidra decompile) and the expected values; output EITHER a minimal WIRED fix that moves the boundary
   (title prefix `Diverge <fixture>-<turn>:`), OR, if the fix lies in a file with a fresh Opus/Codex LANE-CLAIM, a precise wiring
   request on the board (addresses, expected values, file + function to touch, fixture + turn). Former claimed areas (economy/,
   world/commands.rs, workers/, ...) are NOT off limits any more; the gate merges there only when a frontier moves and nothing regresses.
B. FRONTIER-REACHED PORTS (20%). Title prefix `Port:`. A function qualifies for bucket B if (a) the reference executes it within +-50 turns of
   a current frontier (per-turn function census: `s5recomp` block trace with `TRACE_TURNS` = the turns frontier-50..frontier+50 and
   `tools/coverage_map.py`, as `tools/coverage_map.sh` does; `tools/census.sh` = window divergence census per field / World::unmodelled,
   queue opus-008, helps to pick the window), (b) its address is not cited in `crates/s5clean/src` on origin/clean/integration, and
   (c) no open PR or open issue already covers it (title/body/files). One coherent group (5-25 functions) per task, addresses named,
   the frontier fixture + turn named. Port from the Ghidra decompile (siedler-forschung
   `cloud-re/results/rust_handoff/source/functions.jsonl`) cross-checked against `recomp/` (the lift is the truth), cite each address,
   unit-test bit-exact against recomp/, WIRE it; the PR merges only if NO fixture regresses (no exception any more).
C. SCENARIOS (10%). Only scenarios that reach a NEW code path (a native handler no current fixture executes) or a NEW
   unmodelled marker (`World::unmodelled`); a pure stay-IDENTICAL time extension of an already covered path no longer counts and
   no longer merges. Name the expected new path/marker in the task; the PR body must carry `New-code-path: <address/subsystem>` or
   `New-unmodelled: <marker>` (gate rule). Write generators for unexercised systems (trade, weather, campaign scripts, AI attacks,
   sieges, ...) or extend a fixture only if the extension reaches such a path, with `docs/clean/new-fixtures/RUNNER.md`,
   `tools/genb_fixtures.py`, `docs/clean/new-fixtures/gen_commands.py`, gen-c. Upload as private release asset
   `fixtures-gen-<date>-<topic>` in Kofi55533/siedler-sim-rs, run s5clean live and post the first divergence on the board: every new
   divergence becomes a frontier (bucket A picks it up next run). Each task a distinct area/map from active runs.
D. HYGIENE (20%). Goal: open PRs in siedler-sim-rs below 60. Each task names its PRs as `PR #<n>` (the gate re-measures every
   `PR #<n>` named in the title that the run pushed to): rebase green ones onto clean/integration so the gate can merge them
   (title `Rebase PR #<n> ...`), or close stale/superseded drafts and duplicates (title `Hygiene: close PR #<n> PR #<m> ...`):
   only agent/* heads, never opus/codex/haiku heads or PRs handed to Codex (needs-codex comment), always a one-line reason comment
   naming the superseding PR/merge, branches kept. Pick the oldest conflicted/duplicate PRs first; no two hygiene tasks on one PR.
   MANDATORY regardless of the open-PR count (Dominic 2026-10-20): every `REBASE ... mandatory` row of the frontier work map
   (a CONFLICTING agent/* PR that holds a frontier) gets a `Rebase PR #<n>` task first; then the other conflicting agent/* PRs
   of the fill list. Only the remaining hygiene slots (closing stale drafts) go to diagnosis when open PRs are below 60.
   A rebase task tells the run: rebase onto origin/clean/integration, keep the PR's hunks (patch-id), keep DRAFT/branch, abort
   that single rebase with a board handoff if the conflict lies in a file with a fresh Opus/Codex LANE-CLAIM.

FILL PORTS (fill source (a), Dominic 2026-10-20): outside bucket B, any logic function the reference EXECUTES in a fixture
   (listed in planner_state.md "Port candidates", from the cached coverage map) that is uncited in crates/s5clean/src qualifies as
   a `Port:` fill task: one coherent group (5-15, same class / call chain) per task, addresses + fixture + first turn named,
   ported from the Ghidra decompile cross-checked against recomp/ (the lift is the truth), bit-exact unit tests against recomp/,
   each address cited, wired where its state is compared; an inert/read-only function is recorded in one line and skipped.
CLAIMS (Dominic 2026-10-20): never create a task whose work lies in a file or function listed under "Fresh Opus/Codex lane
claims" in planner_state.md (a `file::function` claim reserves that function, a plain file claim the whole file) — the owner is
on it; pick the next candidate instead. A task near such a lane must say "do not edit <claimed items>; post a wiring request".
STOP (never create these): ports of functions the reference never executes in any fixture (function_inventory.json alone is not
enough; use it only for the class/role of an executed function); duplicate tasks (dedupe
every candidate against open issues AND open PRs AND active runs: same fixture+turn+entity, same function addresses, same area/map);
tasks or PRs that only document an "inert helper"; tasks whose only output is a self-merge board entry; pure stay-IDENTICAL
time extensions of already covered paths; a second task/PR on a lane that already has an open agent/* PR (WIP limit 1).
Every task must be DISTINCT from each other and from every ACTIVE/QUEUED task, every FINISHED task of the last 24 h with the same
target (same fixture+turn, same addresses, same command/map) and every open agent/* PR. If the frontier buckets cannot fill N,
use the FILL SOURCES (section 2, "ALWAYS QUEUE N"); create fewer only with the proof described there. Never invent topics from other Settlers games (S5 has no bakery/woodcutter/
forester); cite recomp/ functions or game data when naming a subsystem.
TASK SUPPLY (Dominic 2026-10-20, enforced by refill: a violating queued task is closed instead of launched):
- never queue a `Port:` task whose addresses are listed under "Known done" in planner_state.md (evidence-only runs proved them
  ported/modelled, inert, STL/CRT helpers or unreached); a group may keep at most a minority of known-done addresses, named as such;
- never queue a target listed under "Do-not-repeat" again (same addresses, or same fixture+turn after an evidence-only result)
  unless the frontier turn moved or the base changed relevantly; then add the body line `Known-done-override: <the change>`;
- no `Diverge` task for a fixture that the scoreboard shows IDENTICAL over its full length;
- no genb/genc re-measure-only tasks: gate-baseline mode=remeasure re-measures every genb/genc fixture on clean/integration
  every 3 h without an LLM and posts `<fixture>: DIVERGENT turn N` board lines; plan a root-cause task on its result instead.
Anti-stuck: before creating a task for a fixture+boundary, check recent issues/runs (last ~24h) on the same fixture+turn. If ONE run
already ended evidence-only (or 2-3 runs without moving that boundary), do NOT create another task for it; post a short handoff line on `docs/AGENT_BOARD.md`
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
- s5-live before and after; report `live <fixture>: before X -> after Y`. Frontier PRs (title `Diverge ...` or `Frontier-fixture:` line)
  MUST state these live before -> after turn numbers in the PR body, or the gate does not merge them.
- WIP limit: at most 1 open PR per run; if an open agent/* PR already covers the lane, push to it (or stop) instead of opening another.
- Scenario PRs merge only with a body line `New-code-path: ...` or `New-unmodelled: ...`; stay-IDENTICAL time extensions do not merge.

## 4. Task format (follow exactly, like earlier refill tasks)
- Title: one line, specific, with the bucket prefix: `Diverge <fixture>-<turn>: ...` (A), `Port: ...` (B), `Coverage ...` (C), `Rebase PR #<n> ...` / `Hygiene: close PR #<n> ...` (D); DS-REQs: `DS-REQ <id>: ...`.
- Issue body: ONE short generic sentence (e.g. `Muse task (planner). Details in the /opencode comment.`), then the line `---PLANNER-COMMENT---` and the full comment text (section 5) — no private code, no log content.
- Comment: starts with `/opencode ` then one dense paragraph: target fixture + current turn, angle, what to trace read-only in recomp/,
  base branch/PR, branch name, success criterion; then a ` Context (re-measure yourself, board = docs/AGENT_BOARD.md on clean/integration,
  read it first and post your claim/result there): ...` paragraph listing latest relevant merges and open drafts not to duplicate; then the rules of section 3 in short form.

## 5. Queue them (the refill workflow launches them)
For each task, as soon as it is chosen (comment.txt starts with "/opencode "):
```
printf 'Muse task (planner). Details in the /opencode comment.\n\n---PLANNER-COMMENT---\n' > body.txt && cat comment.txt >> body.txt
n=$(gh issue create -R Kofi55533/siedler-agent --label planner-task --label queued --title "$TITLE" --body-file body.txt | grep -oE '[0-9]+$')
```
Do NOT post the `/opencode` comment yourself and do not comment `/opencode` on this planner issue or on any other issue: the
refill workflow (every ~5 min) posts the text after `---PLANNER-COMMENT---` as the `/opencode` comment when a slot is free
(DS-REQ first, then oldest) and swaps the label `queued` -> `launched`; queued tasks older than 6 h are closed as stale.
Verify each issue exists with both labels (`gh issue view $n -R Kofi55533/siedler-agent --json labels,body`).
Dedupe against active runs, QUEUED issues and recent issues alike (all listed in planner_state.md).

## 6. Summary
Write to $W/summary.txt (max 5 lines): `planner: queued #a #b ...` (list `DS-REQ <id> -> #n` first) plus one line with the bucket split (diagnosis / ports / scenarios / hygiene, open PR count) and why fewer than N if so.
