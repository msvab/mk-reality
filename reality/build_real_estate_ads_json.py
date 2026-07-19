import argparse
import json
import re
import time
import urllib.parse
from pathlib import Path

DEFAULT_WORKERS = [
    "reality.idnes.cz",
    "realitymix.cz",
    "reality.aktualne.cz",
    "sreality.cz",
]
REALITY_AKTUALNE_CANDIDATE_EXCLUSION_REASONS = {
    "inactive-or-unpriced",
}

PORTAL_STATUS_PRECEDENCE = {
    "ok": 0,
    "no_results": 1,
    "inactive": 2,
    "fallback_page": 3,
    "partial": 4,
    "fetch_error": 5,
    "blocked": 6,
    "timeout": 7,
    "dns_error": 8,
    "rate_limited": 9,
}


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    value = str(url).strip()
    if not value:
        return None
    if not (value.startswith("http://") or value.startswith("https://")):
        value = "https://" + value
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    cleaned = parsed._replace(query="", fragment="")
    return urllib.parse.urlunparse(cleaned)


def normalize_list(value) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out = []
    seen = set()
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def slugify_text(text: str) -> str:
    value = normalize_text(text)
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
    for source, target in replacements.items():
        value = value.replace(source, target)
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def detect_fetch_status(text: str) -> tuple[str | None, int | None]:
    normalized = normalize_text(text)
    if not normalized:
        return None, None
    has_http_429 = (
        re.search(r"\bhttp\s*(?:status\s*)?429\b", normalized)
        or re.search(r"\bstatus(?:\s+code)?\s*429\b", normalized)
        or re.search(r"\b429\b.*\btoo many requests\b", normalized)
    )
    if has_http_429 or "too many requests" in normalized or "rate-limit" in normalized or "rate limit" in normalized:
        return "rate_limited", 429
    if "dns" in normalized:
        return "dns_error", None
    if "timeout" in normalized or "timed out" in normalized:
        return "timeout", None
    if "blocked" in normalized:
        return "blocked", None
    if "fallback page" in normalized or "detail-fallback" in normalized:
        return "fallback_page", None
    if "inactive" in normalized or "no longer in offer" in normalized or "no longer in our database" in normalized:
        return "inactive", None
    if (
        "cache-miss" in normalized
        or "cache miss" in normalized
        or "internal fetch" in normalized
        or "fetch failed" in normalized
        or "detail fetch failed" in normalized
        or "detail-open-cache-miss" in normalized
    ):
        return "fetch_error", None
    if "snapshot" in normalized or "indexed" in normalized or "partial" in normalized or "intermittent" in normalized:
        return "partial", None
    return None, None


def parse_candidate_exclusion(text: str) -> dict | None:
    raw_text = str(text).strip()
    if not raw_text:
        return None
    reason, separator, raw_url = raw_text.partition(":")
    if not separator:
        return None
    reason = reason.strip()
    url = normalize_url(raw_url)
    if reason not in REALITY_AKTUALNE_CANDIDATE_EXCLUSION_REASONS or not url:
        return None
    if "reality.aktualne.cz" not in url:
        return None
    return {
        "portal": "reality.aktualne.cz",
        "status": "inactive",
        "reason": reason,
        "url": url,
        "message": raw_text,
        "evidence": [raw_text],
    }


def is_stale_detail_attempt(attempt: dict) -> bool:
    if attempt.get("status") != "fallback_page":
        return False
    stage = str(attempt.get("stage") or "")
    url = str(attempt.get("url") or "")
    return stage in {"detail_fetch", "detail_parse", "discovery_detail_fetch"} and "/detail/" in url


def is_stale_detail_evidence(text: str) -> bool:
    normalized = normalize_text(text)
    if "detail" not in normalized or "fallback" not in normalized:
        return False
    return any(portal in text for portal in DEFAULT_WORKERS)


def normalize_legacy_portal_status(portal: str, status: dict) -> dict | None:
    normalized = normalize_portal_status_entry(status)
    status_name = normalized.get("status")
    stage = str(normalized.get("stage") or "")
    evidence = [str(item) for item in normalized.get("evidence", []) if isinstance(item, str)]
    if (
        status_name == "fallback_page"
        and stage in {"detail_fetch", "detail_parse", "discovery_detail_fetch"}
        and evidence
        and all(is_stale_detail_evidence(item) for item in evidence)
    ):
        return None
    return normalized


