#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
import sys
from html import unescape
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from reality.paths import OVERPASS_MUNICIPALITIES_PATH

BASE_URL = "https://reality.aktualne.cz"
USER_AGENT = "Mozilla/5.0"
CATEGORY_URLS = {
    "house": "https://reality.aktualne.cz/vyhledavani/prodej-domy_vily.html",
    "land": "https://reality.aktualne.cz/vyhledavani/prodej-pozemky.html",
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


def classify_fetch(http_status: int | None, returncode: int, body: str, stderr: str) -> tuple[str, str | None]:
    if returncode != 0:
        return "fetch_error", stderr.strip() or f"curl exited with {returncode}"
    if http_status == 429:
        return "rate_limited", "HTTP 429"
    if http_status is not None and http_status >= 500:
        return "fetch_error", f"HTTP {http_status}"
    if http_status is not None and http_status >= 400:
        return "blocked", f"HTTP {http_status}"
    if not body.strip():
        return "fetch_error", "empty response body"
    return "ok", None


def is_removed_detail_fetch(url: str, stage: str, http_status: int | None) -> bool:
    return http_status == 404 and "/detail/" in url and stage in {"detail_fetch", "discovery_detail_fetch"}


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
        "portal": "reality.aktualne.cz",
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


def run_fetch(url: str, *, attempts: list[dict] | None = None, stage: str = "fetch") -> str:
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
    if status == "blocked" and is_removed_detail_fetch(url, stage, http_status):
        status = "fallback_page"
        error = "HTTP 404"
    if attempts is not None:
        append_fetch_attempt(
            attempts,
            url=url,
            stage=stage,
            attempt=1,
            status=status,
            http_status=http_status,
            error=error,
        )
    if status not in {"ok", "fallback_page"}:
        raise RuntimeError(error or status)
    return body


def canonicalize_detail_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(("https", "reality.aktualne.cz", parts.path.rstrip("/"), "", ""))


def extract_detail_urls(result_html: str, municipality: str | None = None) -> list[str]:
    urls = set()
    for href in re.findall(r'href="(https://reality\.aktualne\.cz/detail/[^"#?]+\.html)"', result_html):
        urls.add(canonicalize_detail_url(href))
    for href in re.findall(r'href="(/detail/[^"#?]+\.html)"', result_html):
        urls.add(canonicalize_detail_url(urljoin(BASE_URL, href)))
    ordered = sorted(urls)
    if not municipality:
        return ordered
    target = f"/detail/{slug_normalize(municipality).replace(' ', '-')}/"
    return [url for url in ordered if target in slug_normalize(url).replace(" ", "-")]


def canonicalize_result_url(url: str) -> str:
    parts = urlsplit(urljoin(BASE_URL, url))
    return urlunsplit(("https", "reality.aktualne.cz", parts.path.rstrip("/"), "", ""))


def municipality_search_url(municipality: str) -> str:
    query = urlencode(
        {
            "form[search_in_city]": municipality,
            "form[cena_mena]": "1",
        }
    )
    return f"{BASE_URL}/vypis-nabidek/?{query}"


def extract_result_urls(html: str, include_houses: bool, include_land: bool) -> list[str]:
    urls = set()
    for href in re.findall(r'href="([^"]*/vyhledavani/[^"#?]+\.html)"', html):
        url = canonicalize_result_url(href)
        if "/vyhledavani/r-" not in url:
            continue
        if include_houses and "prodej-domy_vily.html" in url:
            urls.add(url)
        if include_land and "prodej-pozemky.html" in url:
            urls.add(url)
    return sorted(urls)


def extract_meta_content(html: str, prop: str) -> str | None:
    match = re.search(
        rf'<meta\s+(?:property|name)="{re.escape(prop)}"\s+content="([^"]*)"',
        html,
        re.IGNORECASE,
    )
    return unescape(match.group(1)).strip() if match else None


def extract_heading(html: str, level: str) -> str | None:
    match = re.search(rf"<{level}>(.*?)</{level}>", html, re.S | re.IGNORECASE)
    if not match:
        return None
    text = re.sub(r"<[^>]+>", " ", unescape(match.group(1)))
    return re.sub(r"\s+", " ", text).strip() or None


def extract_table_pairs(html: str) -> dict[str, str]:
    pairs = {}
    for key, value in re.findall(r"<td><b>(.*?)</b></td>\s*<td>(.*?)</td>", html, re.S):
        clean_key = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(key))).strip().rstrip(":")
        clean_value = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value))).strip()
        if clean_key:
            pairs[clean_key] = clean_value
    return pairs


def clean_location(value: str) -> str:
    text = re.sub(r"\s*UPC\s+Klikněte.*$", "", value)
    return re.sub(r"\s+", " ", text).strip()


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


def address_conflicts_with_expected_district(address: str, expected_district: str | None) -> bool:
    if not expected_district:
        return False
    parts = [part.strip() for part in re.split(r"\s*,\s*", address) if part.strip()]
    if len(parts) < 3:
        return False
    district_part = re.sub(r"^(?:okr\.?|okres)\s+", "", parts[-1], flags=re.IGNORECASE).strip()
    normalized_district = slug_normalize(district_part)
    normalized_expected = slug_normalize(expected_district)
    return bool(normalized_district and normalized_district != normalized_expected)


