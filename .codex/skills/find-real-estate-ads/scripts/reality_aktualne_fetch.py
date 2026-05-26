#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
import sys
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit


BASE_URL = "https://reality.aktualne.cz"
USER_AGENT = "Mozilla/5.0"


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
        ["curl", "-sL", "-A", USER_AGENT, url],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def canonicalize_detail_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(("https", "reality.aktualne.cz", parts.path.rstrip("/"), "", ""))


def extract_detail_urls(result_html: str) -> list[str]:
    urls = set()
    for href in re.findall(r'href="(https://reality\.aktualne\.cz/detail/[^"#?]+\.html)"', result_html):
        urls.add(canonicalize_detail_url(href))
    for href in re.findall(r'href="(/detail/[^"#?]+\.html)"', result_html):
        urls.add(canonicalize_detail_url(urljoin(BASE_URL, href)))
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


def infer_property_type(title: str, table: dict[str, str]) -> tuple[str | None, str | None]:
    lowered = slug_normalize(" ".join([title, table.get("Druh pozemku", "")]))
    if "chata" in lowered:
        return None, "excluded-chata"
    if "chalup" in lowered:
        return "house", "normalized-from:chalupa"
    if any(marker in lowered for marker in ["pozemek", "pro bydleni"]):
        return "land", None
    if any(marker in lowered for marker in ["dum", "domu", "vila", "rodinny"]):
        return "house", None
    return None, "unsupported-property-type"


def land_is_buildable(title: str, table: dict[str, str], body_text: str) -> bool:
    lowered = slug_normalize(" ".join([title, table.get("Druh pozemku", ""), body_text]))
    if any(marker in lowered for marker in ["zahrada", "louka", "pole", "zemedel", "nestaveb"]):
        return False
    return any(marker in lowered for marker in ["staveb", "pro bydleni", "bydleni vesnicke", "bv"])


def municipality_matches(municipality: str, url: str, title: str, description: str, address: str) -> bool:
    target = slug_normalize(municipality)
    haystack = slug_normalize(" ".join([title, description, address]))
    if target not in haystack:
        return False
    if f"{target} u " in haystack and f"{target} u " not in target:
        return False
    slug_city = slug_normalize(urlsplit(url).path.split("/")[2]) if len(urlsplit(url).path.split("/")) > 2 else ""
    return slug_city == target or target in haystack


def listing_from_detail(url: str, html: str, municipality: str) -> tuple[dict | None, str | None]:
    inactive, reason = is_inactive_or_unusable(html)
    if inactive:
        return None, reason

    title = extract_heading(html, "h3") or extract_meta_content(html, "og:title") or extract_heading(html, "h2") or "unknown"
    description = extract_meta_content(html, "og:description") or extract_meta_content(html, "description") or ""
    table = extract_table_pairs(html)
    address = table.get("Adresa", "")
    if not municipality_matches(municipality, url, title, description, address):
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
    land_area = parcel_area

    if property_type in {"house", "land"} and (land_area is None or land_area < 1000):
        return None, "land-below-threshold"

    body_heading = extract_heading(html, "h3") or ""
    body_match = re.search(rf"<h3>\s*{re.escape(body_heading)}\s*</h3>\s*(.*?)\s*<section", html, re.S) if body_heading else None
    body_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(body_match.group(1)))) if body_match else description
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


def build_output(
    municipality: str,
    location_scope: str,
    include_houses: bool,
    include_land: bool,
    result_urls: list[str],
    detail_urls: list[str],
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

    for url in result_urls:
        try:
            html = run_fetch(url)
        except subprocess.CalledProcessError:
            gaps.append(f"failed-result-fetch:{url}")
            continue
        found = extract_detail_urls(html)
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
            html = run_fetch(detail_url)
        except subprocess.CalledProcessError:
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
    parser.add_argument("--output")
    args = parser.parse_args()

    payload = build_output(
        municipality=args.municipality,
        location_scope=args.location_scope,
        include_houses=args.include_houses,
        include_land=args.include_land,
        result_urls=args.result_url,
        detail_urls=args.detail_url,
    )

    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
