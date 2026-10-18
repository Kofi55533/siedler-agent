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
  Runs inline in `opencode.yml` and manually via `muse-gate.yml`/`gate.yml`.
  Never targets main.
- `scripts/close-stale-drafts.sh [--dry-run]` + `stale-drafts.yml` (hourly): closes siedler-sim-rs draft PRs older than 48 h that are
  conflicted or were gate-measured without moving a boundary (comment with reason; branches kept; opus/codex/haiku heads and
  non-clean/integration bases never touched).

## Priority rule (Dominic 2026-10-17 00:11)
Progress is measured ONLY by first-divergence turns moving forward on the live scenarios.
Every OpenCode run must target the current first divergence of one scenario, find the root cause at that turn,
and land a **wired** fix (hooked into World tick/dispatch) that moves that boundary.
No doc-only analysis PRs and no unwired helpers. If wiring is in a Codex/Opus-claimed area, post an explicit
wiring request on `docs/AGENT_BOARD.md` instead. Self-merge gate criterion remains no-regression.

