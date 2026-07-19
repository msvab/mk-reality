#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
import sys
import time
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlencode, urlsplit, urlunsplit

from ..paths import REALITY_IDNES_LOCALITY_ID_CACHE_PATH

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF_SECONDS = 2.0
CURL_CONNECT_TIMEOUT_SECONDS = 15
CURL_MAX_TIME_SECONDS = 45
AUTOCOMPLETE_LOCALITY_URL = (
    "https://reality.idnes.cz/admin.api/autocomplete-locality"
    "?fe=1&st={query}"
    "&types%5B0%5D=OBEC&types%5B1%5D=CAST_OBCE"
)
LOCALITY_ID_CACHE_PATH = REALITY_IDNES_LOCALITY_ID_CACHE_PATH


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


def path_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", slug_normalize(value)).strip("-")


def classify_fetch(http_status: int | None, returncode: int, body: str, stderr: str) -> tuple[str, str | None]:
    if returncode != 0:
        return "fetch_error", stderr.strip() or f"curl exited with {returncode}"
    if http_status == 429:
        return "rate_limited", "HTTP 429"
    if http_status is not None and http_status >= 500:
        return "fetch_error", f"HTTP {http_status}"
    if http_status is not None and http_status >= 400:
        return "fallback_page", f"HTTP {http_status}"
    if not body.strip():
        return "fetch_error", "empty response body"
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
        "portal": "reality.idnes.cz",
        "url": canonicalize_detail_url(url) if "/detail/" in url else url,
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


def run_fetch(
    url: str,
    *,
    attempts: list[dict] | None = None,
    stage: str = "fetch",
    retries: int = DEFAULT_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
) -> str:
    last_error = None
    for attempt in range(1, retries + 2):
        completed = subprocess.run(
            [
                "curl",
                "-sL",
                "--connect-timeout",
                str(CURL_CONNECT_TIMEOUT_SECONDS),
                "--max-time",
                str(CURL_MAX_TIME_SECONDS),
                "-A",
                USER_AGENT,
                "-w",
                "\n__HTTP_STATUS__:%{http_code}",
                url,
            ],
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
        status, error = classify_fetch(http_status, completed.returncode, body, completed.stderr)
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
        if status in {"ok", "fallback_page"}:
            return body
        last_error = error or status
        if attempt <= retries:
            time.sleep(backoff_seconds * attempt)
    raise RuntimeError(last_error or "fetch_error")


def canonicalize_detail_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(("https", "reality.idnes.cz", parts.path.rstrip("/") + "/", "", ""))


def canonicalize_result_url(url: str) -> str:
    parts = urlsplit(unescape(url))
    return urlunsplit(("https", "reality.idnes.cz", parts.path.rstrip("/") + "/", urlencode(parse_qs(parts.query), doseq=True), ""))


def result_urls_from_locality_ids(locality_ids: list[str]) -> list[str]:
    urls = []
    for locality_id in locality_ids:
        if not re.fullmatch(r"CAST_OBCE-\d+", locality_id):
            continue
        for category in ("domy", "pozemky"):
            url = f"https://reality.idnes.cz/s/prodej/{category}/?s-l={locality_id}"
            if url not in urls:
                urls.append(url)
    return urls


def result_urls_from_municipality_slug(municipality: str) -> list[str]:
    slug = path_slug(municipality)
    if not slug:
        return []
    return [
        f"https://reality.idnes.cz/s/prodej/domy/{slug}/",
        f"https://reality.idnes.cz/s/prodej/pozemky/{slug}/",
    ]


def extract_locality_ids(html: str) -> list[str]:
    ids = []
    for match in re.finditer(r"(?:s-l=|\"|'|\b)(CAST_OBCE-\d+)", unescape(html)):
        locality_id = match.group(1)
        if locality_id not in ids:
            ids.append(locality_id)
    return ids


def load_locality_id_cache(path: Path = LOCALITY_ID_CACHE_PATH) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    cache = {}
    for municipality, locality_ids in payload.items():
        if not isinstance(municipality, str) or not isinstance(locality_ids, list):
            continue
        valid_ids = [
            locality_id
            for locality_id in locality_ids
            if isinstance(locality_id, str) and re.fullmatch(r"CAST_OBCE-\d+", locality_id)
        ]
        if valid_ids:
            cache[municipality] = list(dict.fromkeys(valid_ids))
    return cache


def save_locality_id_cache(cache: dict[str, list[str]], path: Path = LOCALITY_ID_CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(cache.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cached_locality_ids(municipality: str, cache: dict[str, list[str]]) -> list[str]:
    normalized_municipality = slug_normalize(municipality)
    for cached_municipality, locality_ids in cache.items():
        if slug_normalize(cached_municipality) == normalized_municipality:
            return list(locality_ids)
    return []


def remember_cached_locality_ids(municipality: str, locality_ids: list[str], cache: dict[str, list[str]]) -> None:
    valid_ids = [locality_id for locality_id in locality_ids if re.fullmatch(r"CAST_OBCE-\d+", locality_id)]
    if valid_ids:
        cache[municipality] = list(dict.fromkeys([*cached_locality_ids(municipality, cache), *valid_ids]))


def autocomplete_locality_ids(municipality: str, *, attempts: list[dict] | None = None) -> list[str]:
    url = AUTOCOMPLETE_LOCALITY_URL.format(query=quote_plus(municipality))
    html = run_fetch(url, attempts=attempts, stage="locality_autocomplete_fetch")
    if slug_normalize(municipality) not in slug_normalize(html):
        return []
    return extract_locality_ids(html)


def extract_detail_urls(html: str) -> list[str]:
    urls = []
    for match in re.finditer(r'href=["\']([^"\']*/detail/prodej/[^"\']+)["\']', html, re.IGNORECASE):
        url = canonicalize_detail_url(unescape(match.group(1)))
        if url not in urls:
            urls.append(url)
    return urls


def extract_meta_content(html: str, prop: str) -> str | None:
    match = re.search(
        rf'<meta\s+(?:property|name)="{re.escape(prop)}"\s+content="([^"]*)"',
        html,
        re.IGNORECASE,
    )
    return unescape(match.group(1)).strip() if match else None


def extract_js_string(html: str, key: str) -> str | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]*)"', html)
    return unescape(match.group(1)).strip() if match else None


def extract_js_number(html: str, key: str) -> int | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*(\d+|null)', html)
    if not match or match.group(1) == "null":
        return None
    return int(match.group(1))


def extract_area_value(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"(\d[\d\s.]*)\s*m", text)
    if not match:
        return None
    digits = re.sub(r"[^\d]", "", match.group(1))
    return int(digits) if digits else None


def extract_visible_price(html: str) -> str | None:
    match = re.search(r'<p class="b-detail__price">.*?<strong>(.*?)</strong>', html, re.S)
    if not match:
        return None
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(match.group(1)))).strip()
    return text.replace("\u200d", "") or None


