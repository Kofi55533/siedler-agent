# Fleet operation

The current rebuild is documented in [docs/FLEET_V2.md](docs/FLEET_V2.md). It uses the existing
`Kofi55533/siedler-agent` pool with a ceiling of 20 concurrent jobs, including verification infrastructure.
The old two-pool experiment is not the active rollout path.

Control state, domain inventories, native evidence, model outputs and game-code branches remain private in
`Kofi55533/siedler-sim-rs`. Game-code PRs target `clean/integration`. No fleet job targets its `main` branch.
The public Actions repository contains infrastructure and synthetic tests only.
