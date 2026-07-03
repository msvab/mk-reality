# Search Playbook

## Purpose

Use this reference when the parent orchestrator needs to prepare portal-worker inputs, receive standardized outputs, and deduplicate merged listing sets for Czech municipalities.

## Parent Input Schema

Build a search brief with these fields:

- `municipality`: required municipality name
- `location_scope`: default `municipality_only`; use `nearby_allowed` only when the user explicitly allows expansion beyond the exact municipality
- `nearby_radius_km`: optional; include only when the user provides or clearly implies a distance for nearby expansion
- `country`: default `Czech Republic`
- `property_types`: default `house`, `chalupa`, and `land`, where `chalupa` is input-only and must normalize to `house`
- `price_min`
- `price_max`
- `house_size_min_m2`
- `house_size_max_m2`
- `land_size_min_m2`: default `1000`
- `land_size_max_m2`
- `must_have`: required traits
- `exclude`: forbidden traits
- `result_limit`: optional final output cap; include only when the user explicitly asks for a capped result set

If the prompt is incomplete, leave fields empty or set them to `unknown`. Do not invent constraints.
Always preserve the default constraint that both houses and land must have at least `1000 m2` of land area unless the user explicitly changes it.
Always search sale inventory only unless the skill is explicitly redesigned for another transaction type.
Do not impose any result limit unless the user explicitly asks for one.

## Worker Prompt Pattern

Each sub-agent prompt should contain:

1. the portal name it owns
2. the shared search brief
3. a strict rule that it must search only that portal
4. the required normalized output schema
5. a rule to use `unknown` for missing fields

The prompt should explicitly say:

- search only within the provided Czech municipality
- if `location_scope = nearby_allowed`, nearby municipalities may be included; use `nearby_radius_km` only when it is explicitly provided or clearly implied by the user
- search sale listings only
- treat `chalupa` as an input-only house-like search term and normalize matching rows to `house`
- include only houses and building land
- reject house listings where parcel/land area is below `1000 m2`
- reject land listings below `1000 m2`

Portal workers should not perform cross-portal deduplication. They may remove exact duplicates within their own portal output.

## Worker Output Schema

Require this markdown table or equivalent structured rows:

| portal | title | location | property_type | price | house_area_m2 | land_area_m2 | urls | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

Field expectations:

- `portal`: one exact portal hostname for a single-source row, or multiple source hostnames when duplicate rows are merged across portals
- `title`: listing headline
- `location`: locality shown by the listing
- `property_type`: `apartment`, `house`, `land`, `commercial`, `other`, or `unknown`
- `price`: exact visible price with currency, else `unknown`
- `house_area_m2`: interior/floor area when visible, else `unknown`
- `land_area_m2`: parcel or land area when visible, else `unknown`
- `urls`: one or more direct detail-page listing URLs for the same retained property, not category/search page URLs
- `notes`: compact machine-readable qualifiers such as `snapshot-sufficient`, `duplicate-merged-from:realitymix.cz`, `duplicate-price:realitymix.cz=6390000`, `missing-public-sewer`

Rules by property type:

- `house`: populate `house_area_m2` when visible and require `land_area_m2` for the mandatory land filter
- `chalupa`: never emit as a normalized output type; search it as input and normalize matching rows to `house`
- `land`: set `house_area_m2` to `unknown` and populate `land_area_m2`
- `land`: retain only when the listing explicitly indicates buildable or residential use; exclude clearly agricultural, non-buildable, or non-residential land unless the user explicitly asks for it
- `land`: if the listing mixes residential and recreational positioning, retain it only when residential/buildable use is still explicit and record the ambiguity in `notes`
- exclude any row when `price` cannot be verified and would remain `unknown`
- exclude `house` and `land` rows when `land_area_m2` cannot be verified and would remain `unknown`
- if a portal shows one area but it is unclear whether it is floor area or parcel area, keep the uncertain field as `unknown` and explain the ambiguity in `notes`
- if the detail page is not opened, still extract the detail-page URL from the listing card, anchor target, or visible snippet when available

## Parent Merge Rules

After all workers return:

1. concatenate all worker rows
2. normalize trivial formatting differences
3. remove empty or malformed rows
4. remove rows whose `price` is `unknown`
5. remove rows that violate the `1000 m2` land minimum
   For `house`, use `land_area_m2`.
   For `land`, use `land_area_m2`.
   If `land_area_m2` is `unknown`, exclude the row.
6. run conservative deduplication
7. sort the surviving results by parsed verified price in descending order with fixed tie-breakers
8. if `result_limit` is set, truncate the final merged shortlist to at most that many rows

## Deduplication Heuristic

Treat two rows as the same property only when most of these align:

- same or nearly same locality
- same property type
- same price, a trivially rounded variant, or a small syndication drift that still looks like the same underlying listing
- same house area when known
- same land area when known
- very similar title wording

When duplicates are found:

1. merge the rows into one retained property record
2. preserve all contributing source hostnames in `portal`
3. preserve all known detail-page URLs in `urls`
4. keep the lowest verified visible price in `price`
5. record alternate source portals and alternate visible prices in `notes`
6. prefer the row with more complete data for the remaining fields
7. if completeness is tied, prefer an active detail-verified row over a snapshot-only row
8. if still tied, prefer the cleaner canonical URL and fewer `unknown` values

## Ranking Rule

- After filtering and deduplication, sort rows by parsed verified price in descending order.
- Parse the visible `price` text into a numeric CZK value in the parent before sorting. Normalize Czech number formatting and common million-style abbreviations where possible.
- If a retained row's visible verified `price` still cannot be parsed into a numeric CZK value, treat the row as malformed and exclude it before ranking.
- If two rows have the same parsed price, prefer the row with more complete data.
- If parsed price and completeness are tied, prefer an active detail-verified row over a snapshot-only row.
- If parsed price, completeness, and verification tier are tied, prefer the row with the cleaner canonical URL.
- If still tied, use a stable lexical fallback on `portal`, then `title`, then `location`.
- Because rows with `price = unknown` are excluded earlier, all surviving rows must be included in this ordering.

## Coverage Reporting

The parent agent should report:

- how many portal workers were launched
- which workers returned zero results
- how many candidate rows were gathered before deduplication
- how many rows remained after deduplication

## Extension Rule

Before adding another portal to the registry, create a portal-specific reference file that explains:

- portal scope
- recommended query patterns
- detail-page expectations
- portal-specific caveats