def candidate_exclusion_key(exclusion: dict) -> tuple:
    return (
        exclusion.get("portal"),
        exclusion.get("status"),
        exclusion.get("reason"),
        exclusion.get("url"),
    )


def extract_candidate_exclusions(payload: dict) -> list[dict]:
    exclusions = []
    seen = set()
    gaps = payload.get("gaps", [])
    if not isinstance(gaps, list):
        return exclusions
    for item in gaps:
        exclusion = parse_candidate_exclusion(str(item))
        if not exclusion:
            continue
        key = candidate_exclusion_key(exclusion)
        if key in seen:
            continue
        seen.add(key)
        exclusions.append(exclusion)
    return exclusions


def merge_portal_status(
    statuses: dict[str, dict],
    portal: str,
    status: str,
    *,
    http_status: int | None = None,
    stage: str | None = None,
    retained_from_snapshot: bool | None = None,
    message: str | None = None,
    evidence: str | None = None,
) -> None:
    if portal not in DEFAULT_WORKERS:
        return
    current = statuses.get(portal, {})
    current_status = current.get("status", "ok")
    status_upgraded = PORTAL_STATUS_PRECEDENCE.get(status, -1) > PORTAL_STATUS_PRECEDENCE.get(current_status, -1)
    if status_upgraded or PORTAL_STATUS_PRECEDENCE.get(status, -1) == PORTAL_STATUS_PRECEDENCE.get(current_status, -1):
        current["status"] = status
    if http_status is not None:
        current["http_status"] = http_status
    if stage and not current.get("stage"):
        current["stage"] = stage
    if retained_from_snapshot is True:
        current["retained_from_snapshot"] = True
    elif retained_from_snapshot is not None and "retained_from_snapshot" not in current:
        current["retained_from_snapshot"] = retained_from_snapshot
    if message and (status_upgraded or not current.get("message")):
        current["message"] = message
    if evidence:
        current.setdefault("evidence", [])
        if evidence not in current["evidence"]:
            current["evidence"].append(evidence)
    statuses[portal] = current


def normalize_portal_status_entry(raw: dict) -> dict:
    status = str(raw.get("status") or "unknown").strip() or "unknown"
    output = {"status": status}
    http_status = raw.get("http_status")
    if isinstance(http_status, int):
        output["http_status"] = http_status
    for key in ("stage", "message"):
        value = str(raw.get(key) or "").strip()
        if value:
            output[key] = value
    retained_from_snapshot = raw.get("retained_from_snapshot")
    if isinstance(retained_from_snapshot, bool):
        output["retained_from_snapshot"] = retained_from_snapshot
    evidence = normalize_list(raw.get("evidence"))
    if evidence:
        output["evidence"] = evidence
    return output


def normalize_fetch_attempts(payload: dict) -> list[dict]:
    raw_attempts = payload.get("fetch_attempts", [])
    if not isinstance(raw_attempts, list):
        return []
    attempts = []
    seen = set()
    for raw in raw_attempts:
        if not isinstance(raw, dict):
            continue
        portal = str(raw.get("portal") or "").strip()
        url = normalize_url(raw.get("url"))
        stage = str(raw.get("stage") or "").strip()
        status = str(raw.get("status") or "").strip()
        attempt_number = raw.get("attempt")
        if not portal or not url or not stage or not status or not isinstance(attempt_number, int) or attempt_number < 1:
            continue
        item = {
            "portal": portal,
            "url": url,
            "stage": stage,
            "attempt": attempt_number,
            "status": status,
        }
        http_status = raw.get("http_status")
        if isinstance(http_status, int):
            item["http_status"] = http_status
        for key in ("error", "message"):
            value = str(raw.get(key) or "").strip()
            if value:
                item[key] = value
        key = tuple(item.get(field) for field in ("portal", "url", "stage", "attempt", "status", "http_status"))
        if key in seen:
            continue
        seen.add(key)
        attempts.append(item)
    return attempts


