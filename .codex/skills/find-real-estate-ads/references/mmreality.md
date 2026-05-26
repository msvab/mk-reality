# mmreality.cz

## Scope

This worker owns only `mmreality.cz`. It should search listings on that portal and return normalized rows to the parent orchestrator.

Default scope:

- country: `Czech Republic`
- location: user-provided municipality
- property types: `house`, `chalupa`, and `building land`
- minimum land area: `1000 m2` for both houses and land

`chalupa` is input-only and must always normalize to `house` in output.

## Search Discipline

- Keep all search and verification scoped to `mmreality.cz`.
- Use the helper script for the repeatable fetch, inactive-status checks, filtering, and normalization path:
  `rtk proxy python3 .codex/skills/find-real-estate-ads/scripts/mmreality_fetch.py --municipality '<municipality>'`
- The script accepts one or more `--result-url` inputs and/or explicit `--detail-url` inputs.
- The worker should spend tokens only on discovering relevant MM Reality result pages or explicit MM detail URLs for the target municipality.
- Prefer listing detail pages over listing-category pages.
- Use location and property-type terms from the shared search brief.
- Search sale listings only. Do not search rent inventory.
- When `location_scope = municipality_only`, restrict results to the provided municipality.
- Expand beyond the exact municipality only when `location_scope = nearby_allowed`, and use `nearby_radius_km` only when it is present in the parent brief.
- Include `chalupa` when it is listed as a house-like property, but always normalize it to `house` in output.
- Exclude `chata` unless the parent brief explicitly allows it.
- Exclude house listings whose parcel area is below `1000 m2`.
- Exclude land listings below `1000 m2`.
- Exclude listings when price is missing or cannot be verified.
- Exclude house and land listings when parcel area is missing or cannot be verified.
- Prefer buildable land and avoid non-buildable land unless the parent brief says otherwise.
- After capturing a detail URL, verify that the detail page still represents an active listing before retaining the row.
- If the detail page says the property is no longer offered by M&M Reality, drop the row as stale/inactive even if the listing card or snippet still appears elsewhere.
- MM Reality may expose multiple detail-URL patterns for the same listing number.
- Treat the generic `/nemovitosti/<id>/` URL as the authoritative status check for whether the listing is still active.
- If the generic `/nemovitosti/<id>/` page is inactive, exclude the listing entirely even if another URL variant with the same listing number still renders.
- Use alternate URL variants only as supporting evidence or fallback navigation, not to override an inactive generic `/nemovitosti/<id>/` result.
- If normal browsing does not expose enough MM Reality detail, request a narrow persistent approval for `["rtk", "proxy", "curl"]` for direct fetches.
- Do not request broad approvals such as all `curl`, all `python3`, or all proxy-wrapped commands.
- The helper script already does the authoritative generic-detail fetch and normalized filtering. Prefer returning its JSON rather than reimplementing those checks in the prompt.

## Script-First Workflow

1. Find one or more relevant MM Reality municipality-scoped result pages, or explicit detail URLs, for the in-scope categories.
2. Run the helper with:
   `rtk proxy python3 .codex/skills/find-real-estate-ads/scripts/mmreality_fetch.py --municipality '<municipality>' --result-url '<url>'`
3. Add more `--result-url` flags when multiple MM results pages are needed, and use `--detail-url '<url>'` when you only have explicit MM detail URLs.
4. Use the script JSON as the authoritative worker payload for `coverage`, `gaps`, and normalized `listings`.
5. Only fall back to manual row-by-row extraction if the script is genuinely broken against the current portal markup, and explain that failure in `gaps`.

## Expected Output

Return rows in this schema:

| portal | title | location | property_type | price | house_area_m2 | land_area_m2 | urls | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

Use `mmreality.cz` as the `portal` value in every row.

## Caveats

- If a page shows teaser data only, capture only what is visible and mark the rest as `unknown`.
- Treat text such as `Je nám líto, ale tato nemovitost již není v nabídce M&M Reality.` as a hard inactive signal and exclude that listing.
- When a listing number is visible, use it to compare MM Reality URL variants, but let the generic `/nemovitosti/<id>/` status decide whether the listing survives.
- If the portal uses inconsistent wording for property types, map them into the shared normalized set.
- Map `chalupa` into the normalized `house` class. Do not emit `chalupa` as a separate output type.
- If price is missing, set `price` to `unknown` and exclude the row.
- For house listings, populate `house_area_m2` from the visible floor/interior area when possible.
- For house and land listings, populate `land_area_m2` from the visible parcel/land area when possible.
- If a house page shows floor area clearly but parcel area is missing, set `land_area_m2` to `unknown` and exclude the row.
- Do not compare or merge with listings from other portals. That is the parent orchestrator's job.
