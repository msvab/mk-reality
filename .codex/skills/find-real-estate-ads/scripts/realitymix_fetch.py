#!/usr/bin/env python3

import argparse
import json
import re
import subprocess
import sys
import time
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

BASE_URL = "https://www.realitymix.cz"
FETCH_BASE_URL = "https://www.realitymix.cz"
USER_AGENT = "Mozilla/5.0"
REMOVED_MARKER = "Požadovaný inzerát již není v naší databázi"
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF_SECONDS = 2.0

CATEGORY_ROOTS = {
    "house": ["/reality/domy/prodej/"],
    "land": ["/reality/pozemky/pro-bydleni/", "/reality/pozemky/pro-bydleni/prodej/"],
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
        "portal": "realitymix.cz",
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
    fetch_url = url.replace("https://realitymix.cz", FETCH_BASE_URL).replace("http://realitymix.cz", FETCH_BASE_URL)
    last_error = None
    for attempt in range(1, retries + 2):
        completed = subprocess.run(
            ["curl", "-sL", "-A", USER_AGENT, "-w", "\n__HTTP_STATUS__:%{http_code}", fetch_url],
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
        if status == "ok":
            return body
        last_error = error or status
        if attempt <= retries:
            time.sleep(backoff_seconds * attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def canonicalize_detail_url(url: str) -> str:
    parts = urlsplit(url)
    scheme = "https"
    netloc = "realitymix.cz"
    path = parts.path.rstrip("/")
    if path.endswith(".html"):
        path = path
    return urlunsplit((scheme, netloc, path, "", ""))


def find_municipality_path(root_html: str, municipality: str) -> str | None:
    pattern = re.compile(
        r'<input[^>]+name="form\[adresa_obec_id]\[(?P<district>\d+)]\[\]"[^>]*>'
        r'\s*<label[^>]*>\s*<a href="(?P<href>[^"]+)"[^>]*>\s*(?P<label>.*?)\s*</a>',
        re.S,
    )
    target = slug_normalize(municipality)
    matches = []
    for match in pattern.finditer(root_html):
        label = re.sub(r"<[^>]+>", "", unescape(match.group("label"))).strip()
        if slug_normalize(label) == target:
            matches.append(match.group("href"))
    if not matches:
        return None
    return matches[0]


def extract_detail_urls(result_html: str, municipality: str) -> list[str]:
    urls = set()
    id_queue_match = re.search(r'<span id="id_queue" data-ids="([^"]+)"', result_html)
    if id_queue_match:
        raw = unescape(id_queue_match.group(1))
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            seo_city = str(item.get("seo_mesto", "")).strip()
            seo_name = str(item.get("seo_nazev", "")).strip()
            listing_id = item.get("nabidka_id")
            if seo_city and seo_name and listing_id:
                urls.add(canonicalize_detail_url(f"{BASE_URL}/detail/{seo_city}/{seo_name}-{listing_id}.html"))

    for href in re.findall(r'href="(https://realitymix\.cz/detail/[^"]+)"', result_html):
        urls.add(canonicalize_detail_url(href))
    for href in re.findall(r'href="(https://www\.realitymix\.cz/detail/[^"]+)"', result_html):
        urls.add(canonicalize_detail_url(href))
    for href in re.findall(r'href="(/detail/[^"]+)"', result_html):
        urls.add(canonicalize_detail_url(urljoin(BASE_URL, href)))

    target = f"/detail/{slug_normalize(municipality).replace(' ', '-')}/"
    ordered = sorted(urls)
    return [url for url in ordered if target in slug_normalize(url).replace(" ", "-")]


def extract_meta_content(html: str, prop: str) -> str | None:
    match = re.search(
        rf'<meta\s+(?:property|name)="{re.escape(prop)}"\s+content="([^"]*)"',
        html,
        re.IGNORECASE,
    )
    return unescape(match.group(1)).strip() if match else None


def extract_canonical_url(html: str, fallback: str) -> str:
    match = re.search(r'<link rel="canonical" href="([^"]+)"', html, re.IGNORECASE)
    if match:
        return unescape(match.group(1)).strip()
    return fallback


def extract_location(html: str) -> str | None:
    match = re.search(r'<p class="advert-detail-heading__address">\s*(.*?)\s*</p>', html, re.S)
    if not match:
        return None
    text = re.sub(r"<[^>]+>", " ", match.group(1))
    return re.sub(r"\s+", " ", unescape(text)).strip()


def extract_detail_pairs(html: str) -> dict[str, str]:
    pairs = {}
    for key, value in re.findall(
        r'<li class="detail-information__data-item">\s*<span>(.*?)</span>\s*<span>(.*?)</span>',
        html,
        re.S,
    ):
        clean_key = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(key))).strip().rstrip(":")
        clean_value = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value))).strip()
        if clean_key:
            pairs[clean_key] = clean_value
    return pairs


