# Shared private agent fleet

Both Actions repositories consume one queue in the private source repository:

- State and status: `Kofi55533/siedler-sim-rs`, branch `agents/control`.
- Source base and draft PR target: `clean/integration` in that same repository.
- Pools: `Kofi55533/siedler-agent` and `Dominickofi00/opencode-runner`, up to 20 workers each.
- Model: `opencode/deepseek-v4.1-flash-free`, CLI pinned to `@opencode/cli@2.0.26`.

## Workflow

`fleet-control` runs on completions and every five minutes. It refreshes current
PR reservations, current Opus/Codex board claims and running/queued jobs, then
starts a lightweight planner, available workers and the integration queue.
Schedulers never keep a runner occupied just to wait for a timer. GitHub
schedules can be delayed; completion events provide another refill trigger.

The planner reads the current roadmap, architecture, board, source paths, open
PR scopes and previous outcomes. It writes small JSON tasks. The wrapper validates
their schema and rejects repeated semantic keys/native producers. Imported old
issue requests become planning input, never direct unreserved code assignments.

A worker claims its task and all write paths in one state update. State commits
are children of the state version read; a non-force fast-forward ref update
rejects concurrent sibling updates. The loser reads the new state and retries.
Semantic topic reservations additionally prevent two fixtures from producing two
simultaneous fixes for the same native behavior. This relies on meaningful task
topics; a model cannot prove that differently named topics are semantically distinct.

Workers renew reservations every five minutes with a 30-minute expiry. Expired
reservations are only recycled after GitHub confirms the old job has completed.
A lost reservation stops the model. Scope checks reject writes outside the
claimed paths. The model receives no GitHub token or authenticated remote URL.
Only the wrapper pushes the specific private branch and opens its draft PR.
Results, logs and findings stay in the private control branch / PR.

Writing tasks retain reservations until integration finishes. One global
integration lease serializes both pools. The gate checks the exact worker head,
builds, existing project tests, live baseline and candidate, and rejects missing
verdicts and unavailable required fixtures. A changed code base causes a full
retry rather than using measurements from an older base. External maintainers
should coordinate integration through this queue too; GitHub's PR merge API does
not provide a base-SHA compare-and-swap for an unrelated external merge.

## Migration and rollout

Already-running old jobs finish unchanged. During the transition, new workers
may investigate unreserved modules with the read-only source profile; new code
work/integration waits until old workers and gates have drained. Legacy jobs
count toward the 20+20 target. New `/opencode` comments go to the private planning
inbox. The old refill sleep chain is removed for future runs. Existing PR scopes
and active board claims remain reserved.

The configured target and ceiling are 40, as requested by the user. When the
explicit queue is thin, the planner independently proposes useful work and the
wrapper seeds focused gap investigations on unclaimed source modules. Stable
source-blob keys prevent repeatedly auditing unchanged code. Findings feed the
next implementation planning pass. Source-only investigations fetch source and
existing native JSON exports, avoiding the large game-data setup.
A ceiling does not assert 40 actual workers: independent ready tasks, GitHub
account concurrency, runner availability and provider rate limits all apply.
Idle workers exit before data setup instead of inventing duplicate work.

## Operation

Use the existing authorized GitHub CLI login without printing its token:

```powershell
$env:GH_TOKEN = gh auth token --hostname github.com --user Dominickofi00
python scripts/fleet.py status
python scripts/fleet.py configure --enabled false
python scripts/fleet.py configure --enabled true --limit 40
Remove-Item Env:GH_TOKEN
```

Alternatively run **fleet-control → Run workflow** and choose start/pause and an
optional ceiling. Pausing stops new assignments; it does not cancel work or
discard reservations. The private branch README shows task states, PRs and
outcomes. Raw model output is never a public artifact.

Each pool needs one `PRIVATE_REPO_TOKEN` with access to the private source/data
repositories and its own Actions dispatches. The provider secret remains central:
`OPENCODE_API_KEY` in the original pool, `HUU` in the added pool. No project-by-project
copy is needed. Provider errors cool down the affected pool with backoff instead
of creating endless failing jobs.

A provider network-access refusal places that pool on an explicit access hold.
It is not retried by launching new runner IPs. Other already-authorized work may
finish, and the integration queue can continue without a model call. An operator
must choose an approved provider/execution configuration before releasing the hold.

Data setup omits the unused `opus-extra` archives. REA is not a fleet dependency.
