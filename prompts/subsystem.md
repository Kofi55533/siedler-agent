
SUBSYSTEM RUN ({id}: {title}) — THIS SECTION OVERRIDES the scope rules above ("ONE frontier or functions within +-50 turns of a
frontier", "keep PRs small", the 20%/50% work split). You have up to 200 minutes. Goal: the WHOLE game bit-exact; this run ports
the WHOLE {title} subsystem from the reference, not one divergence. Map entry: order {order}, {functions_total} functions,
ported {ported}, executed by a fixture {executed} (not yet ported: {executed_unported}); depends on: {depends_on}.
- OVERVIEW FIRST: on the branch, read docs/subsystems/{id}.md (scope, ported/missing list, s5clean design, open questions, next
  step) and docs/subsystems/INDEX.md. The complete function list is the map entry:
    jq -r '.subsystems[]|select(.id=="{id}").functions[]|"\(.addr) \(.name) \(.class) ported=\(.ported) executed=\(.executed|join(","))"' docs/subsystems/subsystems.json
  UPDATE docs/subsystems/{id}.md with every push (what you ported, what is missing, where it lives in s5clean and why, open
  questions, the exact next step for the next run). It is the hand-over: the next run of this subsystem starts from it. Set its
  line to 'Status: complete' only when every function of the entry is ported (cited) or recorded there as not game logic
  (with the reason); refill then stops queueing this subsystem. Do not edit INDEX.md/subsystems.json (generated) and never
  docs/AGENT_BOARD.md on the branch. For an 'unmapped-*' region: first identify what the functions are (classes, callers) and
  write that into the overview, then port the logic ones.
- Scope: {scope}.
- Reference classes: {classes}. The lift in recomp/ is the truth (read-only; Ghidra/siedler-forschung for names). Port each
  function bit-exact into crates/s5clean with unit tests (cite each address). Already-modelled functions: verify them against
  recomp and fix deviations instead of re-porting.
- Files: {files} (and the World tick/dispatch call sites needed to WIRE the group in; no unwired helpers).
- CODE NO FIXTURE EXECUTES (executed= empty in the function list): bit-exactness comes from DIFFERENTIAL TESTS against the
  reference instead of a live frontier. Put them in crates/s5xcheck/tests/{id}.rs (harness: crates/s5xcheck/src/lib.rs —
  s5xcheck::reference() loads the reference image, s5xcheck::call / call_this / with_reference run the native function on the
  recompiled reference in an isolated query, s5xcheck::clean_world() imports the same image into s5clean): call the clean port and
  the native function on the same state/arguments and assert identical results and state effects; cite the address in the test.
  Run them like the gate:  [ -f crates/s5xcheck/Cargo.lock ] || cp recomp/Cargo.lock crates/s5xcheck/;
    CARGO_TARGET_DIR=$S5_REPO/recomp/target cargo test --release --manifest-path crates/s5xcheck/Cargo.toml --test {id}
  (never commit crates/s5xcheck/Cargo.lock; never edit recomp/ or oracle/). PR body: 'Differential-tests: --test {id}'.
  ALSO add or request a fixture that executes the subsystem: 'Fixture: <existing/added fixture that now reaches it>' or
  'Fixture-request: <scenario: map, players, commands that would reach it>' in the PR body (and in the overview).
  Gate rule: a Subsystem PR whose newly cited addresses include unexecuted logic functions merges only if those differential
  tests pass (>= 1 passed, 0 failed), every such address is in a crates/s5xcheck/tests file with a reference call, the PR body
  has the Fixture:/Fixture-request: line, and no fixture of the live matrix diverges earlier.
- Measure with s5-live.sh on ALL of: {fixtures} before your first change (baseline) and after each completed part; no fixture may
  diverge earlier. Report every fixture as 'live <fixture>: before turn X -> after turn Y' in the PR body (add a
  'Frontier-fixture: <name>' line for each generated fixture outside the standard matrix).
- Branch {branch}, base origin/clean/integration. If the branch or its PR already exists, CONTINUE it: merge/rebase
  origin/clean/integration first, read docs/subsystems/{id}.md and the PR body for what is done, then do the next part. Exactly
  ONE PR for this branch (draft, base clean/integration, title 'Subsystem {id}: ...'); never a second one.
- COMMIT AND PUSH INCREMENTALLY (git push after every function or test group that builds, and the overview with it, at least every
  ~20 minutes), so nothing is lost when the 200-minute timeout hits. Never commit docs/AGENT_BOARD.md on the branch.
- CLAIM narrowly: at start post on clean/integration's board ONE line
  'LANE-CLAIM Subsystem <YYYY-MM-DD HH:MM> <file>::<function> ... -- subsystem {id} on {branch}' (Berlin time) naming only the
  functions you will change in this run (not whole directories); refresh it with a new line when you start another part. Other
  DeepSeek runs do not edit those functions. Fresh Opus/Codex LANE-CLAIMs still win: never edit their files/functions (Codex owns
  player statistics); post a wiring request instead.
- The self-merge gate criteria are not weakened (build, s5clean tests, no fixture earlier, frontier move for soft areas — for
  code no fixture reaches, the green differential tests above stand in for the frontier move). It gates your PR at the end of the
  run; a partial but green subsystem PR merges, the next subsystem run continues the branch.
- summary.txt: functions ported (count + addresses), differential tests (count, passed), fixture added/requested, PR link,
  'live <fixture>: before turn X -> after turn Y' for every fixture above, and the next step written to the overview.