def extract_office_brand(html: str) -> str | None:
    match = re.search(r'<section id="office-info".*?<h3>\s*(.*?)\s*</h3>', html, re.S)
    if not match:
        return None
    text = re.sub(r"<[^>]+>", " ", unescape(match.group(1)))
    brand = re.sub(r"\s+", " ", text).strip()
    return brand or None


def is_detail_page(html: str) -> bool:
    title = extract_meta_content(html, "og:title") or extract_heading(html, "h2") or ""
    return "/detail/" in html and "Reality Aktuálně" in html and (
        "Prodej -" in title or "Pronájem -" in title or '<section id="additional-info">' in html
    )


def is_inactive_or_unusable(html: str) -> tuple[bool, str | None]:
    if not is_detail_page(html):
        return True, "detail-fallback-page"
    title = extract_meta_content(html, "og:title") or extract_heading(html, "h2") or ""
    description = extract_meta_content(html, "og:description") or extract_meta_content(html, "description") or ""
    table = extract_table_pairs(html)
    price = table.get("Cena") or extract_heading(html, "strong")
    normalized = slug_normalize(" ".join([title, description, price or ""]))
    if any(marker in normalized for marker in ["rezervovano", "prodano", "pronajato"]):
        return True, "inactive-or-unpriced"
    return False, None


def extract_area_value(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"(\d[\d\s.]*)\s*m", text)
    if not match:
        return None
    digits = re.sub(r"[^\d]", "", match.group(1))
    return int(digits) if digits else None


def extract_area_after_keywords(text: str, keywords: list[str]) -> int | None:
    normalized = re.sub(r"\s+", " ", text)
    keyword_pattern = "|".join(re.escape(keyword) for keyword in keywords)
    match = re.search(rf"(?:{keyword_pattern})\D{{0,40}}(\d[\d\s.]*)\s*m", normalized, re.IGNORECASE)
    if not match:
        return None
    digits = re.sub(r"[^\d]", "", match.group(1))
    return int(digits) if digits else None


def infer_property_type(title: str, table: dict[str, str]) -> tuple[str | None, str | None]:
    lowered = slug_normalize(" ".join([title, table.get("Druh pozemku", "")]))
    if "chata" in lowered:
        return None, "excluded-chata"
    if "chalup" in lowered:
        return "house", "normalized-from:chalupa"
    if any(marker in lowered for marker in ["pozemek", "pro bydleni"]):
        return "land", None
    if any(marker in lowered for marker in ["dum", "domu", "vila", "rodinny"]) or re.search(r"(^|\W)rd($|\W)", lowered):
        return "house", None
    return None, "unsupported-property-type"


def land_is_buildable(title: str, table: dict[str, str], body_text: str) -> bool:
    lowered = slug_normalize(" ".join([title, table.get("Druh pozemku", ""), body_text]))
    if any(marker in lowered for marker in ["zahrada", "louka", "pole", "zemedel", "nestaveb"]):
        return False
    return any(marker in lowered for marker in ["staveb", "pro bydleni", "bydleni vesnicke", "bv"])


def municipality_matches(
    municipality: str,
    url: str,
    title: str,
    description: str,
    address: str,
    expected_district: str | None = None,
) -> bool:
    target = slug_normalize(municipality)
    haystack = slug_normalize(" ".join([title, description, address]))
    if target not in haystack:
        return False
    if f"{target} u " in haystack and f"{target} u " not in target:
        return False
    if address_conflicts_with_expected_district(address, expected_district):
        return False
    slug_city = slug_normalize(urlsplit(url).path.split("/")[2]) if len(urlsplit(url).path.split("/")) > 2 else ""
    return slug_city == target or target in haystack


