import argparse
import json
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .build_real_estate_ads_by_city import (
    ad_identity_keys,
    build_aggregate_output,
    index_previous_ads,
    load_school_cities,
)
from .build_real_estate_ads_json import build_output
from .paths import (
    REAL_ESTATE_ADS_BY_CITY_PATH,
    REAL_ESTATE_EXEC_SCHEMA_PATH,
    REAL_ESTATE_RAW_DIR,
    REAL_ESTATE_RUN_STATE_PATH,
    SCHOOLS_JSON_PATH,
)

DEFAULT_RAW_DIR = REAL_ESTATE_RAW_DIR
DEFAULT_STATE_PATH = REAL_ESTATE_RUN_STATE_PATH
DEFAULT_AGGREGATE_PATH = REAL_ESTATE_ADS_BY_CITY_PATH
DEFAULT_SCHEMA_PATH = REAL_ESTATE_EXEC_SCHEMA_PATH
LOCAL_FETCHERS = {
    "reality.idnes.cz": "reality.portal_fetchers.reality_idnes_fetch",
    "realitymix.cz": "reality.portal_fetchers.realitymix_fetch",
    "reality.aktualne.cz": "reality.portal_fetchers.reality_aktualne_fetch",
    "sreality.cz": "reality.portal_fetchers.sreality_fetch",
}
SUPPORTED_PORTALS = tuple(LOCAL_FETCHERS)
REALITY_IDNES_PORTAL = "reality.idnes.cz"
REALITY_IDNES_CIRCUIT_BREAKER_THRESHOLD = 3

STOP_REQUESTED = False


class LocalFetcherBlockedError(RuntimeError):
    def __init__(self, portal: str, blocked_portals: list[str]) -> None:
        self.portal = portal
        self.blocked_portals = blocked_portals
        super().__init__(f"{portal} local fetch reported blocked or failed requests: {blocked_portals}")


class ProviderCircuitBreaker:
    def __init__(self, threshold: int = REALITY_IDNES_CIRCUIT_BREAKER_THRESHOLD) -> None:
        self.threshold = threshold
        self.disabled_portals: set[str] = set()
        self.consecutive_failures: dict[str, int] = {}

    def effective_local_portals(self, local_portals: set[str] | None) -> set[str]:
        portals = set(local_portals) if local_portals is not None else set(SUPPORTED_PORTALS)
        return portals - self.disabled_portals

    def record_success(self, portal: str) -> None:
        self.consecutive_failures.pop(portal, None)

    def record_failure(self, exc: LocalFetcherBlockedError) -> bool:
        if exc.portal != REALITY_IDNES_PORTAL:
            return False
        count = self.consecutive_failures.get(exc.portal, 0) + 1
        self.consecutive_failures[exc.portal] = count
        if count >= self.threshold:
            self.disabled_portals.add(exc.portal)
            return True
        return False


def request_stop(_signum, _frame) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def slugify_city(city: str) -> str:
    slug = city.strip().lower()
    replacements = {
        "á": "a", "ä": "a", "č": "c", "ď": "d", "é": "e", "ě": "e",
        "í": "i", "ň": "n", "ó": "o", "ř": "r", "š": "s", "ť": "t",
        "ú": "u", "ů": "u", "ý": "y", "ž": "z",
    }
    for src, dst in replacements.items():
        slug = slug.replace(src, dst)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-") or "city"


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "updated_at": None,
            "status": "idle",
            "schools_input": None,
            "raw_dir": None,
            "aggregate_output": None,
            "completed_cities": [],
            "failed_cities": {},
            "last_completed_city": None,
            "current_city": None,
            "remaining_cities": [],
            "daily_refresh": {},
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(
    path: Path,
    state: dict,
    schools_input: Path,
    raw_dir: Path,
    aggregate_output: Path,
    completed_cities: list[str],
    failed_cities: dict,
    current_city: str | None,
    remaining_cities: list[str],
    status: str,
) -> None:
    state.update(
        {
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "status": status,
            "schools_input": str(schools_input),
            "raw_dir": str(raw_dir),
            "aggregate_output": str(aggregate_output),
            "completed_cities": completed_cities,
            "failed_cities": failed_cities,
            "last_completed_city": completed_cities[-1] if completed_cities else None,
            "current_city": current_city,
            "remaining_cities": remaining_cities,
        }
    )
    atomic_write_json(path, state)


def today_string() -> str:
    return time.strftime("%Y-%m-%d")


