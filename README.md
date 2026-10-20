# siedler-agent

Runs [OpenCode](https://opencode.ai) on GitHub's public runners to work on private repositories.
Triggered by `/opencode <task>` comments from the owner only. Results go exclusively as branches /
draft PRs to the private repos; this repo contains only the workflow, and comments contain only short summaries.

## Live data and self-merge gate (2026-10-16)
- `data-v2` (private `siedler-data`) now also carries `newfix.tzst`: the extra Codex box fixtures/images
  (ai-long, late-ai, late-economy, camp-crawford, map-winterisland, combat-waves/big/long), extracted to `/opt/s5/data/newfix`.
  `s5-live.sh` covers every Codex live gate (`$S5_GATE_FIXTURES`).
- `scripts/s5-gate.sh <pr> [--dry-run]` (Opus decision 2026-10-18): DeepSeek may squash-merge its own `agent/*` PR into
  `clean/integration` only if s5verify builds, `crates/s5clean` tests pass (bit-exact unit tests), NO fixture of the live
  matrix diverges earlier (no "Port:" exception), no `hard` path of `scripts/muse-claims.txt` (recomp/, oracle/, s5verify/s5diff,
  .github/) is touched, no fresh (<6 h) Opus/Codex `LANE-CLAIM` line on the board names a touched file (`scripts/lane-claims.py`),
  and, when a `soft` path (former claimed area, e.g. economy/, world/commands.rs) is touched, at least one frontier moves later.
  Opus 2026-10-18 20:40: a source-free scenario/coverage/fixture PR merges only with a body line `New-code-path: ...` or
  `New-unmodelled: ...` (pure stay-IDENTICAL extensions no longer merge); a frontier PR (`Diverge ...` title or `Frontier-fixture:`
  line) needs `live <fixture>: before turn X -> after turn Y` in its body.
  Runs inline in `opencode.yml` and manually via `muse-gate.yml`/`gate.yml`.
  Never targets main.
- `scripts/close-stale-drafts.sh [--dry-run]` + `stale-drafts.yml` (hourly): closes siedler-sim-rs draft PRs older than 48 h that are
  conflicted or were gate-measured without moving a boundary (comment with reason; branches kept; opus/codex/haiku heads and
  non-clean/integration bases never touched).

## Independent domain fleet

The approved v2 rollout replaces the fixed frontier/planner quota with continuing domain ownership across all
29,723 logic obligations. Workers use the 300-minute job budget, submit each package independently, then take
another package in the same domain. Codex and Opus are optional contributors. The existing strict merge gate
remains mandatory. See [operation, verification and rollback](docs/FLEET_V2.md).

The repository variable `FLEET_V2_ENABLED` switches between v2 and the preserved legacy refill path. Existing
jobs are never canceled. No model or provider-refusal workaround is part of the switch.
