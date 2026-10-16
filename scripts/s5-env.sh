# sourced: fixed paths so cargo fingerprints from the build cache stay valid
export S5=/opt/s5
export S5_REPO=$S5/siedler-sim-rs
export S5_GAME=$S5/data/game
export S5_ARTIFACTS=$S5/data/art
export S5_V=$S5/data/fixtures/codex-verify-infra
export VIRTUAL_ENV=$S5/py311
export PATH="$VIRTUAL_ENV/bin:$HOME/.cargo/bin:$PATH"
export UNICORN_LIB_DIR=$S5/py311/lib/python3.11/site-packages/unicorn/lib
export RUSTUP_TOOLCHAIN=1.99.0
export CARGO_TERM_COLOR=never CARGO_TERM_PROGRESS_WHEN=never
export WGPU_BACKEND=vulkan
export S5_NF=$S5/data/newfix
# full live gate set (same as Codex box suite)
export S5_GATE_FIXTURES="bc-full wintersturm economy wsecon build production combat combat-waves combat-big combat-long ai-long late-ai late-economy camp-crawford map-winterisland wintersturm-mapstart economy-mapstart"