def daily_refresh_city_completed_today(state: dict, city: str, today: str | None = None) -> bool:
    today = today or today_string()
    daily_refresh = state.get("daily_refresh", {})
    if not isinstance(daily_refresh, dict):
        return False
    cities = daily_refresh.get("cities", {})
    if not isinstance(cities, dict):
        return False
    city_state = cities.get(city, {})
    if not isinstance(city_state, dict):
        return False
    return city_state.get("last_completed_on") == today


def select_cities(all_cities: list[str], city: str | None = None) -> list[str]:
    if city is None:
        return all_cities
    requested = city.strip()
    for known_city in all_cities:
        if known_city == requested:
            return [known_city]
    requested_slug = slugify_city(requested)
    matches = [known_city for known_city in all_cities if slugify_city(known_city) == requested_slug]
    if not matches:
        raise ValueError(f"unknown city: {city}")
    if len(matches) > 1:
        raise ValueError(f"city is ambiguous: {city}")
    return matches


def record_daily_refresh_city_completion(
    path: Path,
    state: dict,
    *,
    city: str,
) -> None:
    daily_refresh = state.get("daily_refresh", {})
    if not isinstance(daily_refresh, dict):
        daily_refresh = {}
    cities = daily_refresh.get("cities", {})
    if not isinstance(cities, dict):
        cities = {}
    cities[city] = {
        "last_completed_on": today_string(),
        "last_completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    daily_refresh["cities"] = cities
    state["daily_refresh"] = daily_refresh
    atomic_write_json(path, state)


def cached_ads_for_prompt(previous_aggregate: dict | None, city: str) -> list[dict]:
    if not isinstance(previous_aggregate, dict):
        return []
    cities = previous_aggregate.get("cities", {})
    if not isinstance(cities, dict):
        return []
    bundle = cities.get(city, {})
    if not isinstance(bundle, dict):
        return []
    ads = bundle.get("ads", [])
    if not isinstance(ads, list):
        ads = []
    hidden_ads = bundle.get("hidden_ads", [])
    if not isinstance(hidden_ads, list):
        hidden_ads = []
    out = []
    for status, ad in [*[("active", ad) for ad in ads], *[("hidden", ad) for ad in hidden_ads]]:
        if not isinstance(ad, dict):
            continue
        out.append(
            {
                "status": ad.get("status") or status,
                "title": ad.get("title"),
                "location": ad.get("location"),
                "property_type": ad.get("property_type"),
                "price": ad.get("price"),
                "house_area_m2": ad.get("house_area_m2"),
                "land_area_m2": ad.get("land_area_m2"),
                "urls": ad.get("urls", []),
            }
        )
    return out


def cached_detail_urls_by_portal(previous_aggregate: dict | None, city: str) -> dict[str, list[str]]:
    urls_by_portal = {portal: [] for portal in LOCAL_FETCHERS}
    if not isinstance(previous_aggregate, dict):
        return urls_by_portal
    cities = previous_aggregate.get("cities", {})
    if not isinstance(cities, dict):
        return urls_by_portal
    bundle = cities.get(city, {})
    if not isinstance(bundle, dict):
        return urls_by_portal
    ads = bundle.get("ads", [])
    if not isinstance(ads, list):
        ads = []
    hidden_ads = bundle.get("hidden_ads", [])
    if not isinstance(hidden_ads, list):
        hidden_ads = []
    for ad in [*ads, *hidden_ads]:
        if not isinstance(ad, dict):
            continue
        for url in ad.get("urls", []):
            text = str(url)
            for portal in urls_by_portal:
                if portal in text and text not in urls_by_portal[portal]:
                    urls_by_portal[portal].append(text)
    return urls_by_portal


def cached_portal_fetch_attempts(previous_aggregate: dict | None, city: str, portal: str) -> list[dict]:
    if not isinstance(previous_aggregate, dict):
        return []
    cities = previous_aggregate.get("cities", {})
    if not isinstance(cities, dict):
        return []
    bundle = cities.get(city, {})
    if not isinstance(bundle, dict):
        return []
    fetch_attempts = bundle.get("fetch_attempts", [])
    if not isinstance(fetch_attempts, list):
        return []
    return [attempt for attempt in fetch_attempts if isinstance(attempt, dict) and attempt.get("portal") == portal]


def cached_realitymix_result_page_urls(previous_aggregate: dict | None, city: str) -> dict[str, str]:
    urls = {}
    for attempt in cached_portal_fetch_attempts(previous_aggregate, city, "realitymix.cz"):
        if attempt.get("status") != "ok":
            continue
        stage = attempt.get("stage")
        url = str(attempt.get("url", ""))
        if "/reality/" not in url:
            continue
        if stage == "house_result_fetch":
            urls.setdefault("house", url)
        elif stage == "land_result_fetch":
            urls.setdefault("land", url)
    return urls


def cached_reality_aktualne_result_page_urls(previous_aggregate: dict | None, city: str) -> list[str]:
    urls = []
    for attempt in cached_portal_fetch_attempts(previous_aggregate, city, "reality.aktualne.cz"):
        if attempt.get("status") != "ok":
            continue
        url = str(attempt.get("url", ""))
        if "reality.aktualne.cz" not in url:
            continue
        if "/vyhledavani/" not in url or "/detail/" in url:
            continue
        if url not in urls:
            urls.append(url)
    return urls


def cached_reality_idnes_result_page_urls(previous_aggregate: dict | None, city: str) -> list[str]:
    urls = []
    for attempt in cached_portal_fetch_attempts(previous_aggregate, city, "reality.idnes.cz"):
        if attempt.get("status") != "ok":
            continue
        url = str(attempt.get("url", ""))
        if "reality.idnes.cz" not in url:
            continue
        if "/s/prodej/" not in url or "/detail/" in url:
            continue
        if url not in urls:
            urls.append(url)
    return urls


def combine_local_fetcher_payloads(city: str, payloads: list[dict]) -> dict:
    coverage = {
        "workers_launched": len(payloads),
        "workers_with_results": 0,
        "candidates_gathered": 0,
        "rows_retained": 0,
        "zero_result_portals": [],
        "blocked_portals": [],
    }
    assumptions = [
        "local-first cached detail verification was used; cached iDNES, RealityMix, Reality Aktuálně, and Sreality rows were refreshed where supported."
    ]
    gaps = []
    listings = []
    portal_status = {}
    fetch_attempts = []

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        raw_coverage = payload.get("coverage", {})
        if isinstance(raw_coverage, dict):
            coverage["workers_with_results"] += int(raw_coverage.get("workers_with_results", 0) or 0)
            coverage["candidates_gathered"] += int(raw_coverage.get("candidates_gathered", 0) or 0)
            coverage["rows_retained"] += int(raw_coverage.get("rows_retained", 0) or 0)
            coverage["zero_result_portals"].extend(raw_coverage.get("zero_result_portals", []))
            coverage["blocked_portals"].extend(raw_coverage.get("blocked_portals", []))
        assumptions.extend(item for item in payload.get("assumptions", []) if isinstance(item, str))
        gaps.extend(item for item in payload.get("gaps", []) if isinstance(item, str))
        listings.extend(item for item in payload.get("listings", []) if isinstance(item, dict))
        raw_status = payload.get("portal_status", {})
        if isinstance(raw_status, dict):
            portal_status.update(raw_status)
        fetch_attempts.extend(item for item in payload.get("fetch_attempts", []) if isinstance(item, dict))

    return {
        "city": city,
        "query": {
            "municipality": city,
            "location_scope": "municipality_only",
            "country": "Czech Republic",
            "property_types": ["house", "chalupa", "land"],
            "price_min": None,
            "price_max": None,
            "house_size_min_m2": None,
            "house_size_max_m2": None,
            "land_size_min_m2": 1000,
            "land_size_max_m2": None,
            "must_have": ["sale listings", "buildable or residential land only"],
            "exclude": ["chata"],
        },
        "assumptions": list(dict.fromkeys(assumptions)),
        "coverage": coverage,
        "portal_status": portal_status,
        "fetch_attempts": fetch_attempts,
        "gaps": list(dict.fromkeys(gaps)),
        "listings": listings,
    }


def listing_portals(listing: dict) -> set[str]:
    portals = listing.get("portal", [])
    if isinstance(portals, str):
        portals = [portals]
    if not isinstance(portals, list):
        return set()
    return {str(portal) for portal in portals}


def payload_portals(payload: dict) -> set[str]:
    portals = set()
    portal_status = payload.get("portal_status", {})
    if isinstance(portal_status, dict):
        portals.update(str(portal) for portal in portal_status)
    coverage = payload.get("coverage", {})
    if isinstance(coverage, dict):
        for portal in coverage.get("zero_result_portals", []):
            portals.add(str(portal))
    for attempt in payload.get("fetch_attempts", []):
        if isinstance(attempt, dict) and attempt.get("portal"):
            portals.add(str(attempt["portal"]))
    for listing in payload.get("listings", []):
        if isinstance(listing, dict):
            portals.update(listing_portals(listing))
    return portals & set(SUPPORTED_PORTALS)


def merge_local_payload_into_existing_raw(existing_payload: dict, local_payload: dict) -> dict:
    portals = payload_portals(local_payload)
    if not portals:
        return local_payload

    merged = dict(existing_payload)
    merged["city"] = local_payload.get("city") or existing_payload.get("city")
    if "query" not in merged and isinstance(local_payload.get("query"), dict):
        merged["query"] = local_payload["query"]

    existing_listings = existing_payload.get("listings", [])
    if not isinstance(existing_listings, list):
        existing_listings = []
    local_listings = local_payload.get("listings", [])
    if not isinstance(local_listings, list):
        local_listings = []
    merged["listings"] = [
        listing
        for listing in existing_listings
        if not (isinstance(listing, dict) and listing_portals(listing) & portals)
    ] + [listing for listing in local_listings if isinstance(listing, dict)]

    existing_status = existing_payload.get("portal_status", {})
    if not isinstance(existing_status, dict):
        existing_status = {}
    local_status = local_payload.get("portal_status", {})
    if not isinstance(local_status, dict):
        local_status = {}
    merged_status = dict(existing_status)
    for portal in portals:
        merged_status.pop(portal, None)
    for portal, status in local_status.items():
        if portal in portals:
            merged_status[portal] = status
    merged["portal_status"] = merged_status

    existing_attempts = existing_payload.get("fetch_attempts", [])
    if not isinstance(existing_attempts, list):
        existing_attempts = []
    local_attempts = local_payload.get("fetch_attempts", [])
    if not isinstance(local_attempts, list):
        local_attempts = []
    merged["fetch_attempts"] = [
        attempt
        for attempt in existing_attempts
        if not (isinstance(attempt, dict) and str(attempt.get("portal")) in portals)
    ] + [attempt for attempt in local_attempts if isinstance(attempt, dict)]

    existing_gaps = [item for item in existing_payload.get("gaps", []) if isinstance(item, str)]
    local_gaps = [item for item in local_payload.get("gaps", []) if isinstance(item, str)]
    merged["gaps"] = list(dict.fromkeys(existing_gaps + local_gaps))
    existing_assumptions = [item for item in existing_payload.get("assumptions", []) if isinstance(item, str)]
    local_assumptions = [item for item in local_payload.get("assumptions", []) if isinstance(item, str)]
    merged["assumptions"] = list(dict.fromkeys(existing_assumptions + local_assumptions))

    retained_portals = set()
    for listing in merged["listings"]:
        if isinstance(listing, dict):
            retained_portals.update(listing_portals(listing))
    merged_statuses = merged.get("portal_status", {})
    status_portals = set(merged_statuses) if isinstance(merged_statuses, dict) else set()
    merged["coverage"] = {
        "workers_launched": len(status_portals),
        "workers_with_results": len(retained_portals & status_portals),
        "candidates_gathered": len(merged["listings"]),
        "rows_retained": len(merged["listings"]),
        "zero_result_portals": [
            portal
            for portal, status in merged_statuses.items()
            if isinstance(status, dict) and status.get("status") == "no_results"
        ],
        "blocked_portals": [],
    }
    return merged


def run_local_fetchers(
    city: str,
    repo_root: Path,
    output_path: Path,
    previous_aggregate: dict | None,
    local_portals: set[str] | None = None,
    merge_local_results: bool = False,
) -> bool:
    urls_by_portal = cached_detail_urls_by_portal(previous_aggregate, city)
    realitymix_result_urls = cached_realitymix_result_page_urls(previous_aggregate, city)
    reality_aktualne_result_urls = cached_reality_aktualne_result_page_urls(previous_aggregate, city)
    reality_idnes_result_urls = cached_reality_idnes_result_page_urls(previous_aggregate, city)
    payloads = []
    for portal, urls in urls_by_portal.items():
        if local_portals is not None and portal not in local_portals:
            continue
        use_reality_idnes_results = portal == "reality.idnes.cz" and bool(reality_idnes_result_urls)
        discover_reality_idnes = portal == "reality.idnes.cz"
        discover_realitymix = portal == "realitymix.cz"
        use_reality_aktualne_results = portal == "reality.aktualne.cz" and bool(reality_aktualne_result_urls)
        discover_reality_aktualne = portal == "reality.aktualne.cz"
        discover_sreality = portal == "sreality.cz"
        if (
            not urls
            and not use_reality_idnes_results
            and not discover_reality_idnes
            and not discover_realitymix
            and not use_reality_aktualne_results
            and not discover_reality_aktualne
            and not discover_sreality
        ):
            continue
        cmd = [sys.executable, "-m", LOCAL_FETCHERS[portal], "--municipality", city]
        if use_reality_idnes_results:
            for result_url in reality_idnes_result_urls:
                cmd.extend(["--result-url", result_url])
        if discover_reality_idnes:
            cmd.append("--discover-results")
        if use_reality_aktualne_results:
            for result_url in reality_aktualne_result_urls:
                cmd.extend(["--result-url", result_url])
        if discover_reality_aktualne:
            cmd.append("--discover-results")
        if discover_sreality:
            cmd.append("--discover-results")
        if discover_realitymix:
            cmd.append("--discover-results")
            if realitymix_result_urls.get("house"):
                cmd.extend(["--house-page-url", realitymix_result_urls["house"]])
            if realitymix_result_urls.get("land"):
                cmd.extend(["--land-page-url", realitymix_result_urls["land"]])
        for url in urls:
            cmd.extend(["--detail-url", url])
        completed = subprocess.run(cmd, check=True, capture_output=True, text=True)
        payload = json.loads(completed.stdout)
        if isinstance(payload, dict):
            coverage = payload.get("coverage", {})
            blocked_portals = coverage.get("blocked_portals", []) if isinstance(coverage, dict) else []
            if blocked_portals:
                raise LocalFetcherBlockedError(portal, blocked_portals)
            payloads.append(payload)

    if not payloads:
        return False

    combined = combine_local_fetcher_payloads(city, payloads)
    if merge_local_results:
        if not output_path.exists():
            return False
        existing_payload = load_previous_aggregate(output_path)
        combined = merge_local_payload_into_existing_raw(existing_payload, combined)
    validate_raw_output(combined, city)
    atomic_write_json(output_path, combined)
    return True


def build_prompt(city: str, cached_ads: list[dict] | None = None) -> str:
    cached_ads = cached_ads or []
    cache_block = ""
    if cached_ads:
        cache_block = f"""
Known ads from the previous local aggregate are provided below. Treat them as a cache, not as proof that the ad is still active. Rows with `status: hidden` were previously seen but missing from the latest successful refresh.

Use this cache to avoid unnecessary detail lookups for ads whose current search result still exposes the same URL/title/location/price/areas. Still run current municipality-level searches so new ads can be discovered and missing cached ads can be omitted from the latest `listings` array.

Previous ads:
{json.dumps(cached_ads, ensure_ascii=False, indent=2)}
"""

    return f"""Use the `find-real-estate-ads` skill.

Search only sale listings in the Czech municipality `{city}`.

Return only JSON matching the provided output schema. Do not return markdown. Do not wrap the JSON in code fences.

Requirements:
- country: Czech Republic
- location_scope: municipality_only
- property_types: house, chalupa, land
- land_size_min_m2: 1000
- exclude chata
- keep only buildable or residential land
- use the skill's deduplication and ranking rules
- if no credible listings are found, still return a valid JSON object with an empty `listings` array and explain any coverage/gaps in `gaps`
- do not spawn sub-agents in this run; use the skill's documented single-agent fallback while preserving the same portal-by-portal discipline
- include `portal_status` for each checked supported portal so fetch problems are machine-readable; do not hide 429/rate-limit, DNS, timeout, cache-miss, inactive, or fallback-page evidence only in `gaps`
- include `fetch_attempts` for direct portal/search/detail fetches where available, especially every retry or failed detail fetch

Set:
- `city` to `{city}`
- `query.municipality` to `{city}`
- `query.location_scope` to `municipality_only`
- `query.country` to `Czech Republic`
- `query.property_types` to [\"house\", \"chalupa\", \"land\"]
- `query.land_size_min_m2` to 1000

For `portal_status`, use portal domains as keys and objects with:
- include all four supported portal keys: `reality.idnes.cz`, `realitymix.cz`, `reality.aktualne.cz`, and `sreality.cz`
- `status`: one of `ok`, `no_results`, `rate_limited`, `fetch_error`, `dns_error`, `timeout`, `blocked`, `inactive`, `fallback_page`, `partial`, or `unknown`
- `http_status`: include numeric HTTP code when known, e.g. `429`
- `stage`: where it happened, e.g. `search_fetch`, `detail_fetch`, `helper_fetch`, or `browser_open`
- `retained_from_snapshot`: true when a row was kept from a listing/search/indexed snapshot because detail fetch failed
- `message` and `evidence`: concise human-readable detail
- include every key; use null for unknown `http_status`, `stage`, or `retained_from_snapshot`, an empty string for `message`, and an empty array for `evidence`

For `fetch_attempts`, include objects with:
- `portal`, `url`, `stage`, `attempt`, and `status`
- `http_status` when known
- `error` or `message` when the attempt failed or fell back
- include every key; use null for unknown `http_status`, `error`, or `message`
{cache_block}
"""


def validate_raw_output(payload: dict, city: str) -> None:
    if not isinstance(payload, dict):
        raise ValueError("raw output must be a JSON object")
    payload_city = str(payload.get("city", "")).strip()
    if not payload_city:
        query = payload.get("query", {})
        if isinstance(query, dict):
            payload_city = str(query.get("municipality", "")).strip()
    if payload_city != city:
        raise ValueError(f"raw output city mismatch: expected {city!r}, got {payload_city!r}")
    build_output(payload)


def load_previous_aggregate(path: Path) -> dict | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def aggregate_outputs(schools_input: Path, raw_dir: Path, aggregate_output: Path, previous_aggregate: dict | None = None) -> None:
    payload = build_aggregate_output(schools_input, raw_dir, previous_aggregate=previous_aggregate)
    atomic_write_json(aggregate_output, payload)


def city_bundle(aggregate: dict | None, city: str) -> dict:
    if not isinstance(aggregate, dict):
        return {}
    cities = aggregate.get("cities", {})
    if not isinstance(cities, dict):
        return {}
    bundle = cities.get(city, {})
    return bundle if isinstance(bundle, dict) else {}


def city_refresh_summary(current_aggregate: dict, previous_aggregate: dict | None, city: str) -> dict:
    bundle = city_bundle(current_aggregate, city)
    previous_bundle = city_bundle(previous_aggregate, city)
    previous_index = index_previous_ads(previous_bundle)
    active_ads = bundle.get("ads", [])
    hidden_ads = bundle.get("hidden_ads", [])
    previous_active_ads = previous_bundle.get("ads", [])
    previous_hidden_ads = previous_bundle.get("hidden_ads", [])
    if not isinstance(active_ads, list):
        active_ads = []
    if not isinstance(hidden_ads, list):
        hidden_ads = []
    if not isinstance(previous_active_ads, list):
        previous_active_ads = []
    if not isinstance(previous_hidden_ads, list):
        previous_hidden_ads = []

    new_ads = 0
    price_changed = 0
    for ad in active_ads:
        if not isinstance(ad, dict):
            continue
        previous = next((previous_index[key][0] for key in ad_identity_keys(ad) if key in previous_index), None)
        if not previous:
            new_ads += 1
        elif previous.get("price_czk") != ad.get("price_czk"):
            price_changed += 1

    return {
        "active": len(active_ads),
        "active_delta": len(active_ads) - len(previous_active_ads),
        "hidden": len(hidden_ads),
        "hidden_delta": len(hidden_ads) - len(previous_hidden_ads),
        "new": new_ads,
        "price_changed": price_changed,
    }


def format_delta(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


def print_city_refresh_summary(aggregate_output: Path, previous_aggregate: dict | None, city: str) -> None:
    current_aggregate = load_previous_aggregate(aggregate_output)
    summary = city_refresh_summary(current_aggregate or {}, previous_aggregate, city)
    print(
        "  summary: "
        f"active={summary['active']} ({format_delta(summary['active_delta'])}) "
        f"hidden={summary['hidden']} ({format_delta(summary['hidden_delta'])}) "
        f"new={summary['new']} "
        f"price_changed={summary['price_changed']}",
        flush=True,
    )


def run_city(
    city: str,
    repo_root: Path,
    schema_path: Path,
    output_path: Path,
    codex_bin: str,
    model: str | None,
    cached_ads: list[dict] | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output_path.parent, delete=False, suffix=".json") as handle:
        temp_output_path = Path(handle.name)
    cmd = [
        codex_bin,
        "--search",
        "exec",
        "--color",
        "never",
        "-C",
        str(repo_root),
        "--output-schema",
        str(schema_path),
        "-o",
        str(temp_output_path),
        build_prompt(city, cached_ads=cached_ads),
    ]
    if model:
        cmd[2:2] = ["--model", model]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        payload = json.loads(temp_output_path.read_text(encoding="utf-8"))
        validate_raw_output(payload, city)
        temp_output_path.replace(output_path)
    except subprocess.CalledProcessError as exc:
        if temp_output_path.exists():
            temp_output_path.unlink()
        details = []
        if exc.stdout and exc.stdout.strip():
            details.append(exc.stdout.strip())
        if exc.stderr and exc.stderr.strip():
            details.append(exc.stderr.strip())
        if details:
            raise RuntimeError("\n".join(details)) from exc
        raise
    except Exception:
        if temp_output_path.exists():
            temp_output_path.unlink()
        raise


def should_skip_city(city: str, output_path: Path, overwrite: bool) -> bool:
    if overwrite or not output_path.exists():
        return False
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        validate_raw_output(payload, city)
        return True
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the real estate ads skill for all school municipalities with resumable per-city outputs.")
    parser.add_argument("--schools-input", default=str(SCHOOLS_JSON_PATH), help="Path to the source city list JSON.")
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR), help="Directory for raw per-city skill outputs.")
    parser.add_argument("--aggregate-output", default=str(DEFAULT_AGGREGATE_PATH), help="Aggregate JSON output path.")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH), help="Path to the resumable run-state JSON file.")
    parser.add_argument("--schema-path", default=str(DEFAULT_SCHEMA_PATH), help="Path to the Codex exec output schema file.")
    parser.add_argument("--codex-bin", default="codex", help="Codex CLI binary to invoke.")
    parser.add_argument("--model", default=None, help="Optional model override passed to codex exec.")
    parser.add_argument("--limit", type=int, default=None, help="Optional maximum number of cities to process this run.")
    parser.add_argument("--city", help="Run only one municipality from the schools input.")
    parser.add_argument("--overwrite", action="store_true", help="Re-run even when a valid raw output file already exists.")
    parser.add_argument("--retry-failed", action="store_true", help="Retry cities recorded as failed in the state file.")
    parser.add_argument("--aggregate-after-each", action="store_true", help="Refresh the aggregate JSON after every successful city.")
    parser.add_argument(
        "--daily-refresh",
        action="store_true",
        help="Refresh every city, pass previous active ads as prompt cache, and hide ads missing from the latest snapshot.",
    )
    parser.add_argument(
        "--force-daily-refresh",
        action="store_true",
        help="Allow --daily-refresh to run even when a daily refresh already completed today.",
    )
    parser.add_argument(
        "--local-first",
        action="store_true",
        help="Try deterministic local cached-detail fetchers before falling back to Codex.",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Use only deterministic local cached-detail fetchers and fail cities that would need Codex fallback.",
    )
    parser.add_argument(
        "--local-portal",
        action="append",
        choices=SUPPORTED_PORTALS,
        help="Limit deterministic local fetchers to one portal. Repeat to include multiple portals.",
    )
    parser.add_argument(
        "--merge-local-results",
        action="store_true",
        help="Merge local fetcher output into an existing raw city file instead of replacing the full snapshot.",
    )
    args = parser.parse_args()

    repo_root = Path.cwd()
    schools_input = Path(args.schools_input)
    raw_dir = Path(args.raw_dir)
    aggregate_output = Path(args.aggregate_output)
    state_path = Path(args.state_path)
    schema_path = Path(args.schema_path)
    use_local_fetchers = args.local_first or args.local_only
    local_portals = set(args.local_portal) if args.local_portal else None
    if local_portals and not use_local_fetchers:
        parser.error("--local-portal requires --local-first or --local-only")
    if args.merge_local_results and not use_local_fetchers:
        parser.error("--merge-local-results requires --local-first or --local-only")
    needs_previous_aggregate = args.daily_refresh or use_local_fetchers
    previous_aggregate = load_previous_aggregate(aggregate_output) if needs_previous_aggregate and aggregate_output.exists() else None
    overwrite = args.overwrite or args.daily_refresh
    retry_failed = args.retry_failed or args.daily_refresh

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    state = load_state(state_path)
    all_cities = select_cities(load_school_cities(schools_input), args.city)
    failed_cities = state.get("failed_cities", {}) if isinstance(state.get("failed_cities"), dict) else {}
    completed_cities = []
    refreshed_cities = []
    pending_cities = []
    circuit_breaker = ProviderCircuitBreaker()

    for city in all_cities:
        output_path = raw_dir / f"{slugify_city(city)}.json"
        if args.daily_refresh and not args.force_daily_refresh and daily_refresh_city_completed_today(state, city):
            completed_cities.append(city)
            failed_cities.pop(city, None)
            continue
        if should_skip_city(city, output_path, overwrite):
            completed_cities.append(city)
            failed_cities.pop(city, None)
            continue
        if city in failed_cities and not retry_failed:
            continue
        pending_cities.append(city)

    if args.limit is not None:
        pending_cities = pending_cities[: args.limit]

    save_state(
        state_path,
        state,
        schools_input,
        raw_dir,
        aggregate_output,
        completed_cities,
        failed_cities,
        None,
        pending_cities,
        "running",
    )

    try:
        for index, city in enumerate(pending_cities):
            if STOP_REQUESTED:
                break
            remaining_cities = pending_cities[index + 1 :]
            save_state(
                state_path,
                state,
                schools_input,
                raw_dir,
                aggregate_output,
                completed_cities,
                failed_cities,
                city,
                [city] + remaining_cities,
                "running",
            )
            output_path = raw_dir / f"{slugify_city(city)}.json"
            print(f"[{index + 1}/{len(pending_cities)}] {city}", flush=True)
            try:
                used_local_fetchers = False
                if use_local_fetchers:
                    retry_local_portals = None
                    while True:
                        effective_local_portals = (
                            retry_local_portals
                            if retry_local_portals is not None
                            else circuit_breaker.effective_local_portals(local_portals)
                        )
                        try:
                            used_local_fetchers = run_local_fetchers(
                                city,
                                repo_root,
                                output_path,
                                previous_aggregate,
                                local_portals=effective_local_portals,
                                merge_local_results=args.merge_local_results,
                            )
                            if REALITY_IDNES_PORTAL in effective_local_portals:
                                circuit_breaker.record_success(REALITY_IDNES_PORTAL)
                            if used_local_fetchers:
                                print(f"  used local cached-detail fetchers for {city}", flush=True)
                            break
                        except LocalFetcherBlockedError as local_exc:
                            disabled_now = circuit_breaker.record_failure(local_exc)
                            can_retry_without_portal = local_exc.portal == REALITY_IDNES_PORTAL and len(effective_local_portals) > 1
                            if can_retry_without_portal:
                                retry_local_portals = set(effective_local_portals) - {local_exc.portal}
                                if disabled_now:
                                    print(
                                        f"  disabled {local_exc.portal} for rest of run after "
                                        f"{circuit_breaker.threshold} consecutive failures; retrying {city} without it",
                                        flush=True,
                                    )
                                else:
                                    consecutive_failures = circuit_breaker.consecutive_failures.get(local_exc.portal, 0)
                                    print(
                                        f"  {local_exc.portal} failed "
                                        f"({consecutive_failures}/{circuit_breaker.threshold}); "
                                        f"retrying {city} without it",
                                        flush=True,
                                    )
                                continue
                            if args.local_only:
                                raise RuntimeError(f"local fetchers failed for {city}: {local_exc}") from local_exc
                            print(f"  local fetchers failed for {city}: {local_exc}; falling back to Codex", flush=True)
                            break
                        except Exception as local_exc:
                            if args.local_only:
                                raise RuntimeError(f"local fetchers failed for {city}: {local_exc}") from local_exc
                            print(f"  local fetchers failed for {city}: {local_exc}; falling back to Codex", flush=True)
                            break
                if not used_local_fetchers:
                    if args.local_only:
                        raise RuntimeError(f"local-only requested but no cached detail URLs were verified for {city}")
                    run_city(
                        city,
                        repo_root,
                        schema_path,
                        output_path,
                        args.codex_bin,
                        args.model,
                        cached_ads=cached_ads_for_prompt(previous_aggregate, city),
                    )
                completed_cities.append(city)
                refreshed_cities.append(city)
                failed_cities.pop(city, None)
                if args.daily_refresh:
                    record_daily_refresh_city_completion(state_path, state, city=city)
                if args.aggregate_after_each:
                    aggregate_outputs(schools_input, raw_dir, aggregate_output, previous_aggregate=previous_aggregate)
                    print_city_refresh_summary(aggregate_output, previous_aggregate, city)
            except Exception as exc:
                failed_cities[city] = {
                    "failed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "error": str(exc),
                }
                print(f"FAILED {city}: {exc}", file=sys.stderr, flush=True)
            finally:
                save_state(
                    state_path,
                    state,
                    schools_input,
                    raw_dir,
                    aggregate_output,
                    completed_cities,
                    failed_cities,
                    None,
                    remaining_cities,
                    "running" if not STOP_REQUESTED else "interrupted",
                )
        aggregate_outputs(schools_input, raw_dir, aggregate_output, previous_aggregate=previous_aggregate)
        if not args.aggregate_after_each:
            for city in refreshed_cities:
                print_city_refresh_summary(aggregate_output, previous_aggregate, city)
    finally:
        final_status = "completed"
        if STOP_REQUESTED:
            final_status = "interrupted"
        elif failed_cities:
            final_status = "completed-with-failures"
        remaining_cities = [city for city in all_cities if city not in completed_cities and city not in failed_cities]
        save_state(
            state_path,
            state,
            schools_input,
            raw_dir,
            aggregate_output,
            completed_cities,
            failed_cities,
            None,
            remaining_cities,
            final_status,
        )


if __name__ == "__main__":
    main()
