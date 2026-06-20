#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
import sys
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit


BASE_URL = "https://www.mmreality.cz"
USER_AGENT = "Mozilla/5.0"
INACTIVE_MARKER = "je nam lito, ale tato nemovitost jiz neni v nabidce m&m reality"
DISCOVERY_RESULT_URLS = {
    "house": "https://www.mmreality.cz/nemovitosti/prodej/rodinne-domy/",
    "land": "https://www.mmreality.cz/nemovitosti/prodej/pozemky/",
}


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


def run_fetch(url: str) -> str:
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
    status, error = classify_fetch(http_status, completed.returncode, body, completed.stderr)
    if status != "ok":
        raise FetchError(status, error or status, http_status)
    return body


class FetchError(RuntimeError):
    def __init__(self, status: str, message: str, http_status: int | None = None):
        super().__init__(message)
        self.status = status
        self.http_status = http_status


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
    status: str,
    http_status: int | None = None,
    error: str | None = None,
) -> None:
    row = {
        "portal": "mmreality.cz",
        "url": url,
        "stage": stage,
        "attempt": 1,
        "status": status,
    }
    if http_status is not None:
        row["http_status"] = http_status
    if error:
        row["error"] = error
    attempts.append(row)


def fetch_with_attempt(url: str, attempts: list[dict], stage: str) -> str:
    try:
        html = run_fetch(url)
    except FetchError as exc:
        append_fetch_attempt(
            attempts,
            url=url,
            stage=stage,
            status=exc.status,
            http_status=exc.http_status,
            error=str(exc),
        )
        raise
    append_fetch_attempt(attempts, url=url, stage=stage, status="ok")
    return html


def inactive_marker_present(html: str) -> bool:
    normalized = slug_normalize(re.sub(r"<[^>]+>", " ", unescape(html)))
    return INACTIVE_MARKER in normalized


def listing_id_from_url(url: str) -> str | None:
    match = re.search(r"/nemovitosti/(\d+)/?", urlsplit(url).path)
    return match.group(1) if match else None


def generic_detail_url(listing_id: str) -> str:
    return f"{BASE_URL}/nemovitosti/{listing_id}/"


def extract_result_offers(html: str) -> list[dict]:
    match = re.search(r':ssr="([^"]+)"', html)
    if not match:
        return []
    try:
        payload = json.loads(unescape(match.group(1)))
    except json.JSONDecodeError:
        return []
    offers = payload.get("offers", [])
    return [offer for offer in offers if isinstance(offer, dict)]


def extract_detail_payload(html: str) -> dict | None:
    match = re.search(r'vue-property-detail-favorite-button\s+:property="([^"]+)"', html)
    if not match:
        return None
    try:
        payload = json.loads(unescape(match.group(1)))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def extract_meta_content(html: str, prop: str) -> str | None:
    match = re.search(
        rf'<meta\s+(?:property|name)="{re.escape(prop)}"\s+content="([^"]*)"',
        html,
        re.IGNORECASE,
    )
    return unescape(match.group(1)).strip() if match else None


def extract_params(html: str) -> dict[str, str]:
    params = {}
    pattern = re.compile(
        r'<div class="rds-property-params-label">(.*?)</div>\s*<div class="rds-property-params-value">(.*?)</div>',
        re.S,
    )
    for key, value in pattern.findall(html):
        clean_key = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(key))).strip().rstrip(":")
        clean_value = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value))).strip()
        if clean_key:
            params[clean_key] = clean_value
    return params


def normalize_price(price: int | str | None) -> str | None:
    if price is None:
        return None
    if isinstance(price, str):
        text = re.sub(r"\s+", " ", price).strip()
        if not text or slug_normalize(text) == "info v rk":
            return None
        return text
    return f"{price:,}".replace(",", " ") + " Kč"


def extract_area_value(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"(\d[\d\s.]*)\s*m", text)
    if not match:
        return None
    digits = re.sub(r"[^\d]", "", match.group(1))
    return int(digits) if digits else None


def parse_result_offer(offer: dict, municipality: str) -> tuple[str | None, str | None]:
    municipality_name = str(offer.get("municipality", "")).strip()
    if slug_normalize(municipality_name) != slug_normalize(municipality):
        return None, f"outside-municipality-result:{offer.get('id')}"
    listing_id = offer.get("id")
    if not listing_id:
        return None, "missing-id-in-results"
    title = slug_normalize(str(offer.get("title", "")))
    if "chata" in title:
        return None, f"excluded-chata:{listing_id}"
    return str(listing_id), None


