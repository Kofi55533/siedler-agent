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
