
SUBSYSTEM RUN ({id}: {title}) — THIS SECTION OVERRIDES the scope rules above ("ONE frontier or functions within +-50 turns of a
frontier", "keep PRs small", the 20%/50% work split). You have up to 200 minutes. Goal: port the WHOLE {title} subsystem bit-exact
from the reference, not one divergence.
- Scope: {scope}.
- Reference classes: {classes}. Enumerate the complete function group from recomp/ (the lift is the truth; Ghidra/siedler-forschung
  for names), list every address in your PR body, and port each function bit-exact into crates/s5clean with unit tests against
  recomp/ (cite each address). Already-modelled functions: verify them against recomp and fix deviations instead of re-porting.
- Files: {files} (and the World tick/dispatch call sites needed to WIRE the group in; no unwired helpers).
- Measure with s5-live.sh on ALL of: {fixtures} before your first change (baseline) and after each completed part; no fixture may
  diverge earlier. Report every fixture as 'live <fixture>: before turn X -> after turn Y' in the PR body (add a
  'Frontier-fixture: <name>' line for each generated fixture outside the standard matrix).
- Branch {branch}, base origin/clean/integration. If the branch or its PR already exists, CONTINUE it: merge/rebase
  origin/clean/integration first, read the PR body/board for what is done, then do the next part. Exactly ONE PR for this branch
  (draft, base clean/integration, title 'Subsystem {id}: ...'); never a second one.
- COMMIT AND PUSH INCREMENTALLY (git push after every function or test group that builds, at least every ~20 minutes), so nothing is
  lost when the 200-minute timeout hits. Never commit docs/AGENT_BOARD.md on the branch.
- CLAIM narrowly: at start post on clean/integration's board ONE line
  'LANE-CLAIM Subsystem <YYYY-MM-DD HH:MM> <file>::<function> ... -- subsystem {id} on {branch}' (Berlin time) naming only the
  functions you will change in this run (not whole directories); refresh it with a new line when you start another part. Other
  DeepSeek runs do not edit those functions. Fresh Opus/Codex LANE-CLAIMs still win: never edit their files/functions (Codex owns
  player statistics); post a wiring request instead.
- The self-merge gate is unchanged (build, s5clean tests, no fixture earlier, frontier move for soft areas). It gates your PR at the
  end of the run; a partial but green subsystem PR merges, the next subsystem run continues the branch.
- summary.txt: functions ported (count + addresses), PR link, 'live <fixture>: before turn X -> after turn Y' for every fixture above.
