import argparse
import html
import json
import re
from pathlib import Path

from .refresh_real_estate_ads import aggregate_totals, format_counts, render_refresh_summary
from .summarize_real_estate_fetch_errors import iter_candidate_exclusions, iter_warnings

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AGGREGATE_PATH = ROOT / "real_estate_ads_by_city.json"
DEFAULT_STATE_PATH = ROOT / "real_estate_ads_run_state.json"
DEFAULT_HTML_PATH = ROOT / "index.html"
DEFAULT_RAW_DIR = ROOT / "data" / "real_estate_ads_raw"


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def raw_file_count(raw_dir: Path) -> int:
    return sum(1 for path in raw_dir.glob("*.json") if path.is_file())


def embedded_counts(html_path: Path) -> dict[str, int]:
    page = html_path.read_text(encoding="utf-8")
    match = re.search(r'<script id="ads-by-city-data" type="application/json">(.*?)</script>', page, re.S)
    if not match:
        raise ValueError(f"{html_path} is missing ads-by-city-data.")
    embedded = json.loads(html.unescape(match.group(1)))
    if not isinstance(embedded, dict):
        raise ValueError("ads-by-city-data must contain a JSON object.")
    counts = {}
    for city, bundle in embedded.items():
        if isinstance(bundle, dict):
            counts[str(city)] = int(bundle.get("count") or 0)
    return counts


def count_mismatches(aggregate: dict, html_path: Path) -> list[dict]:
    counts = embedded_counts(html_path)
    cities = aggregate.get("cities", {})
    if not isinstance(cities, dict):
        raise ValueError("real_estate_ads_by_city.json is missing a cities object.")
    mismatches = []
    for city, bundle in cities.items():
        if not isinstance(bundle, dict):
            continue
        expected = len(bundle.get("ads", []))
        actual = counts.get(city)
        if actual != expected:
            mismatches.append({"city": city, "aggregate": expected, "embedded": actual})
    return mismatches


def validate_data(aggregate: dict, state: dict, *, html_path: Path, raw_dir: Path, fail_on_warnings: bool = False) -> dict:
    errors = []
    warnings = list(iter_warnings(aggregate))
    candidate_exclusions = list(iter_candidate_exclusions(aggregate))
    cities = aggregate.get("cities", {})
    if not isinstance(cities, dict):
        errors.append("aggregate is missing a cities object")
        cities = {}

    failed_cities = state.get("failed_cities", {})
    if not isinstance(failed_cities, dict):
        errors.append("state has invalid failed_cities metadata")
        failed_cities = {}
    elif failed_cities:
        errors.append(f"state has failed cities: {json.dumps(failed_cities, ensure_ascii=False)}")

    unmatched_raw_files = aggregate.get("unmatched_raw_files", [])
    if not isinstance(unmatched_raw_files, list):
        errors.append("aggregate has invalid unmatched_raw_files metadata")
        unmatched_raw_files = []
    elif unmatched_raw_files:
        errors.append(f"aggregate has unmatched raw files: {json.dumps(unmatched_raw_files, ensure_ascii=False)}")

    coverage = aggregate.get("coverage", {})
    if not isinstance(coverage, dict):
        coverage = {}
    actual_raw_files = raw_file_count(raw_dir)
    expected_raw_files = coverage.get("raw_files_found")
    if expected_raw_files != actual_raw_files:
        errors.append(f"raw file count mismatch: aggregate={expected_raw_files} actual={actual_raw_files}")

    try:
        mismatches = count_mismatches(aggregate, html_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        mismatches = []
        errors.append(str(exc))
    if mismatches:
        preview = ", ".join(
            f"{item['city']} aggregate={item['aggregate']} embedded={item['embedded']}"
            for item in mismatches[:20]
        )
        errors.append(f"embedded ad count mismatches: {preview}")

    if fail_on_warnings and warnings:
        errors.append(f"portal warnings present: {len(warnings)}")

    return {
        "ok": not errors,
        "errors": errors,
        "totals": aggregate_totals(aggregate),
        "coverage": coverage,
        "state": {
            "status": state.get("status"),
            "failed_cities": failed_cities,
        },
        "raw_files": {
            "aggregate": expected_raw_files,
            "actual": actual_raw_files,
            "unmatched": unmatched_raw_files,
        },
        "embedded_count_mismatches": mismatches,
        "portal_warnings": {
            "count": len(warnings),
            "by_status": count_by_key(warnings, "status"),
            "by_portal": count_by_key(warnings, "portal"),
        },
        "candidate_exclusions": {
            "count": len(candidate_exclusions),
            "by_status": count_by_key(candidate_exclusions, "status"),
            "by_portal": count_by_key(candidate_exclusions, "portal"),
        },
    }


def count_by_key(rows: list[dict], key: str) -> dict[str, int]:
    counts = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def render_text_report(report: dict) -> str:
    totals = report["totals"]
    raw_files = report["raw_files"]
    lines = [
        f"data health: {'ok' if report['ok'] else 'failed'}",
        f"aggregate: cities={totals['cities']} active={totals['active']} hidden={totals['hidden']} cities_with_ads={totals['cities_with_ads']}",
        f"raw files: aggregate={raw_files['aggregate']} actual={raw_files['actual']} unmatched={len(raw_files['unmatched'])}",
        f"state: {report['state']['status']} failed={len(report['state']['failed_cities'])}",
        f"embedded counts: mismatches={len(report['embedded_count_mismatches'])}",
        f"portal warnings: {report['portal_warnings']['count']} by_status={format_counts(report['portal_warnings']['by_status'])}",
        f"candidate exclusions: {report['candidate_exclusions']['count']} by_status={format_counts(report['candidate_exclusions']['by_status'])}",
    ]
    for error in report["errors"]:
        lines.append(f"error: {error}")
    return "\n".join(lines)


def refresh_summary_from_report(report: dict, generated_at: str | None) -> dict:
    return {
        "generated_at": generated_at,
        "did_refresh": False,
        "totals": report["totals"],
        "coverage": report["coverage"],
        "refreshed_cities": [],
        "failed_cities": report["state"]["failed_cities"],
        "unmatched_raw_files": report["raw_files"]["unmatched"],
        "portal_warnings": report["portal_warnings"],
        "candidate_exclusions": report["candidate_exclusions"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate checked-in real-estate aggregate, raw metadata, state, and HTML data.")
    parser.add_argument("--aggregate", default=str(DEFAULT_AGGREGATE_PATH), help="Path to real_estate_ads_by_city.json.")
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH), help="Path to real_estate_ads_run_state.json.")
    parser.add_argument("--html", default=str(DEFAULT_HTML_PATH), help="Path to generated index.html.")
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR), help="Directory with per-city raw JSON files.")
    parser.add_argument("--json", action="store_true", help="Emit the validation report as JSON.")
    parser.add_argument("--fail-on-warnings", action="store_true", help="Fail when portal health warnings are present.")
    parser.add_argument("--summary-output", help="Optional path to write the compact Markdown summary.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    aggregate_path = Path(args.aggregate)
    aggregate = load_json(aggregate_path)
    state = load_json(Path(args.state))
    report = validate_data(
        aggregate,
        state,
        html_path=Path(args.html),
        raw_dir=Path(args.raw_dir),
        fail_on_warnings=args.fail_on_warnings,
    )
    if args.summary_output:
        summary_path = Path(args.summary_output)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            render_refresh_summary(refresh_summary_from_report(report, aggregate.get("generated_at"))),
            encoding="utf-8",
        )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text_report(report))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