def extract_price(html: str) -> str | None:
    match = re.search(r'<tr class="advert-description__short-props-price">.*?<td>Cena:</td>\s*<td>(.*?)</td>', html, re.S)
    if not match:
        return None
    text = re.sub(r"<[^>]+>", " ", unescape(match.group(1)))
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+Nabídněte cenu$", "", text)
    return text or None


def extract_area_value(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"(\d[\d\s.]*)\s*m", text)
    if not match:
        return None
    digits = re.sub(r"[^\d]", "", match.group(1))
    return int(digits) if digits else None


def infer_property_type(title: str, canonical_url: str) -> str:
    lowered = f"{title} {canonical_url}".casefold()
    if "pozem" in lowered:
        return "land"
    return "house"


def land_is_buildable(title: str, html: str) -> bool:
    details = extract_detail_pairs(html)
    land_type = details.get("Druh pozemku", "")
    description_match = re.search(r'<h2[^>]*>.*?</h2>\s*<p>(.*?)</p>', html, re.S)
    description = description_match.group(1) if description_match else ""
    lowered = " ".join([title, land_type, re.sub(r"<[^>]+>", " ", description)]).casefold()
    if any(marker in lowered for marker in ["zeměděl", "zemedel", "orná půda", "orna puda", "pole"]):
        return False
    return any(marker in lowered for marker in ["staveb", "pro bydlení", "pro-bydleni"])


def discover_result_page_url(
    category: str,
    municipality: str,
    attempts: list[dict],
    retries: int,
    backoff_seconds: float,
) -> str | None:
    target_slug = slug_normalize(municipality).replace(" ", "-")
    pattern = re.compile(r'href="(/reality/[^"]+/[^"/?#]+)"')
    for category_root in CATEGORY_ROOTS[category]:
        root_url = urljoin(BASE_URL, category_root)
        root_html = run_fetch(
            root_url,
            attempts=attempts,
            stage=f"{category}_root_fetch",
            retries=retries,
            backoff_seconds=backoff_seconds,
        )
        candidates = []
        for href in pattern.findall(root_html):
            lowered = slug_normalize(href).replace(" ", "-")
            if not lowered.startswith(category_root):
                continue
            if not lowered.endswith(f"/{target_slug}"):
                continue
            candidates.append(urljoin(BASE_URL, href))
        if candidates:
            return sorted(set(candidates))[0]
    return None


def municipality_note(location: str, municipality: str) -> str | None:
    parts = [part.strip() for part in location.split(",") if part.strip()]
    if len(parts) < 2:
        return None
    if slug_normalize(parts[1]) == slug_normalize(municipality) and slug_normalize(parts[0]) != slug_normalize(municipality):
        return f"municipal-part:{parts[0]}"
    return None


