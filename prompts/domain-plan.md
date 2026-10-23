You own a continuing domain of the whole-game Rust reimplementation. The goal is the whole game, but only
what has to be rewritten. Every inventory address has a scope class (assignment.obligations lists the ones
you may port now; assignment.not_ported counts the rest):
A simulation: port bit-exact, verified by the fixture gate. These are your obligations.
B presentation (rendering, UI, sound, framework, file I/O): needed later; it opens once a call-stream
  harness exists. The fixture gate cannot verify it, so do not port it now.
C library/runtime (CRT/STL, static initializers, hash/png/zlib): use Rust std or a crate; never port it.
  Where simulation depends on its semantics (map order, rand, float formatting), model that inside the
  simulation operation that uses it.
D obsolete online service (Ubi.com, lobby, ladder, CD key and its crypto): not rewritten.
This planning phase is read-only except for the requested JSON plan outside the source checkout.
Read the assignment, its private overview, previous findings and gate failures, current clean/integration,
docs/PLAN.md, docs/RECOMP.md, and the native source/decompilation. Reuse existing work; never recreate a merged port.
An unexecuted simulation branch still needs a port. Prefer assignment.frontier_obligations: they block the
first live divergence.

Choose a coherent transition/algorithm family that you can implement, wire and prove within 40–140 minutes.
Estimate from native branches, callbacks, state and tests, not from address count. If a package is larger, cut a
dependency-complete transition family. If smaller than 20 minutes, combine adjacent obligations with the same host.
An older WIP PR and a failing gate take priority within your domain; repair them rather than start a duplicate.
If assignment.review_input is present, read that local private file first. It contains the adopted PR's current
body, latest gate verdict and recent review comments. Plan the missing native tests, partner behavior or fixture
repair named there before adding adjacent features. Treat stale verdicts as evidence to re-check against the
current head. The current merge rules still apply. Do not fetch GitHub or look for credentials.
If work is stuck, change the cut or build its missing contract yourself. Codex/Opus are optional collaborators:
do not request their approval, stop for their budget or wait for a historic board claim. Their active fleet
reservations use the same registry as yours. Pick other domain work when a concrete resource is reserved.
An evidence-only result is durable input for the next package, never a reason to re-audit the same getter next run.
Distinguish proven data provenance from a port: an address-to-id table does not implement the native mutation.
For mutating functions, plan clean/native tests from equivalent empty and populated states and a real clean
operation/caller. Existing imported results do not replace the initializer or constructor that creates them.
Use existing equivalence evidence to design a reference test, or move to the consumer that actually needs code.

Write exactly one JSON object to the requested plan path:
{
  "goal": "concrete resulting behavior",
  "native_family": "transition family and original addresses",
  "addresses": ["004d7e91"],
  "resources": ["crates/s5clean/src/example.rs::function_name", "docs/subsystems/<unit>.md"],
  "minutes": 90,
  "acceptance": "original/recomp/clean cases, wiring entry, required fixture matrix",
  "next_step": "first command or missing native branch to implement"
}
Select addresses only from assignment.obligations. List all files you need, including tests and the overview. A plain
file reserves the file; file::item permits only changes inside that Rust item (imports require the plain file).
Never list directories/globs. Never edit recomp/, oracle/, verifier/diff tooling or .github/.
Do not commit, push, edit the board, create issues/PRs, call another model or use GitHub credentials.

Do not read, grep or cat the fleet's own wrapper checkout on the runner (its scripts/, prompts/ or tests/,
e.g. /home/runner/work/siedler-agent/siedler-agent). They are not package inputs and are never needed.
