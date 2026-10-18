Work on exactly the private assignment below in the current source checkout.
Read AGENTS.md, docs/AGENT_BOARD.md and relevant project rules first. The wrapper
has atomically reserved this task, its semantic topics and its write paths in
the PRIVATE agents/control branch. Opus/Codex board claims take priority.

The complete original game's behavior is the goal. Follow the current roadmap,
deterministic time/RNG rules, native references and live parity requirements.
One small useful result per run. No unwired helpers, speculative broad rewrites,
time-only scenario copies or documentation-only implementation results. Diagnose
the first differing entity/field, trace its native producer, then wire the
smallest correct implementation if this assignment permits source edits.

Write ONLY paths in this task's paths allowlist. A diagnose task is READ-ONLY for
the checkout; put its concrete finding in the external result JSON. Never edit
recomp/, oracle/, crates/s5verify/, crates/s5diff/, .github/ or the shared board.
Never create/modify credentials, git configuration, commits, branches, remotes,
issues or PRs. Never push or merge. The wrapper does all publishing privately.
Never launch other agents. Never run fleet management commands or alter the queue.
Do not disclose source, captures, game data, logs or secrets outside this checkout.
Do not upload sessions or communicate through public issues.

Existing live tools: /opt/s5/siedler-sim-rs/tools/deepdiff.sh, census.sh,
coverage_map.sh; runner helpers are in the checkout of the public fleet runtime,
whose path is supplied as S5_SCRIPTS. Source $S5_SCRIPTS/s5-env.sh before using its
s5-build.sh / s5-live.sh. Game data and build cache are under /opt/s5. The existing
decompiler exports functions.jsonl and code.jsonl are available under
/opt/s5/siedler-forschung/cloud-re/results/rust_handoff/source/. Search selectively;
do not load the entire exports into a model context. REA is not needed here.
The existing
integration gate remains responsible for the full required parity matrix.
For code fixes, record the target fixture's baseline and candidate measurements.
Do not claim a missing/failed fixture or a build failure is a passing result.

Write the requested external result file as strict JSON:
{"summary":"concrete finding/change with file/function, native address and actual before/after evidence",
 "frontier_fixture":"fixture-name or empty string",
 "before":"measured value or not measured with reason",
 "after":"measured value or not measured with reason",
 "next_step":"specific follow-up if blocked or diagnosing"}.
Do not include raw logs, code, secrets or game content. If the task is blocked,
state the precise reason instead of inventing another task. The wrapper checks
your scope and publishes one private draft PR for integration.
