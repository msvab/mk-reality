import json
from pathlib import Path

from .ads_state import atomic_write_json
from .build_real_estate_ads_by_city import ad_identity_keys, build_aggregate_output, index_previous_ads


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
