# Own-repo mode: a second account in the xc fleet without writing to the main account

A second account (the *pool*, e.g. Dominickofi00) runs xc model jobs in **its own** repos. Nothing it runs writes to a
Kofi55533 repo. The main account (Kofi55533) imports what passed, verifies it again and merges it as any worker does.
All existing files (scripts/xc/*, xc-worker.yml, pool/xc-pool.yml, fleet state) stay untouched; this directory and
three new workflow files are the whole addition.

## Flow

1. **Pool, pick** (`.github/workflows/xc-own.yml`, called by `pool/xc-own-pool.yml` in the pool's repo): reads the
   main work list, tracking issues and open PRs read-only and takes kinds that the main fleet does not hold
   (`own.py pick`): no main claim, no open PR, no merge since the census, its half of a deterministic partition
   (`XC_OWN_PARTITION`, default `1/2`), and below the main fleet's next `XC_OWN_SKIP` (default 12) kinds. It claims them
   in the pool's private results repo (`XC_OWN_RESULTS`). At most 4 jobs at once (default 3).
2. **Pool, fix**: `snapshot.sh` copies the main project's `clean/integration` tree as a commit into the results repo
   (no shared history needed). Then the unchanged `scripts/xc/worker.sh` works there (`XC_PROJECT` = results repo): model,
   `verify.sh`, PR and squash-merge, all in the results repo. The passed change stays as branch `xc/<kind>`, one commit on
   a snapshot.
3. **Main, import** (`.github/workflows/xc-import.yml`, main repo, main token, off by default): lists the results repo's
   `xc/*` heads, takes kinds that are still available on the main fleet and whose head it has not tried
   (`own.py imports`, at most `XC_IMPORT_MAX`, default 2), claims them on the main tracking issues (token `import-<run>`).
4. `import.sh` fetches the head and checks it: one commit, files only under the worker scope, still applies to the main
   `clean/integration`. Then it runs the unchanged `worker.sh` with one round, and `opencode` on PATH is the
   stand-in `scripts/xc_own/opencode`, which applies the pool's change instead of calling a model. `worker.sh` measures
   the base, runs `verify.sh` and only on PASS opens and merges the PR, exactly as for a model change.

## Guards on the pool side

- `own.py pick` and the workflow refuse a results repo that does not belong to the calling account; the jobs do not
  run in the main account.
- `guard/gh` wraps gh: every writing API call outside `repos/<own account>/` is refused (graphql included); only
  `api`, `release download`, `auth status` are allowed.
- `guard/hooks/pre-push` (via `core.hooksPath`) refuses every push to `github.com/Kofi55533/`.
- Recommended: the pool account has the **Read** role on the Kofi55533 repos, so its token cannot write there at all.

## Known limits

- The main fleet does not read the pool's claims: rarely both work on one kind. The partition and the skip make it
  rare; the import drops kinds merged, claimed or with an open PR on the main side.
- A pool change that builds on an earlier, not yet imported pool merge can fail on the main base (snapshot before
  each kind keeps it rare); it is recorded as a normal failed attempt.
- One import try per head sha (cache `xc-import-seen-*`); the pool's next pass of the kind pushes a new head.

## Tests

`python3 -m unittest tests.test_xc_own`: partition, pick and import filters against a fake GitHub, the import flow on
local repos (including a moved base and a change that no longer applies), the gh guard and the push hook.
