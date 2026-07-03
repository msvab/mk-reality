---
name: find-real-estate-ads
description: Orchestrate portal-specific sub-agents to find Czech real estate advertisements for a provided municipality, then merge and deduplicate the results into a normalized shortlist. Use when Codex needs to search current property listings in parallel across supported portals, currently reality.idnes.cz, realitymix.cz, reality.aktualne.cz, and sreality.cz, with defaults limited to houses or building land and a minimum land size of 1000 m2 in both cases.
---

# Find Real Estate Ads

## Overview

Act as a parent orchestrator, not a single search worker. Parse the request once, launch one sub-agent per supported portal, collect normalized portal outputs, and perform final deduplication and ranking in the parent agent.

Supported portals currently include `reality.idnes.cz`, `realitymix.cz`, `reality.aktualne.cz`, and `sreality.cz`.

Default search scope:

- country: `Czech Republic`
- location granularity: provided municipality
- property types: `house`, `chalupa`, and `land`
- land minimum: `1000 m2` for houses and for building land

`chalupa` is input-only. Search for it when relevant, but always normalize matching rows to `house` in output.

## Orchestrator Workflow

1. Parse the user request into a shared search brief.
   Extract municipality, location scope, optional nearby radius, price bounds, house-size bounds, land-size bounds, and exclusions.
2. Make only the minimum necessary assumptions and state them in the final response.
   Default country to `Czech Republic`, transaction to `sale`, property types to `house`, `chalupa`, and `land`, and minimum land area to `1000 m2` for both. Do not impose any result limit unless the user explicitly asks for one.
3. Build one normalized input package for each portal worker.
4. Launch portal-specific sub-agents in parallel when sub-agents are available.
   Each sub-agent must own exactly one portal and must not search outside that portal.
5. Use the matching dedicated worker agent configuration for each portal:
   `agents/reality-idnes.yaml`, `agents/realitymix.yaml`, `agents/reality-aktualne.yaml`, and `agents/sreality.yaml`.
6. Wait for portal workers to finish, then collect their outputs into one combined candidate set.
7. Normalize field formatting if portal workers differ slightly.
8. Deduplicate overlapping listings across all worker outputs.
9. Rank the retained listings by parsed verified price in descending order, then apply fixed tie-breakers.
10. If `result_limit` is set, truncate the final retained shortlist to at most that many rows after filtering, deduplication, and ranking.
11. Return a unified result table with source links and coverage notes.

## Shared Worker Payload

Pass the same structured brief to every dedicated portal worker:

- `municipality`: required target municipality
- `location_scope`: default `municipality_only`; use `nearby_allowed` only when the user explicitly allows expansion beyond the exact municipality
- `nearby_radius_km`: optional; include only when the user provides or clearly implies a distance for nearby expansion
- `country`: `Czech Republic` unless the user explicitly overrides it
- `property_types`: default `house`, `chalupa`, and `land` where `chalupa` is input-only and must normalize to `house`
- `price_min` and `price_max`
- `house_size_min_m2` and `house_size_max_m2`
- `land_size_min_m2`: default `1000`
- `land_size_max_m2`
- `must_have`: list of required traits
- `exclude`: list of exclusions
- `result_limit`: optional final output cap; leave unset unless the user explicitly asks for one

Every dedicated portal worker must return only normalized rows with these fields:

`portal | title | location | property_type | price | house_area_m2 | land_area_m2 | urls | notes`

If a worker cannot retrieve a field, it must use `unknown` rather than guessing.
`chalupa` is not a normalized output type. Search for it as a house-like input category and return matching rows as `house`.
For `house` rows, interpret the `1000 m2` minimum as minimum land/parcel area, not minimum interior floor area.
If a row has `price` as `unknown`, exclude it from retained results.
If a house or land row has `land_area_m2` as `unknown`, exclude it from retained results.
`portal` may contain one or more source hostnames when duplicate rows are merged across portals.
`urls` must contain one or more detail-page URLs for the listing, not category or search results pages.

Read [search-playbook.md](./references/search-playbook.md) for the parent/child schema and deduplication rules.
Use `agents/reality-idnes.yaml` with [reality-idnes.md](./references/reality-idnes.md) for the `reality.idnes.cz` worker.
Use `agents/realitymix.yaml` with [realitymix.md](./references/realitymix.md) for the `realitymix.cz` worker.
Use `agents/reality-aktualne.yaml` with [reality-aktualne.md](./references/reality-aktualne.md) for the `reality.aktualne.cz` worker.
Use `agents/sreality.yaml` with [sreality.md](./references/sreality.md) for the `sreality.cz` worker.

