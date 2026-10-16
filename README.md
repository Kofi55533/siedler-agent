# siedler-agent

Runs [OpenCode](https://opencode.ai) on GitHub's public runners to work on private repositories.
Triggered by `/opencode <task>` comments from the owner only. Results go exclusively as branches /
draft PRs to the private repos; this repo contains only the workflow, and comments contain only short summaries.

## Live data and self-merge gate (2026-10-16)
- `data-v2` (private `siedler-data`) now also carries `newfix.tzst`: the extra Codex box fixtures/images
  (ai-long, late-ai, late-economy, camp-crawford, map-winterisland, combat-waves/big/long), extracted to `/opt/s5/data/newfix`.
  `s5-live.sh` covers every Codex live gate (`$S5_GATE_FIXTURES`).
- `scripts/s5-gate.sh <pr> [--dry-run]`: Muse may squash-merge its own `agent/*` PR into `clean/integration` only if
  s5verify builds, `crates/s5clean` tests pass, every fixture's first divergence >= clean/integration baseline, and no
  path/topic in `scripts/muse-claims.txt` (Opus/Codex lanes) is touched. Runs inline in `opencode.yml` and manually via `muse-gate.yml`.
  Never targets main.

## Priority rule (Dominic 2026-10-17 00:11)
Progress is measured ONLY by first-divergence turns moving forward on the live scenarios.
Every OpenCode run must target the current first divergence of one scenario, find the root cause at that turn,
and land a **wired** fix (hooked into World tick/dispatch) that moves that boundary.
No doc-only analysis PRs and no unwired helpers. If wiring is in a Codex/Opus-claimed area, post an explicit
wiring request on `docs/AGENT_BOARD.md` instead. Self-merge gate criterion remains no-regression.

