# reality.aktualne.cz

## Scope

This worker owns only `reality.aktualne.cz`. It should search listings on that portal and return normalized rows to the parent orchestrator.

Default scope:

- country: `Czech Republic`
- location: user-provided municipality
- property types: `house`, `chalupa`, and `building land`
- minimum land area: `1000 m2` for both houses and land

`chalupa` is input-only and must always normalize to `house` in output.

## Search Discipline

- Keep all search and verification scoped to `reality.aktualne.cz`.
- Use the helper script for the repeatable detail verification, filtering, and normalization path:
  `rtk proxy python3 .codex/skills/find-real-estate-ads/scripts/reality_aktualne_fetch.py --municipality '<municipality>'`
- The script accepts explicit `--detail-url` inputs and can also extract detail URLs from any provided `--result-url`.
- The worker should spend tokens only on discovering relevant Reality Aktuálně detail URLs or result pages for the target municipality.
- Search sale listings only. Do not search rent inventory.
- When `location_scope = municipality_only`, restrict results to the provided municipality.
- Expand beyond the exact municipality only when `location_scope = nearby_allowed`, and use `nearby_radius_km` only when it is present in the parent brief.
- Include `chalupa` when it is listed as a house-like property, but always normalize it to `house` in output.
- Exclude `chata` unless the parent brief explicitly allows it.
- Exclude house listings whose parcel area is below `1000 m2`.
- Exclude land listings below `1000 m2`.
- Exclude listings when price is missing or cannot be verified.
- Exclude house and land listings when parcel area is missing or cannot be verified.
- Retain land rows only when the listing explicitly indicates buildable or residential use.
- Exclude agricultural, non-buildable, or clearly non-residential land unless the parent brief explicitly allows it.
- If a land listing mixes residential and recreational positioning, keep it only when residential/buildable use is still explicit and record the ambiguity in `notes`.
- Prefer actual listing detail pages under `reality.aktualne.cz/detail/...` over search and category pages.
- Use narrow site-scoped queries or portal search pages to find `reality.aktualne.cz/detail/...` pages for the target municipality and property type.
- Capture the `reality.aktualne.cz/detail/...` URL for every retained row.
- If the detail page is reachable, prefer it over snippet-only extraction because the detail page typically exposes price, address/locality, house or parcel area, source agency, and listing identifier.
- The helper script already does the detail-page verification and normalized filtering. Prefer returning its JSON rather than reimplementing those checks in the prompt.

## Script-First Workflow

1. Find one or more relevant Reality Aktuálně detail URLs, or a result page that already exposes those detail URLs.
2. Run the helper with:
   `rtk proxy python3 .codex/skills/find-real-estate-ads/scripts/reality_aktualne_fetch.py --municipality '<municipality>' --detail-url '<url>'`
3. Add more `--detail-url` flags when needed, and use `--result-url '<url>'` only when that page already contains usable `reality.aktualne.cz/detail/...` links.
4. Use the script JSON as the authoritative worker payload for `coverage`, `gaps`, and normalized `listings`.
5. Only fall back to manual row-by-row extraction if the script is genuinely broken against the current portal markup, and explain that failure in `gaps`.

## Aggregator Caveat

- Treat `reality.aktualne.cz` as an aggregator, not a canonical source of unique inventory.
- Expect many rows to mirror agency and portal listings from sources such as RE/MAX, M&M Reality, or other broker sites.
- When a Reality Aktuálně row clearly mirrors another portal's listing, still return the `reality.aktualne.cz` row normally; the parent orchestrator will handle cross-portal deduplication.
- If the detail page visibly names the underlying agency or source brand, record that source in `notes`, for example `aggregator-source:remax-infinity.cz`.
- Do not replace the `reality.aktualne.cz` detail URL with an external agency URL inside the worker output. Keep the Reality Aktuálně detail URL in `urls` and let the parent merge alternate URLs from other portals later.

## Expected Output

Return rows in this schema:

| portal | title | location | property_type | price | house_area_m2 | land_area_m2 | urls | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

Use `reality.aktualne.cz` as the `portal` value in every single-source row.
Use one or more `reality.aktualne.cz/detail/...` detail URLs in `urls`. Do not put search or category pages there unless no detail URL is visible at all, and in that case prefer dropping the row over returning a non-detail URL.

## Caveats

- Detail pages often expose enough structured data to satisfy the worker contract directly, including visible `Cena`, `Plocha parcely`, `Užitná plocha`, address/locality, and a listing identifier such as `REALITYMIX-...`.
- Reality Aktuálně may mirror the same property as another supported portal with a different visible price or slightly different wording; record obvious alternate-source clues in `notes`.
- If a page shows teaser data only, capture only what is visible and mark the rest as `unknown`.
- If the portal uses inconsistent wording for property types, map them into the shared normalized set.
- Map `chalupa` into the normalized `house` class. Do not emit `chalupa` as a separate output type.
- If price is missing, set `price` to `unknown` and exclude the row.
- For house listings, populate `house_area_m2` from the visible interior or usable area when possible.
- For house and land listings, populate `land_area_m2` from `Plocha parcely` or another explicit parcel/land field when possible.
- If a house page shows house area clearly but parcel area is missing, set `land_area_m2` to `unknown` and exclude the row.
- Record recurring provenance and caveats in machine-readable `notes`, for example `aggregator-source:remax-infinity.cz`, `snapshot-sufficient`, `mixed-residential-recreational`.
- Do not compare or merge with listings from other portals. That is the parent orchestrator's job.