def listing_from_detail(url: str, html: str, municipality: str) -> tuple[dict | None, str | None]:
    if REMOVED_MARKER.casefold() in html.casefold():
        return None, "removed-fallback-page"

    canonical_url = extract_canonical_url(html, url)
    title = extract_meta_content(html, "og:title") or extract_meta_content(html, "title") or ""
    location = extract_location(html) or municipality
    if slug_normalize(municipality) not in slug_normalize(location):
        return None, "outside-municipality"

    details = extract_detail_pairs(html)
    property_type = infer_property_type(title, canonical_url)
    price = extract_price(html)
    house_area = details.get("Užitná plocha")
    land_area = details.get("Plocha parcely") or details.get("Celková plocha")

    house_area_m2 = extract_area_value(house_area)
    land_area_m2 = extract_area_value(land_area)

    if not price:
        return None, "missing-price"
    if property_type in {"house", "land"} and (land_area_m2 is None or land_area_m2 < 1000):
        return None, "land-below-threshold"
    if property_type == "land" and not land_is_buildable(title, html):
        return None, "non-buildable-land"

    notes = ["detail-url-verified:realitymix.cz"]
    municipal_part = municipality_note(location, municipality)
    if municipal_part:
        notes.append(municipal_part)
    if property_type == "land" and "staveb" in title.casefold():
        notes.append("buildable-land")

    return {
        "portal": ["realitymix.cz"],
        "title": title or "unknown",
        "location": location,
        "property_type": property_type,
        "price": price,
        "house_area_m2": str(house_area_m2) if house_area_m2 is not None else "unknown",
        "land_area_m2": str(land_area_m2) if land_area_m2 is not None else "unknown",
        "urls": [canonical_url],
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
        output["message"] = "Retained at least one detail-verified RealityMix row."
    else:
        output["message"] = "No retained in-scope RealityMix rows."
    return output


def collect_category_urls(
    category: str,
    municipality: str,
    page_url: str | None,
    attempts: list[dict],
    retries: int,
    backoff_seconds: float,
) -> tuple[list[str], list[str]]:
    if not page_url:
        page_url = discover_result_page_url(category, municipality, attempts, retries, backoff_seconds)
    if not page_url:
        return [], [f"missing-page-url:{category}"]
    result_html = run_fetch(
        page_url,
        attempts=attempts,
        stage=f"{category}_result_fetch",
        retries=retries,
        backoff_seconds=backoff_seconds,
    )
    return extract_detail_urls(result_html, municipality), []


def build_output(
    municipality: str,
    location_scope: str,
    include_houses: bool,
    include_land: bool,
    house_page_url: str | None,
    land_page_url: str | None,
    detail_urls: list[str] | None = None,
    discover_results: bool = False,
    retries: int = DEFAULT_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
) -> dict:
    has_explicit_detail_urls = bool(detail_urls)
    categories = []
    if include_houses and (house_page_url or discover_results or not has_explicit_detail_urls):
        categories.append("house")
    if include_land and (land_page_url or discover_results or not has_explicit_detail_urls):
        categories.append("land")

    assumptions = []
    if location_scope == "municipality_only":
        assumptions.append(
            f"`municipality_only` includes municipal parts explicitly shown by RealityMix as part of {municipality}."
        )

    coverage = {
        "workers_launched": 1,
        "workers_with_results": 0,
        "candidates_gathered": 0,
        "rows_retained": 0,
        "zero_result_portals": [],
        "blocked_portals": [],
    }
    gaps = []
    listings = []
    seen_urls = set()
    fetch_attempts = []

    def verify_detail_url(detail_url: str) -> None:
        if detail_url in seen_urls:
            return
        seen_urls.add(detail_url)
        try:
            html = run_fetch(
                detail_url,
                attempts=fetch_attempts,
                stage="detail_fetch",
                retries=retries,
                backoff_seconds=backoff_seconds,
            )
        except RuntimeError as exc:
            gaps.append(f"detail-fetch-error:{detail_url}")
            coverage["blocked_portals"].append(f"realitymix.cz detail fetch failed: {detail_url}: {exc}")
            return
        listing, reason = listing_from_detail(detail_url, html, municipality)
        if listing is not None:
            listings.append(listing)
        elif reason:
            if reason == "removed-fallback-page":
                append_fetch_attempt(
                    fetch_attempts,
                    url=detail_url,
                    stage="detail_parse",
                    attempt=1,
                    status="fallback_page",
                    message=REMOVED_MARKER,
                )
            gaps.append(f"{reason}:{detail_url}")

    explicit_detail_urls = [canonicalize_detail_url(url) for url in detail_urls or [] if "/detail/" in url]
    coverage["candidates_gathered"] += len(explicit_detail_urls)
    for detail_url in explicit_detail_urls:
        verify_detail_url(detail_url)

    for category in categories:
        page_url = house_page_url if category == "house" else land_page_url
        try:
            detail_urls, category_gaps = collect_category_urls(
                category,
                municipality,
                page_url,
                fetch_attempts,
                retries,
                backoff_seconds,
            )
            gaps.extend(category_gaps)
        except RuntimeError as exc:
            gaps.append(f"category-fetch-error:{category}:{exc}")
            coverage["blocked_portals"].append(f"realitymix.cz {category} result fetch failed: {exc}")
            continue
        coverage["candidates_gathered"] += len(detail_urls)
        for detail_url in detail_urls:
            verify_detail_url(detail_url)

    listings.sort(key=lambda item: re.sub(r"[^\d]", "", item["price"]), reverse=True)
    coverage["rows_retained"] = len(listings)
    coverage["workers_with_results"] = 1 if listings else 0
    if not listings:
        coverage["zero_result_portals"].append("realitymix.cz")

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
            "realitymix.cz": build_portal_status(fetch_attempts, listings),
        },
        "fetch_attempts": fetch_attempts,
        "gaps": gaps,
        "listings": listings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch and normalize realitymix.cz listings for one municipality.")
    parser.add_argument("--municipality", required=True)
    parser.add_argument("--location-scope", default="municipality_only")
    parser.add_argument("--include-houses", action="store_true", default=True)
    parser.add_argument("--include-land", action="store_true", default=True)
    parser.add_argument("--house-page-url")
    parser.add_argument("--land-page-url")
    parser.add_argument("--detail-url", action="append", default=[])
    parser.add_argument(
        "--discover-results",
        action="store_true",
        help="Discover municipality result pages and verify their detail URLs even when explicit detail URLs are supplied.",
    )
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES, help="Retries per RealityMix fetch after the first attempt.")
    parser.add_argument("--backoff-seconds", type=float, default=DEFAULT_BACKOFF_SECONDS, help="Base retry backoff in seconds.")
    parser.add_argument("--output")
    args = parser.parse_args()

    payload = build_output(
        municipality=args.municipality,
        location_scope=args.location_scope,
        include_houses=args.include_houses,
        include_land=args.include_land,
        house_page_url=args.house_page_url,
        land_page_url=args.land_page_url,
        detail_urls=args.detail_url,
        discover_results=args.discover_results,
        retries=args.retries,
        backoff_seconds=args.backoff_seconds,
    )

    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