def infer_portal_status(
    payload: dict,
    listings: list[dict],
    coverage: dict,
    fetch_attempts: list[dict],
) -> dict[str, dict]:
    statuses: dict[str, dict] = {}
    raw_statuses = payload.get("portal_status", {})
    if isinstance(raw_statuses, dict):
        for portal, raw_status in raw_statuses.items():
            portal_name = str(portal).strip()
            if portal_name in DEFAULT_WORKERS and isinstance(raw_status, dict):
                normalized_status = normalize_legacy_portal_status(portal_name, raw_status)
                if normalized_status is not None:
                    statuses[portal_name] = normalized_status

    for portal in coverage.get("zero_result_portals", []):
        merge_portal_status(statuses, str(portal).strip(), "no_results", message="No retained in-scope rows.")

    seen_portals = {portal for row in listings for portal in row["portal"]}
    for portal in seen_portals:
        merge_portal_status(statuses, portal, "ok", message="Retained at least one in-scope row.")

    for attempt in fetch_attempts:
        portal = attempt.get("portal")
        status = attempt.get("status")
        if status in {None, "ok", "no_results"}:
            continue
        if is_stale_detail_attempt(attempt):
            continue
        merge_portal_status(
            statuses,
            str(portal),
            str(status),
            http_status=attempt.get("http_status") if isinstance(attempt.get("http_status"), int) else None,
            stage=str(attempt.get("stage") or "") or None,
            message=str(attempt.get("error") or attempt.get("message") or "") or None,
            evidence=f"{attempt.get('stage')}:{attempt.get('status')}:{attempt.get('url')}",
        )

    evidence_sources = []
    evidence_sources.extend(str(item) for item in coverage.get("blocked_portals", []))
    evidence_sources.extend(str(item) for item in payload.get("gaps", []) if isinstance(item, str))
    for listing in listings:
        portals = [portal for portal in listing["portal"] if portal in DEFAULT_WORKERS]
        for note in listing.get("notes", []):
            note_text = str(note)
            mentioned = [portal for portal in portals if portal in note_text]
            target_portals = mentioned or (portals if len(portals) == 1 else [])
            status, http_status = detect_fetch_status(note_text)
            retained_from_snapshot = "snapshot" in normalize_text(note_text) or "indexed" in normalize_text(note_text)
            for portal in target_portals:
                if status:
                    merge_portal_status(
                        statuses,
                        portal,
                        status,
                        http_status=http_status,
                        stage="detail_fetch" if status in {"rate_limited", "fetch_error", "fallback_page"} else None,
                        retained_from_snapshot=retained_from_snapshot or None,
                        message=note_text,
                        evidence=note_text,
                    )
                elif retained_from_snapshot:
                    merge_portal_status(
                        statuses,
                        portal,
                        "partial",
                        retained_from_snapshot=True,
                        message=note_text,
                        evidence=note_text,
                    )

    for text in evidence_sources:
        if parse_candidate_exclusion(text):
            continue
        if is_stale_detail_evidence(text):
            continue
        status, http_status = detect_fetch_status(text)
        if not status:
            continue
        mentioned_portals = [portal for portal in DEFAULT_WORKERS if portal in text]
        if not mentioned_portals and text.strip() in DEFAULT_WORKERS:
            mentioned_portals = [text.strip()]
        for portal in mentioned_portals:
            if status == "inactive" and portal in seen_portals:
                continue
            merge_portal_status(
                statuses,
                portal,
                status,
                http_status=http_status,
                stage="detail_fetch" if status in {"rate_limited", "fetch_error", "fallback_page"} else None,
                retained_from_snapshot=("snapshot" in normalize_text(text) or "indexed" in normalize_text(text)) or None,
                message=text,
                evidence=text,
            )

    return {portal: statuses[portal] for portal in DEFAULT_WORKERS if portal in statuses}


def parse_price_czk(value) -> int | None:
    if value is None:
        return None
    text = normalize_text(str(value))
    if not text or text == "unknown":
        return None
    compact = text.replace("\xa0", " ")
    million_match = re.search(r"(\d+(?:[.,]\d+)?)\s*mil", compact)
    if million_match:
        return int(round(float(million_match.group(1).replace(",", ".")) * 1_000_000))
    currency_match = re.search(r"(\d[\d\s.,]*)\s*(?:kč|czk)\b", compact)
    price_text = currency_match.group(1) if currency_match else compact
    digits = re.sub(r"[^\d]", "", price_text)
    if not digits:
        return None
    return int(digits)


