import argparse
import html
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from .run_real_estate_ads_by_city import city_refresh_summary, format_delta
from .summarize_real_estate_fetch_errors import iter_candidate_exclusions, iter_warnings

ROOT = Path(__file__).resolve().parent.parent
AGGREGATE_PATH = ROOT / "real_estate_ads_by_city.json"
STATE_PATH = ROOT / "real_estate_ads_run_state.json"
HTML_PATH = ROOT / "index.html"
DEFAULT_SUMMARY_PATH = ROOT / "real_estate_refresh_summary.md"


def run_command(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(args), flush=True)
    return subprocess.run(args, cwd=ROOT, check=check, text=True)


def capture_command(args: list[str], *, check: bool = True) -> str:
    print("+", " ".join(args), flush=True)
    completed = subprocess.run(args, cwd=ROOT, check=check, text=True, capture_output=True)
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    return completed.stdout


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def load_json_if_exists(path: Path) -> dict | None:
    if not path.exists():
        return None
    return load_json(path)


def load_city_order() -> list[str]:
    rows = json.loads((ROOT / "dobruska_primary_schools.json").read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("dobruska_primary_schools.json must contain a JSON array.")
    cities = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        city = str(row.get("city", "")).strip()
        if city and city not in seen:
            cities.append(city)
            seen.add(city)
    return cities


def daily_refresh_stamps(state: dict | None) -> dict[str, str]:
    if not isinstance(state, dict):
        return {}
    daily_refresh = state.get("daily_refresh", {})
    if not isinstance(daily_refresh, dict):
        return {}
    cities = daily_refresh.get("cities", {})
    if not isinstance(cities, dict):
        return {}
    stamps = {}
    for city, city_state in cities.items():
        if isinstance(city_state, dict):
            stamps[str(city)] = str(city_state.get("last_completed_at", ""))
    return stamps


def refreshed_city_names(previous_state: dict | None, current_state: dict) -> list[str]:
    before = daily_refresh_stamps(previous_state)
    after = daily_refresh_stamps(current_state)
    refreshed = [city for city, stamp in after.items() if stamp and before.get(city) != stamp]
    order = {city: index for index, city in enumerate(load_city_order())}
    return sorted(refreshed, key=lambda city: order.get(city, len(order)))


def print_city_summaries(previous_aggregate: dict | None, current_aggregate: dict, cities: list[str]) -> None:
    if not cities:
        print("city summaries: no cities refreshed in this run")
        return
    print(f"city summaries: {len(cities)} refreshed")
    for city in cities:
        summary = city_refresh_summary(current_aggregate, previous_aggregate, city)
        print(
            f"  {city}: "
            f"active={summary['active']} ({format_delta(summary['active_delta'])}) "
            f"hidden={summary['hidden']} ({format_delta(summary['hidden_delta'])}) "
            f"new={summary['new']} "
            f"price_changed={summary['price_changed']}"
        )


def city_summaries(previous_aggregate: dict | None, current_aggregate: dict, cities: list[str]) -> list[dict]:
    return [
        {"city": city, **city_refresh_summary(current_aggregate, previous_aggregate, city)}
        for city in cities
    ]


def validate_state() -> None:
    state = load_json(STATE_PATH)
    failed_cities = state.get("failed_cities", {})
    if failed_cities:
        raise RuntimeError(f"real estate refresh has failed cities: {json.dumps(failed_cities, ensure_ascii=False)}")
    print(f"state: {state.get('status')} failed=0")


def validate_no_unmatched_raw_files(aggregate: dict) -> None:
    unmatched = aggregate.get("unmatched_raw_files", [])
    if not isinstance(unmatched, list):
        raise RuntimeError("real_estate_ads_by_city.json has invalid unmatched_raw_files metadata.")
    if not unmatched:
        print("raw files: no unmatched files")
        return

    formatted = []
    for item in unmatched[:20]:
        if isinstance(item, dict):
            formatted.append(f"{item.get('file', 'unknown')}: {item.get('city', 'unknown')}")
        else:
            formatted.append(str(item))
    if len(unmatched) > 20:
        formatted.append(f"... {len(unmatched) - 20} more")
    raise RuntimeError("real estate aggregate has unmatched raw files:\n" + "\n".join(formatted))


def validate_embedded_counts() -> None:
    aggregate = load_json(AGGREGATE_PATH)
    page = HTML_PATH.read_text(encoding="utf-8")
    match = re.search(r'<script id="ads-by-city-data" type="application/json">(.*?)</script>', page, re.S)
    if not match:
        raise RuntimeError("index.html is missing ads-by-city-data.")
    embedded = json.loads(html.unescape(match.group(1)))

    mismatches = []
    cities = aggregate.get("cities", {})
    if not isinstance(cities, dict):
        raise RuntimeError("real_estate_ads_by_city.json is missing a cities object.")
    for city, bundle in cities.items():
        if not isinstance(bundle, dict):
            continue
        expected = len(bundle.get("ads", []))
        actual = embedded.get(city, {}).get("count")
        if actual != expected:
            mismatches.append((city, expected, actual))
    if mismatches:
        formatted = "\n".join(f"{city}: aggregate={expected} embedded={actual}" for city, expected, actual in mismatches[:20])
        raise RuntimeError(f"index.html ad count mismatches:\n{formatted}")

    active_total = sum(len(bundle.get("ads", [])) for bundle in cities.values() if isinstance(bundle, dict))
    hidden_total = sum(len(bundle.get("hidden_ads", [])) for bundle in cities.values() if isinstance(bundle, dict))
    print(f"embedded counts: ok cities={len(cities)} active={active_total} hidden={hidden_total}")


def summarize_warnings(max_warnings: int) -> None:
    aggregate = load_json(AGGREGATE_PATH)
    warnings = list(iter_warnings(aggregate))
    candidate_exclusions = list(iter_candidate_exclusions(aggregate))
    if not warnings:
        print("portal warnings: 0")
    else:
        by_status = Counter(str(warning["status"]) for warning in warnings)
        by_portal = Counter(str(warning["portal"]) for warning in warnings)
        print(f"portal warnings: {len(warnings)}")
        print("  by status:", ", ".join(f"{status}={count}" for status, count in sorted(by_status.items())))
        print("  by portal:", ", ".join(f"{portal}={count}" for portal, count in sorted(by_portal.items())))
        for warning in warnings[:max_warnings]:
            parts = [warning["city"], warning["portal"], warning["status"]]
            if warning.get("http_status") is not None:
                parts.append(f"HTTP {warning['http_status']}")
            print("  " + " | ".join(str(part) for part in parts))
            if warning.get("message"):
                print(f"    {warning['message']}")
        if len(warnings) > max_warnings:
            print(f"  ... {len(warnings) - max_warnings} more")

    if not candidate_exclusions:
        print("candidate exclusions: 0")
        return

    by_status = Counter(str(exclusion["status"]) for exclusion in candidate_exclusions)
    by_portal = Counter(str(exclusion["portal"]) for exclusion in candidate_exclusions)
    print(f"candidate exclusions: {len(candidate_exclusions)}")
    print("  by status:", ", ".join(f"{status}={count}" for status, count in sorted(by_status.items())))
    print("  by portal:", ", ".join(f"{portal}={count}" for portal, count in sorted(by_portal.items())))
    for exclusion in candidate_exclusions[:max_warnings]:
        parts = [exclusion["city"], exclusion["portal"], exclusion["status"]]
        if exclusion.get("http_status") is not None:
            parts.append(f"HTTP {exclusion['http_status']}")
        print("  " + " | ".join(str(part) for part in parts))
        if exclusion.get("message"):
            print(f"    {exclusion['message']}")
    if len(candidate_exclusions) > max_warnings:
        print(f"  ... {len(candidate_exclusions) - max_warnings} more")


def aggregate_totals(aggregate: dict) -> dict:
    cities = aggregate.get("cities", {})
    if not isinstance(cities, dict):
        cities = {}
    return {
        "cities": len(cities),
        "active": sum(len(bundle.get("ads", [])) for bundle in cities.values() if isinstance(bundle, dict)),
        "hidden": sum(len(bundle.get("hidden_ads", [])) for bundle in cities.values() if isinstance(bundle, dict)),
        "cities_with_ads": sum(1 for bundle in cities.values() if isinstance(bundle, dict) and bundle.get("ads")),
    }


def count_by_key(rows: list[dict], key: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get(key) or "unknown") for row in rows).items()))


