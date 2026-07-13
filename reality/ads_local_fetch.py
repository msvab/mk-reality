import json
import subprocess
import sys
from pathlib import Path

from .ads_codex_runner import validate_raw_output
from .ads_state import atomic_write_json

LOCAL_FETCHERS = {
    "reality.idnes.cz": "reality.portal_fetchers.reality_idnes_fetch",
    "realitymix.cz": "reality.portal_fetchers.realitymix_fetch",
    "reality.aktualne.cz": "reality.portal_fetchers.reality_aktualne_fetch",
    "sreality.cz": "reality.portal_fetchers.sreality_fetch",
}
SUPPORTED_PORTALS = tuple(LOCAL_FETCHERS)
REALITY_IDNES_PORTAL = "reality.idnes.cz"
REALITY_IDNES_CIRCUIT_BREAKER_THRESHOLD = 3


def load_json_object(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


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
        existing_payload = load_json_object(output_path)
        combined = merge_local_payload_into_existing_raw(existing_payload, combined)
    validate_raw_output(combined, city)
    atomic_write_json(output_path, combined)
    return True
