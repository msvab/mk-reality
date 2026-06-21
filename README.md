# mk-reality

This repository builds a static HTML page for municipalities around Dobruška and enriches it with real estate advertisement counts and per-city listing drawers.

The repo currently has two connected pipelines:

- a school and municipality pipeline that generates the base site
- a real estate ads pipeline that fetches, normalizes, aggregates, and renders ad data into that site

The root `*.py` files are command wrappers kept for compatibility with the documented commands. The implementation lives
under `reality/`, and tests live under `tests/`.

## Main Files

### [build_html.py](/Users/michal-mbp/dev/reality/build_html.py)

Primary site generator.

What it does:

- collects municipality, school, amenity, population, and driving-time data
- writes the base dataset to `dobruska_primary_schools.json`
- writes the static site to `index.html`
- optionally reads `real_estate_ads_by_city.json`
- adds the `Počet inzerátů` column into the generated table
- renders the per-city ads drawer UI and embeds the drawer payload into the page

Important inputs:

- `data/overpass/municipalities.json`
- `data/overpass/schools.json`
- `data/overpass/amenities.json`
- `data/cache/school_url_cache.json`
- `data/cache/school_type_cache.json`
- `data/cache/school_registry_cache.json`
- `data/cache/mapotic_malotridky_cache.json`
- optionally `real_estate_ads_by_city.json`

Important outputs:

- `dobruska_primary_schools.json`
- `index.html`

Typical usage:

```bash
rtk python3 build_html.py
```

By default, `build_html.py` uses cached raw Overpass responses from `data/overpass`.
Refresh those source files explicitly when the Overpass query changes or the cached source data needs to be updated:

```bash
rtk proxy python3 build_html.py --refresh-overpass
```

When only `real_estate_ads_by_city.json` changed, use the faster ads-only rebuild. It reuses `dobruska_primary_schools.json` and updates only ad counts and drawer data in `index.html`:

```bash
rtk python3 build_html.py --ads-only
```

### [build_real_estate_ads_json.py](/Users/michal-mbp/dev/reality/build_real_estate_ads_json.py)

Single-city ads normalizer.

What it does:

- takes one raw JSON result from the `find-real-estate-ads` skill
- validates the expected fields
- parses prices and land areas
- filters out unusable rows
- enforces the minimum `1000 m2` land rule
- deduplicates listings
- normalizes portal-level fetch status, including rate limits and partial detail-fetch failures
- preserves structured `fetch_attempts` for retry/error auditability when raw outputs provide them
- sorts retained listings
- writes one normalized JSON file for downstream use

Important input shape:

- one raw per-city JSON object with `query`, `coverage`, `gaps`, and `listings`
- optional `portal_status` keyed by supported portal domain
- optional `fetch_attempts` array with per-URL fetch/retry outcomes

Typical usage:

```bash
rtk python3 build_real_estate_ads_json.py --input raw.json --output normalized.json
```

### [build_real_estate_ads_by_city.py](/Users/michal-mbp/dev/reality/build_real_estate_ads_by_city.py)

All-cities aggregator.

What it does:

- reads the city list from `dobruska_primary_schools.json`
- reads a directory of raw per-city ads JSON files
- normalizes each city through the same single-city logic used by `build_real_estate_ads_json.py`
- produces one aggregated file keyed by city

Important output:

- `real_estate_ads_by_city.json`

Typical usage:

```bash
rtk python3 build_real_estate_ads_by_city.py \
  --schools-input dobruska_primary_schools.json \
  --raw-dir data/real_estate_ads_raw \
  --output real_estate_ads_by_city.json
```

### [run_real_estate_ads_by_city.py](/Users/michal-mbp/dev/reality/run_real_estate_ads_by_city.py)

Resumable batch runner for the ads skill.

What it does:

- reads municipalities from `dobruska_primary_schools.json`
- invokes `codex exec` with the `find-real-estate-ads` skill prompt for each city
- writes one raw JSON file per city into a raw output directory
- skips cities that already have a valid raw file
- persists progress and failures into a state file
- can resume after interruption without restarting from the beginning
- rebuilds the aggregate ads file at the end