def listing_from_detail(
    url: str,
    html: str,
    municipality: str,
    expected_district: str | None = None,
) -> tuple[dict | None, str | None]:
    inactive, reason = is_inactive_or_unusable(html)
    if inactive:
        return None, reason

    title = extract_heading(html, "h3") or extract_meta_content(html, "og:title") or extract_heading(html, "h2") or "unknown"
    description = extract_meta_content(html, "og:description") or extract_meta_content(html, "description") or ""
    table = extract_table_pairs(html)
    address = table.get("Adresa", "")
    if not municipality_matches(municipality, url, title, description, address, expected_district):
        return None, "outside-municipality"

    property_type, property_note = infer_property_type(title, table)
    if property_type is None:
        return None, property_note or "unsupported-property-type"

    price = table.get("Cena")
    if not price:
        price_match = re.search(r'<strong class="price">\s*(.*?)\s*</strong>', html, re.S)
        if price_match:
            price = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(price_match.group(1)))).strip()
    if not price or slug_normalize(price) in {"rezervovano", "prodano", "pronajato"}:
        return None, "missing-price"

    house_area = extract_area_value(table.get("Užitná plocha"))
    parcel_area = extract_area_value(table.get("Plocha parcely") or table.get("Celková plocha"))
    body_heading = extract_heading(html, "h3") or ""
    body_match = re.search(rf"<h3>\s*{re.escape(body_heading)}\s*</h3>\s*(.*?)\s*<section", html, re.S) if body_heading else None
    body_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(body_match.group(1)))) if body_match else description
    if parcel_area is None:
        parcel_area = extract_area_after_keywords(body_text, ["pozemek", "pozemku", "parcela", "parcely"])
    land_area = parcel_area

    if property_type in {"house", "land"} and (land_area is None or land_area < 1000):
        return None, "land-below-threshold"

    if property_type == "land" and not land_is_buildable(title, table, body_text):
        return None, "non-buildable-land"

    location = clean_location(address) or municipality
    office_brand = extract_office_brand(html)
    notes = ["detail-url-verified:reality.aktualne.cz"]
    if property_note:
        notes.append(property_note)
    if property_type == "land":
        notes.append("buildable-land")
    if office_brand:
        notes.append(f"aggregator-source:{slug_normalize(office_brand).replace(' ', '-')}")

    return {
        "portal": ["reality.aktualne.cz"],
        "title": title.replace("| Reality Aktuálně", "").strip(),
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
        # 404/fallback on a cached detail URL usually means the listing was
        # removed. That is listing churn, not a portal outage.
        if attempt_status == "fallback_page" and attempt.get("stage") in {
            "detail_fetch",
            "detail_parse",
            "discovery_detail_fetch",
        }:
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
        output["message"] = "Retained at least one detail-verified Reality Aktuálně row."
    else:
        output["message"] = "No retained in-scope Reality Aktuálně rows."
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
    candidate_urls: set[str] = set()
    fetch_attempts: list[dict] = []
    result_url_set = {canonicalize_result_url(url) for url in result_urls}
    expected_district = expected_district_for_municipality(municipality)

    if discover_results:
        result_url_set.add(municipality_search_url(municipality))
        for detail_url in detail_urls:
            if "/detail/" not in detail_url:
                continue
            try:
                html = run_fetch(detail_url, attempts=fetch_attempts, stage="discovery_detail_fetch")
            except RuntimeError as exc:
                gaps.append(f"failed-discovery-detail-fetch:{detail_url}:{exc}")
                coverage["blocked_portals"].append(f"reality.aktualne.cz discovery detail fetch failed: {detail_url}: {exc}")
                continue
            for result_url in extract_result_urls(html, include_houses, include_land):
                result_url_set.add(result_url)
        if not result_url_set:
            if include_houses:
                result_url_set.add(CATEGORY_URLS["house"])
            if include_land:
                result_url_set.add(CATEGORY_URLS["land"])

    for url in sorted(result_url_set):
        try:
            html = run_fetch(url, attempts=fetch_attempts, stage="search_fetch")
        except RuntimeError as exc:
            gaps.append(f"failed-result-fetch:{url}:{exc}")
            coverage["blocked_portals"].append(f"reality.aktualne.cz result fetch failed: {url}: {exc}")
            continue
        found = extract_detail_urls(html, municipality)
        if not found:
            gaps.append(f"no-detail-urls-found:{url}")
        candidate_urls.update(found)

    for url in detail_urls:
        if "/detail/" not in url:
            gaps.append(f"invalid-detail-url:{url}")
            continue
        candidate_urls.add(canonicalize_detail_url(url))

    coverage["candidates_gathered"] = len(candidate_urls)

    listings = []
    for detail_url in sorted(candidate_urls):
        try:
            html = run_fetch(detail_url, attempts=fetch_attempts, stage="detail_fetch")
        except RuntimeError as exc:
            gaps.append(f"failed-detail-fetch:{detail_url}:{exc}")
            coverage["blocked_portals"].append(f"reality.aktualne.cz detail fetch failed: {detail_url}: {exc}")
            continue
        listing, reason = listing_from_detail(detail_url, html, municipality, expected_district)
        if listing is not None:
            if listing["property_type"] == "house" and not include_houses:
                continue
            if listing["property_type"] == "land" and not include_land:
                continue
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

    listings.sort(key=lambda item: re.sub(r"[^\d]", "", item["price"]), reverse=True)
    coverage["rows_retained"] = len(listings)
    coverage["workers_with_results"] = 1 if listings else 0
    if not listings:
        coverage["zero_result_portals"].append("reality.aktualne.cz")

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
            "reality.aktualne.cz": build_portal_status(fetch_attempts, listings),
        },
        "fetch_attempts": fetch_attempts,
        "gaps": gaps,
        "listings": listings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch and normalize reality.aktualne.cz listings for one municipality.")
    parser.add_argument("--municipality", required=True)
    parser.add_argument("--location-scope", default="municipality_only")
    parser.add_argument("--include-houses", action="store_true", default=True)
    parser.add_argument("--include-land", action="store_true", default=True)
    parser.add_argument("--result-url", action="append", default=[])
    parser.add_argument("--detail-url", action="append", default=[])
    parser.add_argument(
        "--discover-results",
        action="store_true",
        help="Discover current result pages from cached detail pages and broad sale categories.",
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
