# sreality.cz

## Scope

This worker owns only `sreality.cz`. It should search listings on that portal and return normalized rows to the parent orchestrator.

Default scope:

- country: `Czech Republic`
- location: user-provided municipality
- property types: `house`, `chalupa`, and `building land`
- minimum land area: `1000 m2` for both houses and land

`chalupa` is input-only and must always normalize to `house` in output.

## Current Endpoints

Use the JSON API endpoints currently shipped by the Sreality Next.js app:

- locality lookup: `GET https://www.sreality.cz/api/v1/localities/suggest`
- listing search: `GET https://www.sreality.cz/api/v1/estates/search`
- listing detail: `GET https://www.sreality.cz/api/v1/estates/{hash_id}`

Do not use the old `/api/cs/v2/...` endpoints; they currently return 404.

For municipality lookup, call `/localities/suggest` with:

- `phrase=<municipality>`
- `category=municipality_cz`
- `locality_country_id=112`
- `lang=cs`
- `limit=10`

For listing search, use snake_case backend parameter names:

- `category_type_cb=1` for sale
- `category_main_cb=2` for houses
- `category_main_cb=3` for land
- `locality_country_id=112`
- `locality_entity_id=<municipality id>`
- `locality_entity_type=municipality`
- `estate_area_from=1000`
- `limit` and `page`

If `/estates/search` returns HTTP 200 but the title is still generic, for example `Všechny reality`, treat it as an unfiltered response and fix the parameters before accepting coverage.

## Script-First Workflow

1. Run:
   `rtk proxy python3 -m reality.portal_fetchers.sreality_fetch --municipality '<municipality>' --discover-results`
2. Add `--detail-url` only for known Sreality detail URLs that should be reverified.
3. Use the script JSON as the authoritative worker payload for `coverage`, `portal_status`, `fetch_attempts`, `gaps`, and normalized `listings`.
4. Only fall back to manual row extraction if the script is genuinely broken against current API responses, and explain that failure in `gaps`.

## Normalization

- `hash_id` is the stable listing id.
- Build public listing URLs under `https://www.sreality.cz/detail/.../{hash_id}`.
- Use `advert_name` for title.
- Use `advert_description` for detail evidence and filtering.
- For price, prefer `price_summary_czk`, then `price_czk`, then `price`.
- If `price_unit_cb` or `price_summary_unit_cb` indicates a per-square-metre price, multiply the CZK amount by the verified land area before output.
- For houses, use `usable_area` or `floor_area` as `house_area_m2`, and `estate_area` as `land_area_m2`.
- For land, use `estate_area` as `land_area_m2` and set `house_area_m2` to `unknown`.
- Normalize image URLs from `advert_images` if images are needed for evidence.

## Filtering

- Search sale listings only.
- Keep results within the target municipality when `location_scope = municipality_only`.
- Exclude `chata` unless explicitly allowed.
- Exclude houses and land below `1000 m2` parcel/estate area.
- Exclude rows when price is missing or zero.
- Retain land rows only when the category or detail text supports buildable/residential use.
- Exclude agricultural, forest, meadow, and clearly non-buildable land unless explicitly allowed.

## Expected Output

Return rows in this schema:

| portal | title | location | property_type | price | house_area_m2 | land_area_m2 | urls | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

Use `sreality.cz` as the `portal` value in every single-source row.
Use one or more public `sreality.cz/detail/...` detail URLs in `urls`.
Record API verification with compact notes such as `detail-url-verified:sreality.cz`, `api-detail:https://www.sreality.cz/api/v1/estates/123`, and `price-normalized-from-per-m2`.
