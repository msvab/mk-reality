#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
import sys
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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
    return urlunsplit(("https", "reality.idnes.cz", parts.path.rstrip("/") + "/", "", ""))


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


def build_output(municipality: str, location_scope: str, detail_urls: list[str]) -> dict:
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
    candidate_urls = [canonicalize_detail_url(url) for url in detail_urls if "/detail/" in url]
    coverage["candidates_gathered"] = len(candidate_urls)

    for detail_url in candidate_urls:
        try:
            html = run_fetch(detail_url, attempts=fetch_attempts, stage="detail_fetch")
        except RuntimeError as exc:
            gaps.append(f"failed-detail-fetch:{detail_url}:{exc}")
            coverage["blocked_portals"].append(f"reality.idnes.cz detail fetch failed: {detail_url}: {exc}")
            continue
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

    listings.sort(key=lambda item: re.sub(r"[^\d]", "", item["price"]), reverse=True)
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
    parser = argparse.ArgumentParser(description="Fetch and normalize cached reality.idnes.cz detail listings for one municipality.")
    parser.add_argument("--municipality", required=True)
    parser.add_argument("--location-scope", default="municipality_only")
    parser.add_argument("--detail-url", action="append", default=[])
    parser.add_argument("--output")
    args = parser.parse_args()

    payload = build_output(
        municipality=args.municipality,
        location_scope=args.location_scope,
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