def format_price(price_czk: int | None) -> str | None:
    if price_czk is None:
        return None
    return f"{price_czk:,}".replace(",", " ") + " Kč"


def price_sort_value(listing: dict) -> int:
    digits = re.sub(r"[^\d]", "", str(listing.get("price", "")))
    return int(digits) if digits else 0


def infer_property_type(url: str, title: str, category: str) -> tuple[str | None, str | None]:
    lowered = slug_normalize(" ".join([url, title, category]))
    if "chata" in lowered:
        return None, "excluded-chata"
    if "chalup" in lowered:
        return "house", "normalized-from:chalupa"
    if "pozemek" in lowered:
        return "land", None
    if any(marker in lowered for marker in ["dum", "domu", "dům", "vila", "rodinny", "rodinny dum"]):
        return "house", None
    return None, "unsupported-property-type"


def land_is_buildable(title: str, description: str, category: str) -> bool:
    lowered = slug_normalize(" ".join([title, description, category]))
    if any(marker in lowered for marker in ["zahrada", "louka", "pole", "zemedel", "nestaveb"]):
        return False
    return any(marker in lowered for marker in ["staveb", "pro bydleni", "bydleni"])


def listing_from_detail(url: str, html: str, municipality: str) -> tuple[dict | None, str | None]:
    title = extract_meta_content(html, "og:title") or extract_meta_content(html, "twitter:title") or ""
    description = extract_meta_content(html, "og:description") or extract_meta_content(html, "description") or ""
    city = extract_meta_content(html, "cXenseParse:qiw-reaCity") or extract_js_string(html, "listing_localityCity") or ""
    district = extract_meta_content(html, "cXenseParse:qiw-reaDistrict") or extract_js_string(html, "listing_localityDistrict") or ""
    category = extract_meta_content(html, "cXenseParse:qiw-reaCategory") or extract_js_string(html, "listing_category") or ""
    if not title or not city:
        return None, "detail-fallback-page"
    if slug_normalize(city) != slug_normalize(municipality):
        return None, "outside-municipality"

    property_type, property_note = infer_property_type(url, title, category)
    if property_type is None:
        return None, property_note or "unsupported-property-type"

    price_czk = extract_js_number(html, "listing_price")
    price = extract_visible_price(html) or format_price(price_czk)
    if not price:
        return None, "missing-price"

    house_area = extract_js_number(html, "listing_area")
    if house_area is None:
        house_area = extract_area_value(title)
    land_area = extract_js_number(html, "listing_landArea")
    if land_area is None:
        land_area = extract_area_value(title if property_type == "land" else description)
    if property_type in {"house", "land"} and (land_area is None or land_area < 1000):
        return None, "land-below-threshold"
    if property_type == "land" and not land_is_buildable(title, description, category):
        return None, "non-buildable-land"

    notes = ["detail-url-verified:reality.idnes.cz"]
    if property_note:
        notes.append(property_note)
    if property_type == "land":
        notes.append("buildable-land")

    location = f"{city}, okres {district}" if district else city
    return {
        "portal": ["reality.idnes.cz"],
        "title": title,
        "location": location,
        "property_type": property_type,
        "price": price,
        "house_area_m2": str(house_area) if house_area is not None and property_type == "house" else "unknown",
        "land_area_m2": str(land_area) if land_area is not None else "unknown",
        "urls": [canonicalize_detail_url(url)],
        "notes": notes,
    }, None


