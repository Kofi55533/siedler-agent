# novelty-cover

COVER-mode novelty search on GitHub runners: `.github/workflows/novelty-cover.yml`, scripts in `scripts/novelty/`.

- Start: edit `novelty/RUN` on branch `novelty/cover` (key=value: wave, max_waves, shards, keep_free, budget_min, forks,
  reps, strats) and push it; every push that changes `novelty/RUN` starts one wave, the merge job starts the next
  until `max_waves`. (`gh workflow run novelty-cover.yml --ref novelty/cover -f ...` once the file is on main.)
- **Stop**: commit an (empty) file `novelty/STOP` on branch `novelty/cover` (no new wave is planned or dispatched),
  and/or `gh run cancel <id>` for the running wave.
- Results (private): release `novelty-cover-results` in Kofi55533/siedler-data: `w<wave>-s<shard>.json.gz`,
  `w<wave>-merged.json`, `known.json` (logic functions outside union3 found by the search, with the first probe key).
- Inputs (private): release `novelty-cover` (bundle.tzst = s5recomp-cover + helpers + catalog/logic/union3/plan,
  img-<fixture>.tzst = start images). Rebuild with `scripts/novelty/make_bundle.py` on the box.

## Comparing forced inputs with the clean rewrite

`novelty_shard.py --xcheck-config /private/config.json` invokes the sim repository's
`tools/forced_xcheck.py::Discovery` after raw state pokes and before coverage execution or scratch cleanup.
It seals and compares every encoded probe, including probes without novel addresses. The JSON configuration
names `tool`, `out`, `functions`, `binary`, `build_receipt` and optional `only`, `stride`, `maximum`, `timeout`.
Use absolute paths, a complete pinned inventory, and the actual candidate executable with its source/dependency
hash receipt. The full contract is in siedler-sim-rs `docs/clean/FORCED_XCHECK.md`.

Both archive and scratch directories must be new and separate. Private archive objects deduplicate copied images;
they never link to mutable discovery scratch. A failed seal retains the original scratch and fork. A completed
comparison retains its inputs, original log/report and verdict, even for failures or missing bindings. Probe JSON
contains `xcheck`, and the shard reports verdict counts. Coverage success and clean comparison success are distinct.
`NOT_ENCODED`, `FAILED`, `GAPS` and incomplete `PENDING` archives are never acceptance evidence.

The existing Actions workflow does not yet provision this capture executable/build receipt or archive upload.
The flag is currently used by the tested box invocation. Keep all resulting images, commands and captures private;
do not attach them to this public repository's Actions artifacts or logs. Under the Astra task's repository policy,
durable uploads of those bytes belong only in the private siedler-sim-rs or siedler-forschung repository.

Synthetic lifecycle checks:
`python -m unittest discover -s scripts/novelty -p test_novelty_bridge.py -v`.
The native test preserves the original discovery results for two seeded probes; a build probe compares twelve
samples at four functions, while the no-command control retains its no-samples failure. Evidence is in the sim
repository's `docs/clean/validation/astra-discovery-hook-20261111.json`. No new accepted functions are inferred.
