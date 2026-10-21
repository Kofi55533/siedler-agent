# Independent domain fleet

The fleet in `Kofi55533/siedler-agent` can own the entire game implementation without a Codex or Opus session.
`FLEET_V2_ENABLED=true` selects this controller; `false` restores the legacy refill path. Existing jobs finish.
The model remains `opencode/deepseek-v4.1-flash-free`; the OpenCode CLI is pinned to `opencode-ai@1.19.3`.
A provider access refusal holds new assignments. It never triggers a model change or an IP rotation.
An optional tool denial remains denied. The worker may finish using permitted task inputs, without retrying
the denied operation or inspecting OpenCode storage, sessions or credentials. Provider refusals take precedence
over tool denials. Planning phase deadlines allow a smaller plan within the same runner reservation.

## Ownership and planning

The authoritative inventory has 29,723 logic obligations. The smaller subsystem map routes work; unmapped
addresses remain owned obligations. The initial partition contains 104 units, including unmapped inventory bands.
State, plans, original-source context, reservations, outputs and logs stay on the private `agents/control` branch
in `siedler-sim-rs`. The Actions repository stores generic infrastructure and synthetic tests only.

Each domain worker reads its written overview, original code, existing ports, prior attempts and failed gates,
then chooses its next coherent transition or algorithm. It reserves concrete files or Rust items atomically
before writing. It refreshes open PR scopes and fresh board claims before each package reservation. An occupied
file leads to another plan; no whole domain depends on an accelerator's availability. New workers do not
adopt a subsystem while its legacy writer is still active. Existing PR work is repaired rather than replaced.

A plan estimates 10–150 minutes from state, branches, callbacks and verification work. Large or repeatedly stuck
assignments split; small assignments combine neighboring obligations. A partial branch is preserved before its
unattempted remainder moves to a successor. Its findings and pending overview follow the successor even before
its PR merges. Completion cannot be inferred from a citation count, a scenario boundary or an overview label.
Unexecuted logic needs native differential tests and a concrete fixture or fixture request.

## Full runner budget and independent packages

Worker jobs have a 300-minute limit. A timestamp before setup charges setup against that same budget. The wrapper
ends model work by minute 285 and starts no fresh package in the final 25 minutes. Every model call is bounded by
the remaining job time. A checkpoint is pushed every 20 minutes and on completion or interruption.

After each package the wrapper immediately submits its own PR and immutable gate ticket, then atomically claims
another package in the same domain on the same runner. A successor gets a different branch and retains only its
own obligations. Earlier packages keep their resource reservations during verification. The next package cannot
edit their files. A later provider failure, timeout or gate failure cannot discard earlier PRs. Checkpoint-only
work keeps its own draft branch and can be resumed independently. No whole-run final PR is required.
Source changes rejected by the scope checker are saved as private recovery patches before the checkout is
cleaned. Recovery requires a new valid reservation; a saved patch cannot bypass the gate or protected paths.

Five-minute worker heartbeats coalesce controller dispatches, and completed packages request an early dispatch.
This provides a refill and gate trigger during long-running jobs without relying exclusively on cron or occupying
another runner in a sleep loop. Completion events also trigger control. Existing jobs and infrastructure all count
toward the 20-job ceiling. Four slots stay reserved for verification/integration so long workers cannot starve gates.
With the controller active this normally leaves 15 model workers; released infrastructure capacity is reused.

## Gate and integration

Up to four preflights run independently. Each uses the existing build, s5clean tests, hard-path/claim restrictions,
soft-area frontier requirement, unexecuted-code differential suites and complete live no-regression matrix.
Every named extra fixture is required. ERROR, TIMEOUT or missing data cannot become a successful comparison.
Receipts bind the exact base, PR head, merged tree, gate scripts, fixture set and private reference-data version.
A code-changing base move invalidates receipts; only coordination Markdown can move without a new measurement.

The integration writer groups up to four file-disjoint, individually green packages from one base. It builds and
tests their exact union against the full matrix, then repeats every selected native differential suite. A union
failure separates the parts for individual integration rather than endlessly retrying the same combination.
Before pushing, the writer checks current claims across the entire union. A normal fast-forward Git push is the
base compare-and-swap: a concurrent code change rejects the push. The tested tree is never reconstructed with a
squash API. A durable tested-push intent recovers successful pushes followed by a private-state write failure.

Legacy gates remain available during migration. They can move the base; new receipts then requeue. No criteria
are weakened to avoid this work. No workflow targets the game repository's `main` branch.

The v2 controller also admits stranded legacy fixture PRs to the existing `gate-baseline regate` workflow.
At most two such jobs share the four verification slots and the overall 20-run limit. Active legacy writers,
existing re-gates and domain-managed packages are excluded. Infrastructure failures and repaired declarations
can re-enter the full gate; an unchanged correctness rejection waits for an author repair. Admission supplies
no PASS and changes no build, native-test, claim, required-fixture or live-matrix rule.

Private retry records bind the PR head, body, verification-input epoch and gate scripts. Markdown board activity
does not repeatedly re-gate an unchanged package. Code/data, head, body or gate-input changes allow another
attempt. An uncertain dispatch retains its slot for 20 minutes before retry; successfully dispatched unchanged
inputs are not repeatedly launched. Legacy gate completion wakes control even when no model worker is active.

## Rollout, checks and rollback

Run before enabling:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts/autonomy.py scripts/autonomy_state.py scripts/gate_contract.py
bash -n scripts/s5-gate.sh scripts/s5-build.sh
actionlint -shellcheck= .github/workflows/fleet-*.yml .github/workflows/opencode.yml .github/workflows/refill.yml
python3 scripts/autonomy.py shadow
```

Shadow mode reads current GitHub state and computes a plan without state writes, comments, issues, dispatches or
repository variables. Branch workflow dispatches cannot mutate fleet state; mutation requires the main workflow
and the enabling variable. Review automation exercises synthetic Git repositories and fake native tools.
Its optional `worker_smoke` dispatch also runs actual worker setup and planning against private native inputs,
with no leases, state writes, source edits, pushes or PRs. These checks establish orchestration and rejection
behavior, not game-code bit identity.

After merging the rebuild PR, enable and trigger the controller:

```sh
gh variable set FLEET_V2_ENABLED -R Kofi55533/siedler-agent --body true
gh workflow run fleet-control.yml -R Kofi55533/siedler-agent --ref main -f mode=control
```

One-switch rollback:

```sh
gh variable set FLEET_V2_ENABLED -R Kofi55533/siedler-agent --body false
gh workflow run refill.yml -R Kofi55533/siedler-agent --ref main
```

This stops new v2 dispatches and restores legacy supply. Already-running jobs retain their original environment
and can checkpoint/finish; do not cancel them. PRs, leases, receipts and private diagnostics remain recoverable.
Watch the first new workers for at least 30 minutes: acquisition, model start, checkpoints/PRs, gate dispatch and
provider failures. Roll back on a stalled controller or widespread job failure. Never infer game completion from
successful Actions jobs. Track integrated reference-tested logic obligations out of 29,723 separately from live
scenario boundaries; the dashboard labels worker-reported native proof receipts explicitly.