def build_portal_status(fetch_attempts: list[dict], listings: list[dict]) -> dict:
    status_order = {
        "ok": 0,
        "fallback_page": 3,
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
        # Cached iDNES detail URLs can later resolve to generic/non-detail
        # pages. Treat that as stale listing evidence, not portal failure.
        if attempt_status == "fallback_page" and attempt.get("stage") in {"detail_fetch", "detail_parse"}:
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
        output["message"] = "Retained at least one detail-verified iDNES row."
    else:
        output["message"] = "No retained in-scope iDNES rows."
    return output


def build_output(
    municipality: str,
    location_scope: str,
    detail_urls: list[str],
    result_urls: list[str] | None = None,
    discover_results: bool = False,
    locality_id_cache: dict[str, list[str]] | None = None,
) -> dict:
    coverage = {
        "workers_launched": 1,
        "workers_with_results": 0,
        "candidates_gathered": 0,
        "rows_retained": 0,
        "zero_result_portals": [],
        "blocked_portals": [],
    }
    fetch_attempts: list[dict] = []
    gaps: list[str] = []
    listings: list[dict] = []
    locality_id_cache = locality_id_cache if locality_id_cache is not None else {}
    locality_ids: list[str] = cached_locality_ids(municipality, locality_id_cache)
    if locality_ids:
        gaps.append("idnes-discovery-used-locality-id-cache")
    fetched_detail_html: dict[str, str] = {}
    candidate_urls = [] if locality_ids else [canonicalize_detail_url(url) for url in detail_urls if "/detail/" in url]

    def remember_detail_url(url: str) -> None:
        canonical_url = canonicalize_detail_url(url)
        if canonical_url not in candidate_urls:
            candidate_urls.append(canonical_url)

    def remember_locality_ids(html: str) -> None:
        for locality_id in extract_locality_ids(html):
            if locality_id not in locality_ids:
                locality_ids.append(locality_id)
        remember_cached_locality_ids(municipality, locality_ids, locality_id_cache)

    def verify_detail_url(detail_url: str) -> None:
        try:
            html = fetched_detail_html.get(detail_url)
            if html is None:
                html = run_fetch(detail_url, attempts=fetch_attempts, stage="detail_fetch")
                fetched_detail_html[detail_url] = html
        except RuntimeError as exc:
            gaps.append(f"failed-detail-fetch:{detail_url}:{exc}")
            return
        detail_city = extract_meta_content(html, "cXenseParse:qiw-reaCity") or extract_js_string(html, "listing_localityCity") or ""
        if slug_normalize(detail_city) == slug_normalize(municipality):
            remember_locality_ids(html)
        listing, reason = listing_from_detail(detail_url, html, municipality)
        if listing is not None:
            listings.append(listing)
        elif reason:
            if reason == "detail-fallback-page":
                append_fetch_attempt(
                    fetch_attempts,
                    url=detail_url,
                    stage="detail_parse",
                    attempt=1,
                    status="fallback_page",
                    message="Detail URL returned fallback or non-detail content.",
                )
            gaps.append(f"{reason}:{detail_url}")

    verified_detail_urls = set()
    for detail_url in list(candidate_urls):
        verify_detail_url(detail_url)
        verified_detail_urls.add(detail_url)

    normalized_result_urls = []
    for result_url in result_urls or []:
        if "/s/prodej/" in result_url and result_url not in normalized_result_urls:
            normalized_result_urls.append(canonicalize_result_url(result_url))
    if discover_results:
        if not locality_ids and not normalized_result_urls:
            try:
                for locality_id in autocomplete_locality_ids(municipality, attempts=fetch_attempts):
                    if locality_id not in locality_ids:
                        locality_ids.append(locality_id)
                remember_cached_locality_ids(municipality, locality_ids, locality_id_cache)
            except RuntimeError as exc:
                gaps.append(f"idnes-locality-autocomplete-error:{exc}")
        for result_url in result_urls_from_locality_ids(locality_ids):
            if result_url not in normalized_result_urls:
                normalized_result_urls.append(result_url)
        if not normalized_result_urls:
            for result_url in result_urls_from_municipality_slug(municipality):
                if result_url not in normalized_result_urls:
                    normalized_result_urls.append(result_url)
            if normalized_result_urls:
                gaps.append("idnes-discovery-used-municipality-slug-fallback")
            else:
                gaps.append("idnes-discovery-missing-locality-id")

    for result_url in normalized_result_urls:
        try:
            html = run_fetch(result_url, attempts=fetch_attempts, stage="search_fetch")
        except RuntimeError as exc:
            gaps.append(f"result-fetch-error:{result_url}:{exc}")
            coverage["blocked_portals"].append(f"reality.idnes.cz result fetch failed: {result_url}: {exc}")
            continue
        discovered_urls = extract_detail_urls(html)
        if not discovered_urls:
            gaps.append(f"result-no-detail-links:{result_url}")
        for detail_url in discovered_urls:
            remember_detail_url(detail_url)

    for detail_url in list(candidate_urls):
        if detail_url in verified_detail_urls:
            continue
        verify_detail_url(detail_url)
        verified_detail_urls.add(detail_url)

    coverage["candidates_gathered"] = len(candidate_urls)
    listings.sort(key=price_sort_value, reverse=True)
    coverage["rows_retained"] = len(listings)
    coverage["workers_with_results"] = 1 if listings else 0
    if not listings:
        coverage["zero_result_portals"].append("reality.idnes.cz")

    return {
        "city": municipality,
        "query": {
            "municipality": municipality,
            "location_scope": location_scope,
            "country": "Czech Republic",
            "property_types": ["house", "chalupa", "land"],
            "land_size_min_m2": 1000,
        },
        "assumptions": [],
        "coverage": coverage,
        "portal_status": {
            "reality.idnes.cz": build_portal_status(fetch_attempts, listings),
        },
        "fetch_attempts": fetch_attempts,
        "gaps": gaps,
        "listings": listings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch and normalize reality.idnes.cz listings for one municipality.")
    parser.add_argument("--municipality", required=True)
    parser.add_argument("--location-scope", default="municipality_only")
    parser.add_argument("--detail-url", action="append", default=[])
    parser.add_argument("--result-url", action="append", default=[])
    parser.add_argument(
        "--discover-results",
        action="store_true",
        help="Use cached iDNES detail pages to discover municipality result pages and verify their detail URLs.",
    )
    parser.add_argument("--output")
    args = parser.parse_args()

    locality_id_cache = load_locality_id_cache()
    payload = build_output(
        municipality=args.municipality,
        location_scope=args.location_scope,
        detail_urls=args.detail_url,
        result_urls=args.result_url,
        discover_results=args.discover_results,
        locality_id_cache=locality_id_cache,
    )
    save_locality_id_cache(locality_id_cache)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
