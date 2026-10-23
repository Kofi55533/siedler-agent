Implement the reserved package and continue your domain's written overview. The assignment and plan paths
are appended below. All project content stays in the private source repository. The model is fixed to
opencode/deepseek-v4.1-flash-free. Never bypass an OpenCode/provider refusal or launch another model.

The original instructions and active SCE hooks are the reference; recomp/ and oracle/ are read-only. Produce
readable Rust, cite each native address, and wire through the real World tick/dispatch/host entry. Verify every
new behavior against original/recomp execution: branch boundaries, ordering, RNG/x87/binary32 effects and
multi-step transitions, including target loss, destruction and imported state where relevant. Do not use the
ported implementation to manufacture its own expected values. Native wrappers may share one idiomatic Rust
operation; prove that equivalence per address instead of generating one Rust function per native function.
Both sides of a differential test must perform the same operation from equivalent initial state. For a
registration/constructor, compare the clean and native mutations from empty, pre-populated and repeated-call
states, including relevant contents/counts and order. Reading an already imported registry is not a test of
the initializer that builds it. Address-to-id provenance tables and citations are durable evidence, not newly
ported behavior; do not report them as ready proofs. Name the actual clean operation and real caller.

Port only simulation (scope class A) behavior; assignment.obligations lists it. Library code (CRT/STL,
static init, hash/png/zlib) becomes Rust std or a crate, online-service code (Ubi.com, lobby, CD key) is
not rewritten, and presentation code waits for its call-stream harness. Never submit those as ports.
Unexecuted code is first-class work. Add native differential tests under crates/s5xcheck/tests/<unit>.rs and
name the target with `Differential-tests: --test <unit>`. Call the clean operation and native function on the
same inputs and compare outputs/state effects. Include positive, negative and boundary branches. Do not edit
the harness or skip/ignore unavailable private inputs. Add or specify a concrete `Fixture:`/`Fixture-request:`.
Full-game completion requires those obligations too; "no compared field" or "not reached" does not close them.

Use $S5_SCRIPTS/s5-build.sh and s5-live.sh for relevant before/after checks. The independent gate requires
the build, bit-exact tests and the entire live matrix with no earlier divergence; it also tests the exact union
before atomic integration. A port with a regression needs its missing partner in the same package. Preserve
the current soft-area frontier requirement and the existing unexecuted-code differential-test rule.
Do not run the full self-merge gate yourself or poll GitHub. Separate verification jobs own that work.

Write only reserved files. Do not change Git history/remotes, commit, push, open/merge PRs or edit AGENT_BOARD.md.
The trusted wrapper checkpoints and pushes your work every 20 minutes, opens/updates one private draft PR,
and saves it on timeout. You may request an early checkpoint by completing a buildable group. Always leave
the overview with: native obligations and evidence levels; design and real entry points; transition graph;
what remains; attempted plans and why they changed; gate regressions/missing partners; exact next command.
Do not mark the domain complete from citation counts or unsupported exclusions. Large/stuck work should
return a smaller transition cut; small work should group neighboring native obligations.
Codex/Opus can contribute through the same live resource registry. Their presence or review is never required.

Write result.json at the requested path. Use this shape (paths/addresses are illustrative):
{
  "outcome": "ready",
  "summary": "concrete behavior, reference cases and measured before/after",
  "proofs": [{"address": "004d7e91", "reference_test": "suite::test_name",
              "clean_entry": "World::real_entry", "branches": "target present, no leader, repick"}]
}
Other outcomes: checkpoint, evidence, stuck, too_large, too_small, setup_error, provider_error, access_refusal.
For `ready`, also write pr-body.md next to result.json: problem/result, native proof, commands/results,
`Differential-tests: --test <unit>` when required, `Fixture:`/`Fixture-request:`, and one
`Frontier-fixture: <name>` per extra fixture. Write every relevant `live <fixture>: before turn X -> after turn Y`
on its own line; name IDENTICAL ticks explicitly. No raw live logs, game data, secrets or source dumps in the body.
Do not claim success merely because the first divergence stayed unchanged.

This is one package within a 300-minute domain run. Return its result as soon as the coherent change is ready;
the wrapper immediately gives this package its own immutable gate ticket and private PR, refreshes claims,
and takes the next package in the SAME domain on this runner. Do not wait for a gate verdict, combine all run
output into one final PR, or declare the whole run finished after a small package. The wrapper owns the job
clock and final checkpoint margin. Record useful residuals in the overview so the next package reuses them.

Never inspect OpenCode's storage, sessions, auth files or provider settings. They are not game inputs.
Read assignment.review_input when present; use its private gate/review evidence to repair the adopted package.
Resolve the recorded failure with native proof and current measurements. Do not fetch GitHub or seek credentials.
A denied operation stays denied. Continue only with the explicitly supplied permitted game inputs; do not
change permissions or retry the denied operation. Keep temporary probes under assignment's .fleet-input
workspace. Declare every source/test file in the plan before writing it; Cargo.lock files and temporary
experiments are not game-code changes to submit. A rejected source delta is saved privately for scoped repair.

Do not read, grep or cat the fleet's own wrapper checkout on the runner (its scripts/, prompts/ or tests/,
e.g. /home/runner/work/siedler-agent/siedler-agent). They are not package inputs and are never needed.