Important defaults:

- raw output directory: `data/real_estate_ads_raw`
- state file: `real_estate_ads_run_state.json`
- aggregate output: `real_estate_ads_by_city.json`
- exec schema: `real_estate_ads_exec_output.schema.json`

Useful flags:

- `--limit N` processes only the next `N` cities in this run
- `--retry-failed` retries municipalities recorded as failed in the state file
- `--overwrite` forces re-fetch even if a valid raw file already exists
- `--daily-refresh` refreshes every city, passes previous active ads as prompt cache, and hides ads missing from the latest snapshot
- `--force-daily-refresh` allows a daily refresh to re-run municipalities already refreshed today
- `--local-first` tries deterministic portal fetchers before falling back to Codex
- `--local-only` uses only deterministic portal fetchers and records a failure instead of falling back to Codex
- `--aggregate-after-each` refreshes `real_estate_ads_by_city.json` after every successful city

Typical usage:

```bash
rtk python3 run_real_estate_ads_by_city.py --aggregate-after-each
```

Small safe first run:

```bash
rtk python3 run_real_estate_ads_by_city.py --limit 5 --aggregate-after-each
```

Resume later:

```bash
rtk python3 run_real_estate_ads_by_city.py --aggregate-after-each
```

Daily refresh:

```bash
rtk python3 run_real_estate_ads_by_city.py --daily-refresh --local-first --aggregate-after-each
rtk python3 build_html.py --ads-only
```

One-command daily workflow:

```bash
rtk .venv/bin/python refresh_real_estate_ads.py
```

This runs the daily local-first refresh, rebuilds `index.html`, validates the aggregate and embedded drawer payload,
prints refreshed per-city summaries and the portal-warning summary, runs pytest, Ruff, the Playwright drawer smoke test,
and `git diff --check`.
Use `--limit N` for a smaller batch. Use `--push --commit-message "Refresh real estate ads"` after validation when
you want the script to commit generated refresh artifacts and push them.

The daily refresh still runs current municipality-level searches so it can detect new and removed ads. It reuses the previous aggregate as a prompt cache, so known active ads do not need full detail re-discovery when the current search result still exposes the same URL/title/location/price/areas. Ads that were present previously but are missing from the latest city snapshot move from `ads` into `hidden_ads`; only active `ads` are counted and rendered.

Daily refreshes are guarded per municipality in `real_estate_ads_run_state.json`. If a municipality has already completed today, a later `--daily-refresh` skips it and continues with the next municipality, which keeps partial refreshes resumable without paying to re-check the same city. Use `--force-daily-refresh` only when you intentionally want to re-run already refreshed municipalities on the same day.

Use `--local-first` to reduce Codex usage where deterministic portal helpers can cover the refresh. This path reuses cached result pages for `mmreality.cz` and `reality.aktualne.cz`, discovers current result pages on `realitymix.cz` and `reality.aktualne.cz` where possible, and verifies cached detail URLs for all local helper-backed portals; it falls back to Codex when local verification fails.

Use `--local-only` for cost-controlled test runs where Codex must not be invoked. It uses the same local helper path as `--local-first`, but cities that local helpers cannot refresh are recorded as failures instead of falling back.

### [summarize_real_estate_fetch_errors.py](/Users/michal-mbp/dev/reality/summarize_real_estate_fetch_errors.py)

Portal warning reporter for the ads aggregate.

Use it to identify portal errors after a batch fetch, including rate limits, DNS failures, cache misses, inactive pages, fallback pages, and partial snapshot-based results.

Typical usage:

```bash
rtk python3 summarize_real_estate_fetch_errors.py
```

Machine-readable usage:

```bash
rtk python3 summarize_real_estate_fetch_errors.py --json
```

### [real_estate_ads_exec_output.schema.json](/Users/michal-mbp/dev/reality/real_estate_ads_exec_output.schema.json)

JSON schema passed to `codex exec`.

What it does:

