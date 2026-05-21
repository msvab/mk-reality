# reality.idnes.cz

## Scope

This worker owns only `reality.idnes.cz`. It should search listings on that portal and return normalized rows to the parent orchestrator.

Default scope:

- country: `Czech Republic`
- location: user-provided municipality
- property types: `house`, `chalupa`, and `building land`
- minimum land area: `1000 m2` for both houses and land

`chalupa` is input-only and must always normalize to `house` in output.

## Search Discipline

- Keep all search and verification scoped to `reality.idnes.cz`.
- Prefer listing detail pages over listing-category pages, but do not open detail pages aggressively.
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
- Run the search in two phases.
- Phase 1: traverse only listing/search result pages, extract as much visible data as possible, and capture the detail URL from each listing card or snippet as early as possible.
- Phase 2: after Phase 1 filtering, open detail pages only for the shortlist that still needs detail-page confirmation.
- If the listing/search snapshot already provides all fields needed for filtering and normalized output, do not open the detail page.
- In Phase 2, open detail pages only for the strongest candidates or when a missing field materially affects filtering.
- Never keep more than one in-flight request active against `reality.idnes.cz` at a time.
- Cache visited search and detail URLs within the run and do not re-open the same URL unless you are explicitly retrying the request that received a `429`.
- Batch work in this order: collect visible candidates and detail URLs from listing/search pages first, then verify only the shortlist that still needs detail-page confirmation.
- Use a slower cadence for detail-page requests than for listing/search pages.
- Start with conservative pacing even before the first `429`.
- Leave at least `4` seconds between listing/search page requests to `reality.idnes.cz`.
- Leave at least `8` seconds between detail-page requests to `reality.idnes.cz`.
- Avoid bursty navigation. Space requests out and prefer direct navigation to already visible detail URLs over extra UI clicks.
- If a `429` appears, retry the exact request that received the `429` after a backoff pause.
- After the first `429`, keep the same navigation plan and request coverage, but raise the minimum gap to at least `10` seconds before any further request to the same host.
- If another `429` occurs in the same run, continue retrying the blocked request and increase the host-level backoff exponentially, for example `10s`, then `20s`, then `40s`, capped at a reasonable ceiling such as `120s`.
- Reuse detail URLs already visible on listing pages instead of forcing extra clicks when the link target is already exposed.

## Expected Output

Return rows in this schema:

| portal | title | location | property_type | price | house_area_m2 | land_area_m2 | urls | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

Use `reality.idnes.cz` as the `portal` value in every row.
Use one or more detail-page URLs in `urls`. Do not put category or search results pages there unless no detail URL is visible at all, and in that case prefer dropping the row over returning a non-detail URL.

## Caveats

- If a page shows teaser data only, capture only what is visible and mark the rest as `unknown`.
- If all required fields are already visible on the listing/search snapshot, retain the row without opening the detail page and note that the row came from a fully sufficient snapshot.
- If `429 Too Many Requests` appears, treat the portal as rate-limited and slow subsequent requests down by increasing the gaps between them.
- Always retry the exact request that received the `429` after a backoff pause so data is not dropped.
- Do not reduce the number of planned navigation requests solely because of `429`; reduce only request cadence.
- If detail-page content is temporarily blocked but the listing card already exposed the detail href, keep that href as fallback context while continuing slowed retries for the detail page.
- If the portal uses inconsistent wording for property types, map them into the shared normalized set.
- Map `chalupa` into the normalized `house` class. Do not emit `chalupa` as a separate output type.
- If price is missing, set `price` to `unknown` and exclude the row.
- For house listings, populate `house_area_m2` from the visible floor/interior area when possible.
- For house and land listings, populate `land_area_m2` from the visible parcel/land area when possible.
- If a house page shows floor area clearly but parcel area is missing, set `land_area_m2` to `unknown` and exclude the row.
- Record in `notes` when a row still depends on listing-page teaser data after slowed retries and explain which fields could not be confirmed.
- Do not compare or merge with listings from other portals. That is the parent orchestrator's job.