## Portal Registry

Supported portal workers:

- `reality.idnes.cz`
- `realitymix.cz`
- `reality.aktualne.cz`
- `sreality.cz`

For unsupported portals:

- do not pretend support exists
- do not assign one worker multiple portals
- add a new portal-specific reference before claiming the portal is supported

## Search Rules

- Treat listing freshness as important. Prefer active ads and verify current listing pages instead of relying on search snippets.
- A worker may retain a row from a listing/search snapshot without opening the detail page only when that snapshot already exposes every field required for filtering and normalized output, and the detail URL is visible.
- Keep each worker scoped to its portal. Cross-portal comparisons happen only in the parent orchestrator.
- Search for sale listings only. Do not search rent inventory.
- Keep the search limited to the provided Czech municipality when `location_scope = municipality_only`.
- Expand beyond the exact municipality only when `location_scope = nearby_allowed`, and use `nearby_radius_km` only when it is explicitly provided or clearly implied by the user.
- Include `chalupa` when it otherwise behaves like a house listing, but always normalize it to `house` in output.
- Exclude `chata` unless the user explicitly asks for it.
- Exclude houses whose parcel area is below `1000 m2`.
- Exclude land listings below `1000 m2`.
- Exclude listings whose price cannot be verified.
- Exclude houses and land listings whose parcel area cannot be verified.
- Retain `land` rows only when the listing explicitly indicates buildable or residential use.
- Exclude agricultural, non-buildable, or clearly non-residential land unless the user explicitly asks for it.
- If a land listing mixes residential and recreational positioning or is otherwise ambiguous, keep it only when the listing still explicitly supports residential/buildable use and record the ambiguity in `notes`.
- Prefer actual listing detail pages over category pages, directory pages, and blog content unless the listing/search snapshot is already sufficient under the rule above.
- Never invent price, size, address, or listing status.
- Preserve original detail listing URLs so duplicates can be traced back to source pages.
- Use a compact machine-readable `notes` convention for recurring caveats and provenance, for example `snapshot-sufficient`, `duplicate-merged-from:realitymix.cz`, `duplicate-price:realitymix.cz=6390000`, `missing-public-sewer`, `mixed-residential-recreational`.

## Output Format

Return:

1. `Assumptions:` only when needed
2. one unified markdown table with columns

`portal | title | location | property_type | price | house_area_m2 | land_area_m2 | urls | notes`

3. `Coverage:` number of portal workers launched, how many returned results, how many listing pages were retained after deduplication
4. `Gaps:` blocked portals, unclear filters, or missing fields that materially affect confidence

## Deduplication Bar

- Merge listings when locality, property type, title, and the relevant area fields strongly indicate the same property, even if visible prices differ by a small syndication drift.
- When duplicates are merged, retain all contributing source hostnames in `portal`.
- When duplicates are merged, retain all known detail-page URLs in `urls`.
- When duplicates are merged and prices differ, keep the lowest verified visible price in `price` and record alternate source prices in `notes`.
- Prefer the row with the most complete data.
- If completeness is tied, prefer an active detail-verified row over a snapshot-only row.
- If still tied, prefer the row with the cleaner canonical URL.
- If uncertainty remains, keep both rows and mention possible duplication in `notes`.

## Ranking

- After filtering and deduplication, sort retained rows by parsed verified price in descending order.
- Parse the visible `price` text into a numeric CZK value in the parent before sorting. Normalize Czech number formatting and common million-style abbreviations where possible.
- If a retained row's visible verified `price` still cannot be parsed into a numeric CZK value, treat the row as malformed and exclude it before ranking.
- If two rows have the same parsed price, prefer the row with more complete data.
- If parsed price and completeness are tied, prefer an active detail-verified row over a snapshot-only row.
- If parsed price, completeness, and verification tier are tied, prefer the row with the cleaner canonical URL.
- If still tied, use a stable lexical fallback on `portal`, then `title`, then `location`.
- Because rows with unknown price are excluded, every retained row must participate in this sort.

## Quality Bar

- Prefer fewer verified results over many weak matches.
- Keep orchestration logic in the parent and portal logic in workers.
- If sub-agents are unavailable, fall back to a single-agent implementation but preserve the same portal-by-portal discipline.
- If no credible listings are found, say so directly and summarize which portal workers ran.