- constrains the final response format of the non-interactive ads search run
- ensures the runner gets a machine-readable JSON object instead of markdown
- allows optional `portal_status` so portal fetch failures are structured instead of only described in text
- allows optional `fetch_attempts` so retries and direct fetch failures can be audited later

### [real_estate_ads_input.example.json](/Users/michal-mbp/dev/reality/real_estate_ads_input.example.json)

Example raw single-city ads payload.

Use it for:

- understanding the expected raw structure
- testing the normalization and aggregation scripts locally

## Data Files

### [dobruska_primary_schools.json](/Users/michal-mbp/dev/reality/dobruska_primary_schools.json)

Generated base municipality dataset used by both pipelines.

Contains one object per municipality with fields such as:

- `city`
- `population`
- `drive_min`
- `amenities`
- `school_type`
- `school_name`
- `school_url`

### [real_estate_ads_by_city.json](/Users/michal-mbp/dev/reality/real_estate_ads_by_city.json)

Generated aggregated ads artifact used by `build_html.py`.

Contains:

- top-level coverage metadata
- one city entry per municipality
- `count` for the table cell
- active `ads` for the drawer
- `hidden_ads` for previously seen ads missing from the latest snapshot
- `price_history` on active and hidden ads, appending only when a matched ad's parsed `price_czk` changes
- `coverage`, `assumptions`, and `gaps` for each city

### `data/real_estate_ads_raw/*.json`

Raw per-city outputs from the ads runner.

These are the resumable boundary of the ads pipeline. If the run stops after city 5, rerunning will skip valid files for cities 1 to 5 and continue from the next missing or invalid city.

### [real_estate_ads_run_state.json](/Users/michal-mbp/dev/reality/real_estate_ads_run_state.json)

Generated state file for the resumable runner.

Contains:

- completed cities
- failed cities
- current city
- remaining cities
- last completed city
- overall run status

## End-to-End Flow

### Base Site Flow

1. Run `build_html.py`.
2. The script reads cached Overpass municipality, school, and amenity data unless `--refresh-overpass` is used.
3. It writes `dobruska_primary_schools.json`.
4. It writes `index.html`.

### Ads Flow

1. Make sure `dobruska_primary_schools.json` exists.
2. Run `run_real_estate_ads_by_city.py`.
3. The runner reads cities from `dobruska_primary_schools.json`.
4. For each city, it calls `codex exec` with a prompt that uses the `find-real-estate-ads` skill.
5. Each city result is written as one raw JSON file in `data/real_estate_ads_raw`.
6. The runner skips already valid city files on rerun, so the process is resumable.
7. The runner writes progress into `real_estate_ads_run_state.json`.
8. The runner rebuilds `real_estate_ads_by_city.json`.

For the daily refresh path, pass `--daily-refresh`. That forces each city to be refreshed instead of skipped, gives the worker cached active ads from the previous aggregate, and marks missing previous ads as hidden in the rebuilt aggregate.

### Final Render Flow

1. Run `build_html.py` after `real_estate_ads_by_city.json` exists.
2. The generator reads the aggregated ads file.
3. It adds a `Počet inzerátů` column to each municipality row.
4. If a city has ads, the count is rendered as a clickable button.
5. Clicking the count opens a drawer with that city’s listings.

## Recommended Workflow

For a fresh or partial ads refresh:

```bash
rtk python3 run_real_estate_ads_by_city.py --limit 5 --aggregate-after-each
rtk python3 build_html.py --ads-only
```

After verifying the first few cities:

```bash
rtk python3 run_real_estate_ads_by_city.py --aggregate-after-each
rtk python3 build_html.py --ads-only
```

## Notes

- This repo uses the RTK wrapper, so shell commands should be prefixed with `rtk`.
- `build_html.py` remains the source of truth for `index.html`.
- The ads pipeline is designed so raw city outputs are durable and resumable; the aggregate file can always be rebuilt from them.
- The daily refresh uses the previous aggregate as cache context, but removals can only be detected by refreshing the current city search results.
