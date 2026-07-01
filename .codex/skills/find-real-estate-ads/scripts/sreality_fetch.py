#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
import sys
import time
from html import unescape
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

BASE_URL = "https://www.sreality.cz"
API_BASE_URL = f"{BASE_URL}/api/v1"
USER_AGENT = "Mozilla/5.0"
DEFAULT_RETRIES = 1
DEFAULT_BACKOFF_SECONDS = 1.0
OVERPASS_MUNICIPALITIES_PATH = Path("data/overpass/municipalities.json")

CATEGORY_MAIN = {
    "house": 2,
    "land": 3,
}
CATEGORY_MAIN_SLUGS = {
    2: "dum",
    3: "pozemek",
}
TRANSACTION_SALE = 1
COUNTRY_CZ = 112
MIN_LAND_AREA_M2 = 1000
PER_SQUARE_METER_UNIT_VALUES = {3, 4, 5}


def slug_normalize(value: str) -> str:
    text = unescape(value).strip().casefold()
    replacements = {
        "á": "a",
        "ä": "a",
        "č": "c",
        "ď": "d",
        "é": "e",
        "ě": "e",
        "í": "i",
        "ň": "n",
        "ó": "o",
        "ř": "r",
        "š": "s",
        "ť": "t",
        "ú": "u",
        "ů": "u",
        "ý": "y",
        "ž": "z",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return re.sub(r"\s+", " ", text)


def path_slug(value: str | None) -> str:
    text = slug_normalize(str(value or ""))
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "-"


def classify_fetch(
    http_status: int | None,
    returncode: int,
    body: str,
    stderr: str,
    stage: str | None = None,
) -> tuple[str, str | None]:
    if returncode != 0:
        return "fetch_error", stderr.strip() or f"curl exited with {returncode}"
    if http_status == 429:
        return "rate_limited", "HTTP 429"
    if http_status is not None and http_status >= 500:
        return "fetch_error", f"HTTP {http_status}"
    if stage == "detail_fetch" and http_status == 404:
        return "not_found", "HTTP 404"
    if http_status in {401, 403}:
        return "blocked", f"HTTP {http_status}"
    if http_status is not None and http_status >= 400:
        return "blocked", f"HTTP {http_status}"
    if not body.strip():
        return "fetch_error", "empty response body"
    if body.lstrip().startswith("<"):
        return "blocked", "non-JSON HTML response"
    return "ok", None


def append_fetch_attempt(
    attempts: list[dict],
    *,
    url: str,
    stage: str,
    attempt: int,
    status: str,
    http_status: int | None = None,
    error: str | None = None,
    message: str | None = None,
) -> None:
    row = {
        "portal": "sreality.cz",
        "url": canonicalize_public_url(url) if "/detail/" in url else url,
        "stage": stage,
        "attempt": attempt,
        "status": status,
    }
    if http_status is not None:
        row["http_status"] = http_status
    if error:
        row["error"] = error
    if message:
        row["message"] = message
    attempts.append(row)


def run_json_fetch(
    url: str,
    *,
    attempts: list[dict] | None = None,
    stage: str = "fetch",
    retries: int = DEFAULT_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
) -> dict:
    last_error = None
    for attempt in range(1, retries + 2):
        completed = subprocess.run(
            ["curl", "-sL", "-A", USER_AGENT, "-w", "\n__HTTP_STATUS__:%{http_code}", url],
            check=False,
            capture_output=True,
            text=True,
        )
        body, marker, raw_http_status = completed.stdout.rpartition("\n__HTTP_STATUS__:")
        if not marker:
            body = completed.stdout
            http_status = None
        else:
            try:
                http_status = int(raw_http_status.strip())
            except ValueError:
                http_status = None
        status, error = classify_fetch(http_status, completed.returncode, body, completed.stderr, stage)
        if status == "ok":
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                status = "fetch_error"
                error = f"invalid JSON: {exc}"
            else:
                if attempts is not None:
                    append_fetch_attempt(
                        attempts,
                        url=url,
                        stage=stage,
                        attempt=attempt,
                        status="ok",
                        http_status=http_status,
                    )
                return payload
        if attempts is not None:
            append_fetch_attempt(
                attempts,
                url=url,
                stage=stage,
                attempt=attempt,
                status=status,
                http_status=http_status,
                error=error,
            )
        last_error = error or status
        if attempt <= retries:
            time.sleep(backoff_seconds * attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def expected_district_for_municipality(municipality: str) -> str | None:
    if not OVERPASS_MUNICIPALITIES_PATH.exists():
        return None
    try:
        payload = json.loads(OVERPASS_MUNICIPALITIES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    elements = []
    if isinstance(payload, dict):
        if isinstance(payload.get("elements"), list):
            elements = payload["elements"]
        elif isinstance(payload.get("response"), dict) and isinstance(payload["response"].get("elements"), list):
            elements = payload["response"]["elements"]
    target = slug_normalize(municipality)
    for element in elements:
        if not isinstance(element, dict):
            continue
        tags = element.get("tags", {})
        if not isinstance(tags, dict):
            continue
        if slug_normalize(str(tags.get("name") or tags.get("name:cs") or "")) != target:
            continue
        wikipedia = str(tags.get("wikipedia") or "")
        match = re.search(r"\(okres\s+([^)]+)\)", wikipedia, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def locality_suggest_url(municipality: str) -> str:
    params = {
        "phrase": municipality,
        "category": "municipality_cz",
        "locality_country_id": COUNTRY_CZ,
        "lang": "cs",
        "limit": 10,
    }
    return f"{API_BASE_URL}/localities/suggest?{urlencode(params)}"


def search_url(locality: dict, category_main_cb: int, *, page: int, limit: int) -> str:
    params = {
        "category_type_cb": TRANSACTION_SALE,
        "category_main_cb": category_main_cb,
        "locality_country_id": COUNTRY_CZ,
        "locality_entity_id": locality["id"],
        "locality_entity_type": locality["entity_type"],
        "estate_area_from": MIN_LAND_AREA_M2,
        "limit": limit,
        "page": page,
    }
    return f"{API_BASE_URL}/estates/search?{urlencode(params)}"


def detail_api_url(hash_id: int | str) -> str:
    return f"{API_BASE_URL}/estates/{hash_id}"


def choose_locality(payload: dict, municipality: str, expected_district: str | None = None) -> dict | None:
    target = slug_normalize(municipality)
    expected = slug_normalize(expected_district or "")
    fallback = None
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        data = item.get("userData", {})
        if not isinstance(data, dict):
            continue
        if data.get("entityType") != "municipality":
            continue
        name = str(data.get("municipality") or data.get("suggestFirstRow") or "")
        if slug_normalize(name) != target:
            continue
        candidate = {
            "id": int(data["id"]),
            "entity_type": "municipality",
            "name": name,
            "district": data.get("district"),
            "district_id": data.get("district_id"),
            "municipality_seo_name": data.get("municipality_seo_name"),
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
        }
        if expected and slug_normalize(str(data.get("district") or "")) == expected:
            return candidate
        fallback = fallback or candidate
    return fallback


def parse_detail_id(url: str) -> str | None:
    match = re.search(r"/(\d+)(?:/)?$", urlsplit(url).path)
    return match.group(1) if match else None


def canonicalize_public_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(("https", "www.sreality.cz", parts.path.rstrip("/"), "", ""))


def public_detail_url(item: dict) -> str:
    category_main = nested_value(item, "category_main_cb", "value")
    category_sub_name = nested_value(item, "category_sub_cb", "name") or "-"
    locality = item.get("locality", {}) if isinstance(item.get("locality"), dict) else {}
    city = locality.get("city_seo_name") or locality.get("municipality_seo_name") or path_slug(locality.get("city"))
    locality_parts = [city]
    for key in ("citypart_seo_name", "ward_seo_name", "street_seo_name"):
        value = locality.get(key)
        if value and value not in locality_parts:
            locality_parts.append(value)
    locality_slug = "-".join(part for part in locality_parts if part) or "-"
    return (
        f"{BASE_URL}/detail/prodej/{CATEGORY_MAIN_SLUGS.get(category_main, 'nemovitost')}/"
        f"{path_slug(category_sub_name)}/{locality_slug}/{item.get('hash_id')}"
    )


def nested_value(item: dict, *keys):
    value = item
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def response_is_filtered(payload: dict, expected_title_fragments: list[str]) -> bool:
    title = slug_normalize(str(payload.get("search_title") or ""))
    return all(slug_normalize(fragment) in title for fragment in expected_title_fragments)


def item_matches_locality(item: dict, locality: dict, expected_district: str | None = None) -> bool:
    item_locality = item.get("locality", {}) if isinstance(item.get("locality"), dict) else {}
    if item_locality.get("municipality_id") != locality.get("id"):
        return False
    if expected_district:
        district = str(item_locality.get("district") or "")
        if slug_normalize(district) != slug_normalize(expected_district):
            return False
    return True


def is_price_per_square_meter(item: dict) -> bool:
    unit_value = nested_value(item, "price_unit_cb", "value")
    summary_unit_value = nested_value(item, "price_summary_unit_cb", "value")
    unit_name = slug_normalize(
        " ".join(
            str(value or "")
            for value in (nested_value(item, "price_unit_cb", "name"), nested_value(item, "price_summary_unit_cb", "name"))
        )
    )
    return unit_value in PER_SQUARE_METER_UNIT_VALUES or summary_unit_value in PER_SQUARE_METER_UNIT_VALUES or "m2" in unit_name or "m²" in unit_name


def full_price_czk(item: dict, land_area: int | None) -> int | None:
    summary = item.get("price_summary_czk")
    if isinstance(summary, (int, float)) and summary > 0:
        return int(round(summary))
    for key in ("price_czk", "price"):
        value = item.get(key)
        if not isinstance(value, (int, float)) or value <= 0:
            continue
        amount = int(round(value))
        if is_price_per_square_meter(item) and land_area:
            return amount * land_area
        return amount
    return None


def price_normalized_from_per_square_meter(item: dict, land_area: int | None) -> bool:
    summary = item.get("price_summary_czk")
    return not (isinstance(summary, (int, float)) and summary > 0) and bool(land_area) and is_price_per_square_meter(item)


def land_is_buildable(item: dict) -> bool:
    texts = [
        str(item.get("advert_name") or ""),
        str(item.get("advert_description") or ""),
        str(nested_value(item, "category_sub_cb", "name") or ""),
    ]
    lowered = slug_normalize(" ".join(texts))
    if any(marker in lowered for marker in ["zemedel", "orna puda", "louka", "les", "nestaveb"]):
        return False
    if "zahrad" in lowered and not any(marker in lowered for marker in ["stavebni pozemek", "pro bydleni"]):
        return False
    return any(marker in lowered for marker in ["staveb", "bydleni", "rodinny", "rezidenc"])


def is_chata_title_or_category(item: dict) -> bool:
    text = slug_normalize(
        " ".join(
            [
                str(item.get("advert_name") or ""),
                str(nested_value(item, "category_sub_cb", "name") or ""),
            ]
        )
    )
    return bool(re.search(r"\bchat(?:a|y|u|ou|e|am|ami|ach)?\b", text))


def listing_from_detail(detail: dict, municipality: str, locality: dict, expected_district: str | None = None) -> tuple[dict | None, str | None]:
    item = detail.get("result") if isinstance(detail.get("result"), dict) else detail
    if not isinstance(item, dict):
        return None, "invalid-detail-json"
    if not item_matches_locality(item, locality, expected_district):
        return None, "outside-municipality"
    if nested_value(item, "category_type_cb", "value") != TRANSACTION_SALE:
        return None, "unsupported-transaction"

    category_main = nested_value(item, "category_main_cb", "value")
    if category_main == CATEGORY_MAIN["house"]:
        property_type = "house"
    elif category_main == CATEGORY_MAIN["land"]:
        property_type = "land"
    else:
        return None, "unsupported-property-type"

    title = str(item.get("advert_name") or "unknown").strip() or "unknown"
    if property_type == "house" and is_chata_title_or_category(item):
        return None, "excluded-chata"

    land_area = item.get("estate_area")
    land_area = None if not isinstance(land_area, (int, float)) else int(round(land_area))
    house_area = item.get("usable_area") or item.get("floor_area")
    house_area = None if not isinstance(house_area, (int, float)) else int(round(house_area))
    if land_area is None or land_area < MIN_LAND_AREA_M2:
        return None, "land-below-threshold"
    if property_type == "land" and not land_is_buildable(item):
        return None, "non-buildable-land"

    price_czk = full_price_czk(item, land_area)
    if price_czk is None:
        return None, "missing-price"

    item_locality = item.get("locality", {}) if isinstance(item.get("locality"), dict) else {}
    location_parts = [
        item_locality.get("city"),
        item_locality.get("street"),
        item_locality.get("district"),
    ]
    location = ", ".join(str(part) for part in location_parts if part) or municipality
    notes = ["detail-url-verified:sreality.cz", f"api-detail:{detail_api_url(item.get('hash_id'))}"]
    if property_type == "land":
        notes.append("buildable-land")
    if price_normalized_from_per_square_meter(item, land_area):
        notes.append("price-normalized-from-per-m2")

    return {
        "portal": ["sreality.cz"],
        "title": title,
        "location": location,
        "property_type": property_type,
        "price": f"{price_czk:,} Kč".replace(",", " "),
        "house_area_m2": str(house_area) if house_area is not None and property_type == "house" else "unknown",
        "land_area_m2": str(land_area),
        "urls": [public_detail_url(item)],
        "notes": notes,
    }, None


def build_portal_status(fetch_attempts: list[dict], listings: list[dict]) -> dict:
    status_order = {
        "ok": 0,
        "no_results": 1,
        "blocked": 6,
        "fetch_error": 7,
        "rate_limited": 9,
    }
    status = "ok" if listings else "no_results"
    chosen_attempt = None
    for attempt in fetch_attempts:
        attempt_status = str(attempt.get("status", "unknown"))
        if attempt_status in {"ok", "no_results"}:
            continue
        if status_order.get(attempt_status, -1) > status_order.get(status, -1):
            status = attempt_status
            chosen_attempt = attempt
    output = {"status": status}
    if chosen_attempt:
        if chosen_attempt.get("http_status") is not None:
            output["http_status"] = chosen_attempt["http_status"]
        if chosen_attempt.get("stage"):
            output["stage"] = chosen_attempt["stage"]
        message = chosen_attempt.get("error") or chosen_attempt.get("message")
        if message:
            output["message"] = message
        output["evidence"] = [
            ":".join(str(chosen_attempt.get(key, "")) for key in ("stage", "status", "url")).rstrip(":")
        ]
    elif listings:
        output["message"] = "Retained at least one detail-verified Sreality row."
    else:
        output["message"] = "No retained in-scope Sreality rows."
    return output


def is_not_found_error(exc: RuntimeError) -> bool:
    return "HTTP 404" in str(exc)


def build_output(
    municipality: str,
    location_scope: str,
    include_houses: bool,
    include_land: bool,
    detail_urls: list[str],
    limit: int = 60,
) -> dict:
    coverage = {
        "workers_launched": 1,
        "workers_with_results": 0,
        "candidates_gathered": 0,
        "rows_retained": 0,
        "zero_result_portals": [],
        "blocked_portals": [],
    }
    gaps: list[str] = []
    fetch_attempts: list[dict] = []
    listings: list[dict] = []
    excluded_candidates: list[dict] = []
    expected_district = expected_district_for_municipality(municipality)

    try:
        suggest_payload = run_json_fetch(locality_suggest_url(municipality), attempts=fetch_attempts, stage="locality_suggest")
    except RuntimeError as exc:
        gaps.append(f"failed-locality-suggest:{exc}")
        coverage["blocked_portals"].append(f"sreality.cz locality suggest failed: {exc}")
        return empty_output(municipality, location_scope, include_houses, include_land, coverage, fetch_attempts, gaps)
    locality = choose_locality(suggest_payload, municipality, expected_district)
    if not locality:
        gaps.append("municipality-not-found")
        return empty_output(municipality, location_scope, include_houses, include_land, coverage, fetch_attempts, gaps)

    candidate_ids: set[str] = set()
    categories = []
    if include_houses:
        categories.append(("house", CATEGORY_MAIN["house"]))
    if include_land:
        categories.append(("land", CATEGORY_MAIN["land"]))
    for property_type, category_main in categories:
        page = 1
        while True:
            url = search_url(locality, category_main, page=page, limit=limit)
            try:
                payload = run_json_fetch(url, attempts=fetch_attempts, stage=f"{property_type}_search_fetch")
            except RuntimeError as exc:
                gaps.append(f"failed-search-fetch:{url}:{exc}")
                coverage["blocked_portals"].append(f"sreality.cz result fetch failed: {url}: {exc}")
                break
            if not response_is_filtered(payload, [municipality, "od 1 000"]):
                gaps.append(f"unfiltered-search-response:{url}")
                append_fetch_attempt(
                    fetch_attempts,
                    url=url,
                    stage=f"{property_type}_search_parse",
                    attempt=1,
                    status="fetch_error",
                    message="Search response did not include expected municipality/area filters.",
                )
                break
            results = payload.get("results", [])
            if not isinstance(results, list):
                break
            for item in results:
                if isinstance(item, dict) and item.get("hash_id") is not None and item_matches_locality(item, locality, expected_district):
                    candidate_ids.add(str(item["hash_id"]))
            pagination = payload.get("pagination", {})
            total = int(pagination.get("total", 0) or 0) if isinstance(pagination, dict) else 0
            if page * limit >= total or not results:
                break
            page += 1

    for detail_url in detail_urls:
        detail_id = parse_detail_id(detail_url)
        if detail_id:
            candidate_ids.add(detail_id)
        else:
            gaps.append(f"invalid-detail-url:{detail_url}")

    coverage["candidates_gathered"] = len(candidate_ids)
    for candidate_id in sorted(candidate_ids):
        try:
            detail_payload = run_json_fetch(detail_api_url(candidate_id), attempts=fetch_attempts, stage="detail_fetch")
        except RuntimeError as exc:
            if is_not_found_error(exc):
                gaps.append(f"stale-detail-fetch:{candidate_id}:{exc}")
                excluded_candidates.append(
                    {
                        "portal": "sreality.cz",
                        "url": detail_api_url(candidate_id),
                        "reason": "not-found",
                    }
                )
                continue
            gaps.append(f"failed-detail-fetch:{candidate_id}:{exc}")
            coverage["blocked_portals"].append(f"sreality.cz detail fetch failed: {candidate_id}: {exc}")
            continue
        listing, reason = listing_from_detail(detail_payload, municipality, locality, expected_district)
        if listing is not None:
            if listing["property_type"] == "house" and not include_houses:
                continue
            if listing["property_type"] == "land" and not include_land:
                continue
            listings.append(listing)
        elif reason:
            gaps.append(f"{reason}:{detail_api_url(candidate_id)}")
            excluded_candidates.append(
                {
                    "portal": "sreality.cz",
                    "url": detail_api_url(candidate_id),
                    "reason": reason,
                }
            )

    listings.sort(key=lambda item: int(re.sub(r"[^\d]", "", item["price"]) or "0"), reverse=True)
    coverage["rows_retained"] = len(listings)
    coverage["workers_with_results"] = 1 if listings else 0
    if not listings:
        coverage["zero_result_portals"].append("sreality.cz")

    output = base_output(municipality, location_scope, include_houses, include_land)
    output.update(
        {
            "coverage": coverage,
            "portal_status": {"sreality.cz": build_portal_status(fetch_attempts, listings)},
            "fetch_attempts": fetch_attempts,
            "gaps": list(dict.fromkeys(gaps)),
            "listings": listings,
        }
    )
    if excluded_candidates:
        output["excluded_candidates"] = excluded_candidates
    return output


def base_output(municipality: str, location_scope: str, include_houses: bool, include_land: bool) -> dict:
    return {
        "city": municipality,
        "query": {
            "municipality": municipality,
            "location_scope": location_scope,
            "country": "Czech Republic",
            "property_types": [
                item
                for item, enabled in (("house", include_houses), ("chalupa", include_houses), ("land", include_land))
                if enabled
            ],
            "land_size_min_m2": MIN_LAND_AREA_M2,
        },
        "assumptions": [],
    }


def empty_output(
    municipality: str,
    location_scope: str,
    include_houses: bool,
    include_land: bool,
    coverage: dict,
    fetch_attempts: list[dict],
    gaps: list[str],
) -> dict:
    coverage["zero_result_portals"] = ["sreality.cz"]
    output = base_output(municipality, location_scope, include_houses, include_land)
    output.update(
        {
            "coverage": coverage,
            "portal_status": {"sreality.cz": build_portal_status(fetch_attempts, [])},
            "fetch_attempts": fetch_attempts,
            "gaps": list(dict.fromkeys(gaps)),
            "listings": [],
        }
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch and normalize sreality.cz listings for one municipality.")
    parser.add_argument("--municipality", required=True)
    parser.add_argument("--location-scope", default="municipality_only")
    parser.add_argument("--include-houses", action="store_true", default=True)
    parser.add_argument("--include-land", action="store_true", default=True)
    parser.add_argument("--detail-url", action="append", default=[])
    parser.add_argument("--discover-results", action="store_true", help="Accepted for compatibility; Sreality always searches the live API.")
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--output")
    args = parser.parse_args()

    payload = build_output(
        municipality=args.municipality,
        location_scope=args.location_scope,
        include_houses=args.include_houses,
        include_land=args.include_land,
        detail_urls=args.detail_url,
        limit=args.limit,
    )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