def infer_property_type(payload: dict) -> tuple[str | None, str | None]:
    title = slug_normalize(str(payload.get("title", "")))
    group = slug_normalize(str(payload.get("group", {}).get("name", "")))
    kind = slug_normalize(str(payload.get("type", {}).get("name", "")))
    if "chata" in title or "chata" in kind:
        return None, "excluded-chata"
    if "chalupa" in title or "chalupa" in kind:
        return "house", "normalized-from:chalupa"
    if group == "dum" or "dum" in kind:
        return "house", None
    if group == "pozemek" or "pozemek" in kind:
        return "land", None
    return None, "unsupported-property-type"


def land_is_buildable(payload: dict) -> bool:
    title = str(payload.get("title", ""))
    kind = str(payload.get("type", {}).get("name", ""))
    description = str(payload.get("description", ""))
    lowered = slug_normalize(" ".join([title, kind, description]))
    if any(marker in lowered for marker in ["zahrada", "louka", "pole", "zemedel"]):
        return False
    return any(marker in lowered for marker in ["pozemek k bydleni", "staveb", "urcen pro bydleni", "bv"])


def location_from_payload(payload: dict) -> str:
    municipality = str(payload.get("municipality", "")).strip()
    municipality_part = str(payload.get("municipalityPart", "")).strip()
    district = str(payload.get("district", "")).strip()
    if municipality_part and slug_normalize(municipality_part) != slug_normalize(municipality):
        return f"{municipality} - {municipality_part}, okres {district}"
    return f"{municipality}, okres {district}"