def format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in counts.items())


def build_refresh_summary(
    previous_aggregate: dict | None,
    current_aggregate: dict,
    previous_state: dict | None,
    current_state: dict,
    *,
    did_refresh: bool,
) -> dict:
    refreshed_cities = refreshed_city_names(previous_state, current_state) if did_refresh else []
    warnings = list(iter_warnings(current_aggregate))
    candidate_exclusions = list(iter_candidate_exclusions(current_aggregate))
    failed_cities = current_state.get("failed_cities", {})
    if not isinstance(failed_cities, dict):
        failed_cities = {}
    unmatched_raw_files = current_aggregate.get("unmatched_raw_files", [])
    if not isinstance(unmatched_raw_files, list):
        unmatched_raw_files = []

    return {
        "generated_at": current_aggregate.get("generated_at"),
        "did_refresh": did_refresh,
        "totals": aggregate_totals(current_aggregate),
        "coverage": current_aggregate.get("coverage", {}),
        "refreshed_cities": city_summaries(previous_aggregate, current_aggregate, refreshed_cities),
        "failed_cities": failed_cities,
        "unmatched_raw_files": unmatched_raw_files,
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


def render_refresh_summary(summary: dict) -> str:
    totals = summary["totals"]
    coverage = summary.get("coverage", {})
    lines = [
        "# Real Estate Refresh Summary",
        "",
        f"- Generated at: {summary.get('generated_at') or 'unknown'}",
        f"- Refresh executed: {'yes' if summary.get('did_refresh') else 'no'}",
        f"- Cities: {totals['cities']}",
        f"- Active ads: {totals['active']}",
        f"- Hidden ads: {totals['hidden']}",
        f"- Cities with ads: {totals['cities_with_ads']}",
        f"- Raw files: {coverage.get('raw_files_found', 'unknown')}",
        f"- Failed cities: {len(summary.get('failed_cities', {}))}",
        f"- Unmatched raw files: {len(summary.get('unmatched_raw_files', []))}",
        f"- Portal warnings: {summary['portal_warnings']['count']}",
        f"- Candidate exclusions: {summary['candidate_exclusions']['count']}",
        "",
    ]

    refreshed = summary.get("refreshed_cities", [])
    lines.append("## Refreshed Cities")
    if refreshed:
        lines.append("")
        lines.append("| City | Active | Hidden | New | Price changes |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for row in refreshed:
            lines.append(
                f"| {row['city']} | "
                f"{row['active']} ({format_delta(row['active_delta'])}) | "
                f"{row['hidden']} ({format_delta(row['hidden_delta'])}) | "
                f"{row['new']} | "
                f"{row['price_changed']} |"
            )
    else:
        lines.append("")
        lines.append("No cities were refreshed in this run.")

    lines.append("")
    lines.append("## Portal Warnings")
    if summary["portal_warnings"]["count"]:
        lines.append("")
        lines.append(f"- By status: {format_counts(summary['portal_warnings']['by_status'])}")
        lines.append(f"- By portal: {format_counts(summary['portal_warnings']['by_portal'])}")
    else:
        lines.append("")
        lines.append("None.")

    lines.append("")
    lines.append("## Candidate Exclusions")
    if summary["candidate_exclusions"]["count"]:
        lines.append("")
        lines.append(f"- By status: {format_counts(summary['candidate_exclusions']['by_status'])}")
        lines.append(f"- By portal: {format_counts(summary['candidate_exclusions']['by_portal'])}")
    else:
        lines.append("")
        lines.append("None.")

    lines.append("")
    return "\n".join(lines)


def write_refresh_summary(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_refresh_summary(summary), encoding="utf-8")
    print(f"summary: wrote {path}")


def git_has_changes() -> bool:
    return bool(capture_command(["git", "status", "--porcelain"]).strip())


def commit_and_push(message: str, push: bool) -> None:
    if not git_has_changes():
        print("git: no changes to commit")
        return
    run_command(
        [
            "git",
            "add",
            "data/real_estate_ads_raw",
            "real_estate_ads_by_city.json",
            "real_estate_ads_run_state.json",
            "index.html",
        ]
    )
    if not capture_command(["git", "diff", "--cached", "--name-only"]).strip():
        print("git: no generated refresh changes staged")
        return
    run_command(["git", "commit", "-m", message])
    if push:
        run_command(["git", "push"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the daily real-estate refresh workflow end to end.")
    parser.add_argument("--limit", type=int, help="Refresh only the next N pending municipalities.")
    parser.add_argument("--force-daily-refresh", action="store_true", help="Re-run cities already refreshed today.")
    parser.add_argument("--local-only", action="store_true", help="Use local fetchers only; do not fall back to Codex.")
    parser.add_argument("--skip-refresh", action="store_true", help="Skip fetching and only rebuild/validate current data.")
    parser.add_argument("--skip-tests", action="store_true", help="Skip pytest, ruff, and browser smoke checks.")
    parser.add_argument("--skip-browser", action="store_true", help="Skip the Playwright drawer smoke check.")
    parser.add_argument("--commit", action="store_true", help="Commit generated refresh artifacts after validation.")
    parser.add_argument("--push", action="store_true", help="Commit and push generated refresh artifacts after validation.")
    parser.add_argument("--commit-message", default="Refresh real estate ads", help="Commit message for --commit/--push.")
    parser.add_argument("--max-warnings", type=int, default=20, help="Maximum portal warnings to print.")
    parser.add_argument("--summary-output", default=str(DEFAULT_SUMMARY_PATH), help="Path to write the compact refresh summary Markdown artifact.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    python = sys.executable
    previous_aggregate = load_json_if_exists(AGGREGATE_PATH)
    previous_state = load_json_if_exists(STATE_PATH)
    did_refresh = False

    if not args.skip_refresh:
        refresh_cmd = [
            python,
            "run_real_estate_ads_by_city.py",
            "--daily-refresh",
            "--aggregate-after-each",
        ]
        refresh_cmd.append("--local-only" if args.local_only else "--local-first")
        if args.limit is not None:
            refresh_cmd.extend(["--limit", str(args.limit)])
        if args.force_daily_refresh:
            refresh_cmd.append("--force-daily-refresh")
        run_command(refresh_cmd)
        did_refresh = True

    run_command([python, "build_html.py", "--ads-only"])
    current_state = load_json(STATE_PATH)
    current_aggregate = load_json(AGGREGATE_PATH)
    if did_refresh:
        print_city_summaries(previous_aggregate, current_aggregate, refreshed_city_names(previous_state, current_state))
    validate_no_unmatched_raw_files(current_aggregate)
    validate_state()
    validate_embedded_counts()
    summarize_warnings(args.max_warnings)
    summary = build_refresh_summary(
        previous_aggregate,
        current_aggregate,
        previous_state,
        current_state,
        did_refresh=did_refresh,
    )
    write_refresh_summary(Path(args.summary_output), summary)

    if not args.skip_tests:
        run_command([python, "-m", "pytest", "-q"])
        run_command([python, "-m", "ruff", "check", "."])
        if not args.skip_browser:
            run_command([python, "tests/test_drawer_ui.py"])
    run_command(["git", "diff", "--check"])

    if args.commit or args.push:
        commit_and_push(args.commit_message, push=args.push)


if __name__ == "__main__":
    main()
