import argparse
import json
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
        "assumptions": [],
        "gaps": ["missing-raw-skill-output"],
        "ads": [],
    }


def build_city_bundle(raw_payload: dict) -> dict:
    normalized = build_output(raw_payload)
    return {
        "count": len(normalized["listings"]),
        "coverage": normalized["coverage"],
        "assumptions": normalized.get("assumptions", []),
        "gaps": normalized.get("gaps", []),
        "ads": normalized["listings"],
    }


def discover_raw_files(raw_dir: Path) -> list[Path]:
    return sorted(path for path in raw_dir.glob("*.json") if path.is_file())


def build_aggregate_output(schools_input: Path, raw_dir: Path) -> dict:
    school_cities = load_school_cities(schools_input)
    raw_files = discover_raw_files(raw_dir)

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

    output = {
        "generated_at": build_output({"listings": []})["generated_at"],
        "schools_input": str(schools_input),
        "raw_dir": str(raw_dir),
        "coverage": {
            "school_cities": len(school_cities),
            "raw_files_found": len(raw_files),
            "cities_with_ads": sum(1 for bundle in cities.values() if bundle["count"] > 0),
            "cities_with_raw_output": sum(1 for bundle in cities.values() if "missing-raw-skill-output" not in bundle["gaps"]),
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
    args = parser.parse_args()

    schools_input = Path(args.schools_input)
    raw_dir = Path(args.raw_dir)
    output_path = Path(args.output)

    output = build_aggregate_output(schools_input, raw_dir)

    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(output['cities'])} cities to {output_path}")


if __name__ == "__main__":
    main()
