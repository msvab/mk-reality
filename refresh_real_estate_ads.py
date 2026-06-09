import argparse
import html
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from summarize_real_estate_fetch_errors import iter_warnings

ROOT = Path(__file__).resolve().parent
AGGREGATE_PATH = ROOT / "real_estate_ads_by_city.json"
STATE_PATH = ROOT / "real_estate_ads_run_state.json"
HTML_PATH = ROOT / "index.html"


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


def validate_state() -> None:
    state = load_json(STATE_PATH)
    failed_cities = state.get("failed_cities", {})
    if failed_cities:
        raise RuntimeError(f"real estate refresh has failed cities: {json.dumps(failed_cities, ensure_ascii=False)}")
    print(f"state: {state.get('status')} failed=0")


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
    warnings = list(iter_warnings(load_json(AGGREGATE_PATH)))
    if not warnings:
        print("portal warnings: 0")
        return

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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    python = sys.executable

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

    run_command([python, "build_html.py", "--ads-only"])
    validate_state()
    validate_embedded_counts()
    summarize_warnings(args.max_warnings)

    if not args.skip_tests:
        run_command([python, "-m", "pytest", "-q"])
        run_command([python, "-m", "ruff", "check", "."])
        if not args.skip_browser:
            run_command([python, "test_drawer_ui.py"])
    run_command(["git", "diff", "--check"])

    if args.commit or args.push:
        commit_and_push(args.commit_message, push=args.push)


if __name__ == "__main__":
    main()
