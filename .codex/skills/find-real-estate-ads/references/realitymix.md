# realitymix.cz

## Scope

This worker owns only `realitymix.cz`. It should search listings on that portal and return normalized rows to the parent orchestrator.

Default scope:

- country: `Czech Republic`
- location: user-provided municipality
- property types: `house`, `chalupa`, and `building land`
- minimum land area: `1000 m2` for both houses and land

`chalupa` is input-only and must always normalize to `house` in output.

## Search Discipline

- Keep all search and verification scoped to `realitymix.cz`.
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
- Prefer actual listing detail pages over category pages, directory pages, and article content.
- Use search/detail pages first. When portal navigation is noisy, use narrow site-scoped queries to find `realitymix.cz/detail/...` pages for the target municipality and property type.
- Capture the `realitymix.cz/detail/...` URL for every retained row.
- If the listing snapshot already provides all fields needed for filtering and normalized output, the row may be retained without extra navigation.
- If the detail page is reachable, prefer it over snippet-only extraction.

## Aggregator Caveat

- Treat `realitymix.cz` as an aggregator, not a canonical source of unique inventory.
- Expect many rows to duplicate listings that also appear on agency or portal sites such as `mmreality.cz`.
- When a RealtyMix row clearly mirrors another portal's listing, still return the `realitymix.cz` row normally; the parent orchestrator will handle cross-portal deduplication.
- If the RealtyMix detail page visibly names the underlying agency, record that agency in `notes`, for example `aggregator-source:mmreality.cz`.
- Do not replace the `realitymix.cz` detail URL with an external agency URL inside the worker output. Keep the RealityMix detail URL in `urls` and let the parent merge alternate URLs from other portals later.

## Expected Output

Return rows in this schema:

| portal | title | location | property_type | price | house_area_m2 | land_area_m2 | urls | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

Use `realitymix.cz` as the `portal` value in every single-source row.
Use one or more `realitymix.cz/detail/...` URLs in `urls`. Do not put category or search results pages there unless no detail URL is visible at all, and in that case prefer dropping the row over returning a non-detail URL.

## Caveats

- RealityMix snippets often expose enough information to identify duplicates, including municipality, area, price, and a `REALITYMIX-...` listing identifier.
- RealityMix may mirror the same property as another supported portal with a different visible price or slightly different wording; record obvious alternate-source clues in `notes`.
- If a page shows teaser data only, capture only what is visible and mark the rest as `unknown`.
- If the portal uses inconsistent wording for property types, map them into the shared normalized set.
- Map `chalupa` into the normalized `house` class. Do not emit `chalupa` as a separate output type.
- If price is missing, set `price` to `unknown` and exclude the row.
- For house listings, populate `house_area_m2` from the visible floor/interior area when possible.
- For house and land listings, populate `land_area_m2` from the visible parcel/land area when possible.
- If a house page shows floor area clearly but parcel area is missing, set `land_area_m2` to `unknown` and exclude the row.
- Record recurring provenance and ambiguity in machine-readable `notes`, for example `aggregator-source:mmreality.cz`, `snapshot-sufficient`, `mixed-residential-recreational`.
- Do not compare or merge with listings from other portals. That is the parent orchestrator's job.
