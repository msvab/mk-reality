import argparse
import json
import re
import urllib.parse
from pathlib import Path

from build_real_estate_ads_json import build_output


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def load_school_cities(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON array.")
    cities = []
    seen = set()
    for row in payload:
        if not isinstance(row, dict):
            continue
        city = str(row.get("city", "")).strip()
        if not city or city in seen:
            continue
        seen.add(city)
        cities.append(city)
    return cities


def extract_city_name(payload: dict, fallback_path: Path) -> str:
    city = payload.get("city")
    if city:
        return str(city).strip()
    query = payload.get("query", {})
    if isinstance(query, dict) and query.get("municipality"):
        return str(query["municipality"]).strip()
    return fallback_path.stem


def empty_city_bundle() -> dict:
    return {
        "count": 0,
        "coverage": {
            "workers_launched": 0,
            "workers_with_results": 0,
            "candidates_gathered": 0,
            "rows_retained": 0,
            "zero_result_portals": [],
            "blocked_portals": [],
        },
        "portal_status": {},
        "fetch_attempts": [],
        "assumptions": [],
        "gaps": ["missing-raw-skill-output"],
        "ads": [],
        "hidden_ads": [],
    }


def build_city_bundle(raw_payload: dict) -> dict:
    normalized = build_output(raw_payload)
    return {
        "count": len(normalized["listings"]),
        "coverage": normalized["coverage"],
        "portal_status": normalized.get("portal_status", {}),
        "fetch_attempts": normalized.get("fetch_attempts", []),
        "assumptions": normalized.get("assumptions", []),
        "gaps": normalized.get("gaps", []),
        "ads": normalized["listings"],
        "hidden_ads": [],
    }


def discover_raw_files(raw_dir: Path) -> list[Path]:
    return sorted(path for path in raw_dir.glob("*.json") if path.is_file())


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip().lower())


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    value = str(url).strip()
    if not value:
        return None
    parsed = urllib.parse.urlparse(value if value.startswith(("http://", "https://")) else f"https://{value}")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urllib.parse.urlunparse(parsed._replace(query="", fragment=""))


def ad_identity_keys(ad: dict) -> set[tuple[str, str]]:
    keys = {("url", url) for url in (normalize_url(item) for item in ad.get("urls", [])) if url}
    fingerprint = (
        normalize_text(ad.get("location", "")),
        normalize_text(ad.get("property_type", "")),
        normalize_text(ad.get("title", "")),
        str(ad.get("house_area_m2") or ""),
        str(ad.get("land_area_m2") or ""),
    )
    if any(fingerprint):
        keys.add(("fingerprint", "|".join(fingerprint)))
    return keys


def index_previous_ads(previous_bundle: dict | None) -> dict[tuple[str, str], list[dict]]:
    if not isinstance(previous_bundle, dict):
        return {}
    indexed = {}
    for bucket in ("ads", "hidden_ads"):
        ads = previous_bundle.get(bucket, [])
        if not isinstance(ads, list):
            continue
        for ad in ads:
            if not isinstance(ad, dict):
                continue
            for key in ad_identity_keys(ad):
                indexed.setdefault(key, []).append(ad)
    return indexed


def price_history_entry(ad: dict, seen_at: str) -> dict:
    return {
        "seen_at": seen_at,
        "price": ad.get("price"),
        "price_czk": ad.get("price_czk"),
    }