def listing_from_detail(url: str, html: str, municipality: str) -> tuple[dict | None, str | None]:
    if inactive_marker_present(html):
        return None, "inactive-generic-page"

    payload = extract_detail_payload(html)
    if payload is None:
        return None, "missing-detail-payload"

    municipality_name = str(payload.get("municipality", "")).strip()
    if slug_normalize(municipality_name) != slug_normalize(municipality):
        return None, "outside-municipality"

    property_type, property_note = infer_property_type(payload)
    if property_type is None:
        return None, property_note or "unsupported-property-type"

    price = normalize_price(payload.get("price"))
    if price is None:
        return None, "missing-price"

    params = extract_params(html)
    house_area = payload.get("usableArea") or extract_area_value(params.get("Užitná plocha"))
    parcel_area = payload.get("parcelArea") or extract_area_value(params.get("Plocha parcely"))
    land_area = parcel_area if property_type == "house" else payload.get("totalArea") or parcel_area

    if property_type in {"house", "land"} and (land_area is None or int(land_area) < 1000):
        return None, "land-below-threshold"
    if property_type == "land" and not land_is_buildable(payload):
        return None, "non-buildable-land"

    title = extract_meta_content(html, "og:title") or str(payload.get("originalTitle") or payload.get("title") or "unknown")
    location = location_from_payload(payload)

    notes = ["detail-url-verified:mmreality.cz"]
    municipality_part = str(payload.get("municipalityPart", "")).strip()
    if municipality_part and slug_normalize(municipality_part) != slug_normalize(municipality):
        notes.append(f"municipality-subarea:{municipality_part}")
    if property_note:
        notes.append(property_note)
    if property_type == "land":
        notes.append("buildable-land")

    return {
        "portal": ["mmreality.cz"],
        "title": title.replace(" | M&M reality", "").strip(),
        "location": location,
        "property_type": property_type,
        "price": price,
        "house_area_m2": str(house_area) if house_area is not None and property_type == "house" else "unknown",
        "land_area_m2": str(land_area) if land_area is not None else "unknown",
        "urls": [url],
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
    failed_attempt = None
    for attempt in fetch_attempts:
        attempt_status = str(attempt.get("status", "unknown"))
        if attempt_status in {"ok", "no_results"}:
            continue
        if failed_attempt is None or status_order.get(attempt_status, -1) > status_order.get(
            str(failed_attempt.get("status", "unknown")),
            -1,
        ):
            failed_attempt = attempt
    if failed_attempt:
        output = {
            "status": failed_attempt.get("status", "fetch_error"),
            "stage": failed_attempt.get("stage"),
            "message": failed_attempt.get("error", "M&M Reality fetch failed."),
            "evidence": [f"{failed_attempt.get('stage')}:{failed_attempt.get('status')}:{failed_attempt.get('url')}"],
        }
        if failed_attempt.get("http_status") is not None:
            output["http_status"] = failed_attempt["http_status"]
    elif listings:
        output = {
            "status": "ok",
            "message": "Retained at least one detail-verified M&M Reality row.",
        }
    else:
        output = {
            "status": "no_results",
            "message": "No retained in-scope M&M Reality rows.",
        }
    return output


def build_output(
    municipality: str,
    location_scope: str,
    include_houses: bool,
    include_land: bool,
    result_urls: list[str],
    detail_urls: list[str],
    discover_results: bool = False,
) -> dict:
    assumptions = []
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
    listing_ids: set[str] = set()
    effective_result_urls = list(result_urls)
    if discover_results:
        for property_type, url in DISCOVERY_RESULT_URLS.items():
            if property_type == "house" and not include_houses:
                continue
            if property_type == "land" and not include_land:
                continue
            if url not in effective_result_urls:
                effective_result_urls.append(url)

    for url in effective_result_urls:
        try:
            html = fetch_with_attempt(url, fetch_attempts, "search_fetch")
        except FetchError:
            gaps.append(f"failed-result-fetch:{url}")
            continue
        offers = extract_result_offers(html)
        if not offers:
            gaps.append(f"no-offers-found:{url}")
        for offer in offers:
            listing_id, reason = parse_result_offer(offer, municipality)
            if listing_id:
                listing_ids.add(listing_id)
            elif reason and not (discover_results and reason.startswith("outside-municipality-result:")):
                gaps.append(reason)

    for url in detail_urls:
        listing_id = listing_id_from_url(url)
        if listing_id:
            listing_ids.add(listing_id)
        else:
            gaps.append(f"invalid-detail-url:{url}")

    coverage["candidates_gathered"] = len(listing_ids)

    listings = []
    for listing_id in sorted(listing_ids):
        detail_url = generic_detail_url(listing_id)
        try:
            html = fetch_with_attempt(detail_url, fetch_attempts, "detail_fetch")
        except FetchError:
            gaps.append(f"failed-detail-fetch:{detail_url}")
            continue
        listing, reason = listing_from_detail(detail_url, html, municipality)
        if listing is not None:
            if listing["property_type"] == "house" and not include_houses:
                continue
            if listing["property_type"] == "land" and not include_land:
                continue
            listings.append(listing)
        elif reason:
            gaps.append(f"{reason}:{detail_url}")

    listings.sort(key=lambda item: re.sub(r"[^\d]", "", item["price"]), reverse=True)
    coverage["rows_retained"] = len(listings)
    coverage["workers_with_results"] = 1 if listings else 0
    if not listings:
        coverage["zero_result_portals"].append("mmreality.cz")

    return {
        "city": municipality,
        "query": {
            "municipality": municipality,
            "location_scope": location_scope,
            "country": "Czech Republic",
            "property_types": [item for item, enabled in (("house", include_houses), ("chalupa", include_houses), ("land", include_land)) if enabled],
            "land_size_min_m2": 1000,
        },
        "assumptions": assumptions,
        "coverage": coverage,
        "portal_status": {
            "mmreality.cz": build_portal_status(fetch_attempts, listings),
        },
        "fetch_attempts": fetch_attempts,
        "gaps": gaps,
        "listings": listings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch and normalize mmreality.cz listings for one municipality.")
    parser.add_argument("--municipality", required=True)
    parser.add_argument("--location-scope", default="municipality_only")
    parser.add_argument("--include-houses", action="store_true", default=True)
    parser.add_argument("--include-land", action="store_true", default=True)
    parser.add_argument("--result-url", action="append", default=[])
    parser.add_argument("--detail-url", action="append", default=[])
    parser.add_argument(
        "--discover-results",
        action="store_true",
        help="Fetch default M&M Reality sale category pages and filter result offers by municipality.",
    )
    parser.add_argument("--output")
    args = parser.parse_args()

    payload = build_output(
        municipality=args.municipality,
        location_scope=args.location_scope,
        include_houses=args.include_houses,
        include_land=args.include_land,
        result_urls=args.result_url,
        detail_urls=args.detail_url,
        discover_results=args.discover_results,
    )

    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
