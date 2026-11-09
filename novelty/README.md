# novelty-cover

COVER-mode novelty search on GitHub runners: `.github/workflows/novelty-cover.yml`, scripts in `scripts/novelty/`.

- Start a chain: `gh workflow run novelty-cover.yml -R Kofi55533/siedler-agent --ref novelty/cover -f wave=1 -f max_waves=30`
- **Stop**: commit an (empty) file `novelty/STOP` on branch `novelty/cover` (no new wave is planned or dispatched),
  and/or `gh run cancel <id>` for the running wave.
- Results (private): release `novelty-cover-results` in Kofi55533/siedler-data: `w<wave>-s<shard>.json.gz`,
  `w<wave>-merged.json`, `known.json` (logic functions outside union3 found by the search, with the first probe key).
- Inputs (private): release `novelty-cover` (bundle.tzst = s5recomp-cover + helpers + catalog/logic/union3/plan,
  img-<fixture>.tzst = start images). Rebuild with `scripts/novelty/make_bundle.py` on the box.