def normalize_price_history(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    history = []
    for item in value:
        if not isinstance(item, dict):
            continue
        seen_at = str(item.get("seen_at") or "").strip()
        if not seen_at:
            continue
        history.append(
            {
                "seen_at": seen_at,
                "price": item.get("price"),
                "price_czk": item.get("price_czk"),
            }
        )
    return history


def merge_price_history(ad: dict, previous: dict | None, generated_at: str) -> list[dict]:
    if not previous:
        return [price_history_entry(ad, generated_at)]

    history = normalize_price_history(previous.get("price_history"))
    if not history:
        history = [price_history_entry(previous, previous.get("first_seen_at", previous.get("last_seen_at", generated_at)))]

    previous_price = previous.get("price_czk")
    current_price = ad.get("price_czk")
    if previous_price != current_price:
        last = history[-1] if history else {}
        if last.get("price_czk") != current_price or last.get("price") != ad.get("price"):
            history.append(price_history_entry(ad, generated_at))
    return history


def merge_previous_city_bundle(bundle: dict, previous_bundle: dict | None, generated_at: str) -> dict:
    if "missing-raw-skill-output" in bundle.get("gaps", []) and isinstance(previous_bundle, dict):
        preserved = dict(previous_bundle)
        preserved.setdefault("hidden_ads", [])
        preserved["count"] = len(preserved.get("ads", [])) if isinstance(preserved.get("ads"), list) else 0
        return preserved

    indexed_previous = index_previous_ads(previous_bundle)
    matched_previous_ids = set()
    active_ads = []

    for ad in bundle["ads"]:
        keys = ad_identity_keys(ad)
        previous = next((indexed_previous[key][0] for key in keys if key in indexed_previous), None)
        for key in keys:
            if key in indexed_previous:
                matched_previous_ids.update(id(item) for item in indexed_previous[key])
        enriched = dict(ad)
        enriched["status"] = "active"
        enriched["first_seen_at"] = previous.get("first_seen_at", previous.get("last_seen_at", generated_at)) if previous else generated_at
        enriched["last_seen_at"] = generated_at
        enriched["price_history"] = merge_price_history(ad, previous, generated_at)
        active_ads.append(enriched)

    hidden_ads = []
    if isinstance(previous_bundle, dict):
        previous_active = previous_bundle.get("ads", [])
        previous_hidden = previous_bundle.get("hidden_ads", [])
        if not isinstance(previous_active, list):
            previous_active = []
        if not isinstance(previous_hidden, list):
            previous_hidden = []
        for previous in previous_active + previous_hidden:
            if not isinstance(previous, dict) or id(previous) in matched_previous_ids:
                continue
            hidden = dict(previous)
            hidden["status"] = "hidden"
            hidden.setdefault("first_seen_at", previous.get("last_seen_at", generated_at))
            hidden.setdefault("last_seen_at", previous.get("last_seen_at", generated_at))
            hidden.setdefault("hidden_at", generated_at)
            hidden_ads.append(hidden)

    merged = dict(bundle)
    merged["ads"] = active_ads
    merged["hidden_ads"] = hidden_ads
    merged["count"] = len(active_ads)
    return merged


def build_aggregate_output(schools_input: Path, raw_dir: Path, previous_aggregate: dict | None = None) -> dict:
    school_cities = load_school_cities(schools_input)
    raw_files = discover_raw_files(raw_dir)
    generated_at = build_output({"listings": []})["generated_at"]
    previous_cities = previous_aggregate.get("cities", {}) if isinstance(previous_aggregate, dict) else {}
    if not isinstance(previous_cities, dict):
        previous_cities = {}

    cities = {city: empty_city_bundle() for city in school_cities}
    unmatched_files = []

    for raw_file in raw_files:
        raw_payload = load_json(raw_file)
        city = extract_city_name(raw_payload, raw_file)
        bundle = build_city_bundle(raw_payload)
        if city in cities:
            cities[city] = bundle
        else:
            unmatched_files.append({"city": city, "file": raw_file.name})

    cities = {
        city: merge_previous_city_bundle(bundle, previous_cities.get(city), generated_at)
        for city, bundle in cities.items()
    }

    output = {
        "generated_at": generated_at,
        "schools_input": str(schools_input),
        "raw_dir": str(raw_dir),
        "coverage": {
            "school_cities": len(school_cities),
            "raw_files_found": len(raw_files),
            "cities_with_ads": sum(1 for bundle in cities.values() if bundle["count"] > 0),
            "hidden_ads": sum(len(bundle.get("hidden_ads", [])) for bundle in cities.values()),
            "cities_with_raw_output": sum(1 for bundle in cities.values() if "missing-raw-skill-output" not in bundle["gaps"]),
            "cities_with_portal_warnings": sum(
                1
                for bundle in cities.values()
                if any(status.get("status") not in {"ok", "no_results"} for status in bundle.get("portal_status", {}).values())
            ),
        },
        "unmatched_raw_files": unmatched_files,
        "cities": cities,
    }
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate per-city real estate skill outputs into one HTML-ready JSON file.")
    parser.add_argument("--schools-input", default="dobruska_primary_schools.json", help="Path to the schools JSON used as the city source list.")
    parser.add_argument("--raw-dir", required=True, help="Directory with one raw skill-output JSON file per city.")
    parser.add_argument("--output", default="real_estate_ads_by_city.json", help="Path to the aggregated JSON output.")
    parser.add_argument("--previous-aggregate", default=None, help="Optional previous aggregate used to hide ads missing from the latest raw snapshot.")
    args = parser.parse_args()

    schools_input = Path(args.schools_input)
    raw_dir = Path(args.raw_dir)
    output_path = Path(args.output)
    previous_aggregate = load_json(Path(args.previous_aggregate)) if args.previous_aggregate else None

    output = build_aggregate_output(schools_input, raw_dir, previous_aggregate=previous_aggregate)

    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(output['cities'])} cities to {output_path}")


if __name__ == "__main__":
    main()