def format_czk(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " Kč"


def is_per_square_meter_price(value) -> bool:
    text = normalize_text(str(value or "")).replace("\xa0", " ")
    return bool(
        re.search(r"\bza\s*m\s*(?:2|²)\b", text)
        or re.search(r"/\s*(?:\(?\s*)?m\s*(?:2|²)\b", text)
        or re.search(r"\bkč\s*/\s*m\s*(?:2|²)\b", text)
    )


def parse_area_m2(value) -> int | None:
    if value is None:
        return None
    text = normalize_text(str(value))
    if not text or text == "unknown":
        return None
    match = re.search(r"\d[\d\s\xa0.,]*", text)
    if not match:
        return None
    digits = re.sub(r"[^\d]", "", match.group(0))
    if not digits:
        return None
    return int(digits)


def normalize_listing(raw: dict) -> dict | None:
    title = str(raw.get("title", "")).strip()
    location = str(raw.get("location", "")).strip()
    property_type = normalize_text(str(raw.get("property_type", "") or "unknown"))
    price = str(raw.get("price", "")).strip()
    portals = normalize_list(raw.get("portal"))
    urls = [url for url in (normalize_url(item) for item in normalize_list(raw.get("urls"))) if url]
    notes = normalize_list(raw.get("notes"))

    if not title or not location or not portals or not urls:
        return None

    price_czk = parse_price_czk(price)
    house_area_m2 = parse_area_m2(raw.get("house_area_m2"))
    land_area_m2 = parse_area_m2(raw.get("land_area_m2"))

    if price_czk is None:
        return None
    if property_type in {"house", "land"} and (land_area_m2 is None or land_area_m2 < 1000):
        return None
    if is_per_square_meter_price(price) and land_area_m2 is not None:
        unit_price = price_czk
        price_czk = unit_price * land_area_m2
        if f"unit-price:{price}" not in notes:
            notes.append(f"unit-price:{price}")
        price = format_czk(price_czk)

    return {
        "portal": portals,
        "title": title,
        "location": location,
        "property_type": property_type or "unknown",
        "price": price,
        "price_czk": price_czk,
        "house_area_m2": house_area_m2,
        "land_area_m2": land_area_m2,
        "urls": urls,
        "notes": notes,
    }


def listing_fingerprint(listing: dict) -> tuple:
    return (
        normalize_text(listing["location"]),
        listing["property_type"],
        normalize_text(listing["title"]),
        listing["house_area_m2"],
        listing["land_area_m2"],
    )


def title_has_numbered_parcel(title: str) -> bool:
    normalized = normalize_text(title)
    return bool(re.search(r"\b(?:č|c|číslo|cislo)\.?\s*\d+\b", normalized))


def listing_url_city_slugs(listing: dict) -> set[str]:
    slugs = set()
    for url in listing.get("urls", []):
        parsed = urllib.parse.urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        if "detail" not in parts:
            continue
        detail_index = parts.index("detail")
        if detail_index + 1 < len(parts):
            slugs.add(parts[detail_index + 1])
    return slugs


def listing_matches_municipality(listing: dict, municipality_slug: str | None) -> bool:
    if not municipality_slug:
        return False
    haystack = " ".join(
        [
            slugify_text(listing.get("title", "")),
            slugify_text(listing.get("location", "")),
            " ".join(listing_url_city_slugs(listing)),
        ]
    )
    return municipality_slug in haystack


def listing_identity_keys(listing: dict, municipality_slug: str | None = None) -> list[tuple]:
    keys = [("exact", *listing_fingerprint(listing))]
    if title_has_numbered_parcel(listing["title"]):
        return keys
    if not listing_matches_municipality(listing, municipality_slug):
        return keys
    area_key = (
        "area-price",
        municipality_slug,
        listing["property_type"],
        listing["price_czk"],
        listing["house_area_m2"] if listing["property_type"] == "house" else None,
        listing["land_area_m2"],
    )
    keys.append(area_key)
    return keys


def merge_listing(base: dict, incoming: dict) -> dict:
    merged = dict(base)
    merged["portal"] = sorted(set(base["portal"]) | set(incoming["portal"]))
    merged["urls"] = sorted(set(base["urls"]) | set(incoming["urls"]))
    merged["notes"] = list(base["notes"])
    for note in incoming["notes"]:
        if note not in merged["notes"]:
            merged["notes"].append(note)
    if incoming["price_czk"] < base["price_czk"]:
        merged["notes"].append(f"duplicate-price:{','.join(base['portal'])}={base['price']}")
        merged["price"] = incoming["price"]
        merged["price_czk"] = incoming["price_czk"]
    elif incoming["price_czk"] > base["price_czk"]:
        merged["notes"].append(f"duplicate-price:{','.join(incoming['portal'])}={incoming['price']}")
    for portal in incoming["portal"]:
        tag = f"duplicate-merged-from:{portal}"
        if tag not in merged["notes"]:
            merged["notes"].append(tag)
    if (incoming["house_area_m2"] or 0) > (merged["house_area_m2"] or 0):
        merged["house_area_m2"] = incoming["house_area_m2"]
    if (incoming["land_area_m2"] or 0) > (merged["land_area_m2"] or 0):
        merged["land_area_m2"] = incoming["land_area_m2"]
    return merged


def dedupe_and_sort(listings: list[dict], municipality: str | None = None) -> list[dict]:
    merged = {}
    key_to_primary = {}
    municipality_slug = slugify_text(municipality or "") or None
    for listing in listings:
        keys = listing_identity_keys(listing, municipality_slug)
        matched_key = next((key_to_primary[key] for key in keys if key in key_to_primary), None)
        if matched_key is not None:
            merged[matched_key] = merge_listing(merged[matched_key], listing)
            for key in keys:
                key_to_primary[key] = matched_key
        else:
            primary_key = keys[0]
            merged[primary_key] = listing
            for key in keys:
                key_to_primary[key] = primary_key
    return sorted(
        merged.values(),
        key=lambda row: (
            -row["price_czk"],
            -(1 if row["house_area_m2"] is not None else 0),
            -(1 if row["land_area_m2"] is not None else 0),
            ",".join(row["portal"]),
            normalize_text(row["title"]),
            normalize_text(row["location"]),
        ),
    )


def load_input(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Input JSON must be an object.")
    return payload


def build_output(payload: dict) -> dict:
    raw_listings = payload.get("listings", [])
    if not isinstance(raw_listings, list):
        raise ValueError("Input field 'listings' must be an array.")

    normalized = []
    zero_result_portals = []
    for raw in raw_listings:
        if not isinstance(raw, dict):
            continue
        listing = normalize_listing(raw)
        if listing:
            normalized.append(listing)

    municipality = None
    query = payload.get("query", {})
    if isinstance(query, dict):
        municipality = query.get("municipality")
    deduped = dedupe_and_sort(normalized, municipality=municipality)
    fetch_attempts = normalize_fetch_attempts(payload)
    candidate_exclusions = extract_candidate_exclusions(payload)

    coverage = payload.get("coverage", {}) if isinstance(payload.get("coverage"), dict) else {}
    workers_launched = coverage.get("workers_launched", len(DEFAULT_WORKERS))
    workers_with_results = coverage.get("workers_with_results")
    if workers_with_results is None:
        seen_portals = {portal for row in deduped for portal in row["portal"]}
        workers_with_results = sum(1 for portal in DEFAULT_WORKERS if portal in seen_portals)
        zero_result_portals = [portal for portal in DEFAULT_WORKERS if portal not in seen_portals]
    else:
        zero_result_portals = coverage.get("zero_result_portals", [])

    normalized_coverage = {
        "workers_launched": workers_launched,
        "workers_with_results": workers_with_results,
        "candidates_gathered": len(normalized),
        "rows_retained": len(deduped),
        "zero_result_portals": sorted(dict.fromkeys(str(portal) for portal in zero_result_portals)),
        "blocked_portals": sorted(dict.fromkeys(str(portal) for portal in coverage.get("blocked_portals", []))),
    }

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "query": payload.get("query", {}),
        "assumptions": payload.get("assumptions", []),
        "coverage": normalized_coverage,
        "portal_status": infer_portal_status(payload, deduped, normalized_coverage, fetch_attempts),
        "fetch_attempts": fetch_attempts,
        "candidate_exclusions": candidate_exclusions,
        "gaps": payload.get("gaps", []),
        "listings": deduped,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize real estate ads skill output into a JSON feed for HTML.")
    parser.add_argument("--input", required=True, help="Path to raw JSON input collected from the skill.")
    parser.add_argument("--output", default="real_estate_ads.json", help="Path to output JSON feed.")
    args = parser.parse_args()

    payload = load_input(Path(args.input))
    output = build_output(payload)
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(output['listings'])} listings to {args.output}")


if __name__ == "__main__":
    main()
