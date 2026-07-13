import argparse
import signal
import sys
import time
from pathlib import Path

from .ads_codex_runner import cached_ads_for_prompt, run_city, should_skip_city
from .ads_local_fetch import (
    REALITY_IDNES_PORTAL,
    SUPPORTED_PORTALS,
    LocalFetcherBlockedError,
    ProviderCircuitBreaker,
    run_local_fetchers,
)
from .ads_refresh_pipeline import (
    aggregate_outputs,
    load_previous_aggregate,
    print_city_refresh_summary,
)
from .ads_state import (
    daily_refresh_city_completed_today,
    load_state,
    record_daily_refresh_city_completion,
    save_state,
    select_cities,
    slugify_city,
)
from .build_real_estate_ads_by_city import load_school_cities
from .paths import (
    REAL_ESTATE_ADS_BY_CITY_PATH,
    REAL_ESTATE_EXEC_SCHEMA_PATH,
    REAL_ESTATE_RAW_DIR,
    REAL_ESTATE_RUN_STATE_PATH,
    SCHOOLS_JSON_PATH,
)

DEFAULT_RAW_DIR = REAL_ESTATE_RAW_DIR
DEFAULT_STATE_PATH = REAL_ESTATE_RUN_STATE_PATH
DEFAULT_AGGREGATE_PATH = REAL_ESTATE_ADS_BY_CITY_PATH
DEFAULT_SCHEMA_PATH = REAL_ESTATE_EXEC_SCHEMA_PATH

STOP_REQUESTED = False


def request_stop(_signum, _frame) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the real estate ads skill for all school municipalities with resumable per-city outputs.")
    parser.add_argument("--schools-input", default=str(SCHOOLS_JSON_PATH), help="Path to the source city list JSON.")
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR), help="Directory for raw per-city skill outputs.")
    parser.add_argument("--aggregate-output", default=str(DEFAULT_AGGREGATE_PATH), help="Aggregate JSON output path.")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH), help="Path to the resumable run-state JSON file.")
    parser.add_argument("--schema-path", default=str(DEFAULT_SCHEMA_PATH), help="Path to the Codex exec output schema file.")
    parser.add_argument("--codex-bin", default="codex", help="Codex CLI binary to invoke.")
    parser.add_argument("--model", default=None, help="Optional model override passed to codex exec.")
    parser.add_argument("--limit", type=int, default=None, help="Optional maximum number of cities to process this run.")
    parser.add_argument("--city", help="Run only one municipality from the schools input.")
    parser.add_argument("--overwrite", action="store_true", help="Re-run even when a valid raw output file already exists.")
    parser.add_argument("--retry-failed", action="store_true", help="Retry cities recorded as failed in the state file.")
    parser.add_argument("--aggregate-after-each", action="store_true", help="Refresh the aggregate JSON after every successful city.")
    parser.add_argument(
        "--daily-refresh",
        action="store_true",
        help="Refresh every city, pass previous active ads as prompt cache, and hide ads missing from the latest snapshot.",
    )
    parser.add_argument(
        "--force-daily-refresh",
        action="store_true",
        help="Allow --daily-refresh to run even when a daily refresh already completed today.",
    )
    parser.add_argument(
        "--local-first",
        action="store_true",
        help="Try deterministic local cached-detail fetchers before falling back to Codex.",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Use only deterministic local cached-detail fetchers and fail cities that would need Codex fallback.",
    )
    parser.add_argument(
        "--local-portal",
        action="append",
        choices=SUPPORTED_PORTALS,
        help="Limit deterministic local fetchers to one portal. Repeat to include multiple portals.",
    )
    parser.add_argument(
        "--merge-local-results",
        action="store_true",
        help="Merge local fetcher output into an existing raw city file instead of replacing the full snapshot.",
    )
    args = parser.parse_args()

    repo_root = Path.cwd()
    schools_input = Path(args.schools_input)
    raw_dir = Path(args.raw_dir)
    aggregate_output = Path(args.aggregate_output)
    state_path = Path(args.state_path)
    schema_path = Path(args.schema_path)
    use_local_fetchers = args.local_first or args.local_only
    local_portals = set(args.local_portal) if args.local_portal else None
    if local_portals and not use_local_fetchers:
        parser.error("--local-portal requires --local-first or --local-only")
    if args.merge_local_results and not use_local_fetchers:
        parser.error("--merge-local-results requires --local-first or --local-only")
    needs_previous_aggregate = args.daily_refresh or use_local_fetchers
    previous_aggregate = load_previous_aggregate(aggregate_output) if needs_previous_aggregate and aggregate_output.exists() else None
    overwrite = args.overwrite or args.daily_refresh
    retry_failed = args.retry_failed or args.daily_refresh

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    state = load_state(state_path)
    all_cities = select_cities(load_school_cities(schools_input), args.city)
    failed_cities = state.get("failed_cities", {}) if isinstance(state.get("failed_cities"), dict) else {}
    completed_cities = []
    refreshed_cities = []
    pending_cities = []
    circuit_breaker = ProviderCircuitBreaker()

    for city in all_cities:
        output_path = raw_dir / f"{slugify_city(city)}.json"
        if args.daily_refresh and not args.force_daily_refresh and daily_refresh_city_completed_today(state, city):
            completed_cities.append(city)
            failed_cities.pop(city, None)
            continue
        if should_skip_city(city, output_path, overwrite):
            completed_cities.append(city)
            failed_cities.pop(city, None)
            continue
        if city in failed_cities and not retry_failed:
            continue
        pending_cities.append(city)

    if args.limit is not None:
        pending_cities = pending_cities[: args.limit]

    save_state(
        state_path,
        state,
        schools_input,
        raw_dir,
        aggregate_output,
        completed_cities,
        failed_cities,
        None,
        pending_cities,
        "running",
    )

    try:
        for index, city in enumerate(pending_cities):
            if STOP_REQUESTED:
                break
            remaining_cities = pending_cities[index + 1 :]
            save_state(
                state_path,
                state,
                schools_input,
                raw_dir,
                aggregate_output,
                completed_cities,
                failed_cities,
                city,
                [city] + remaining_cities,
                "running",
            )
            output_path = raw_dir / f"{slugify_city(city)}.json"
            print(f"[{index + 1}/{len(pending_cities)}] {city}", flush=True)
            try:
                used_local_fetchers = False
                if use_local_fetchers:
                    retry_local_portals = None
                    while True:
                        effective_local_portals = (
                            retry_local_portals
                            if retry_local_portals is not None
                            else circuit_breaker.effective_local_portals(local_portals)
                        )
                        try:
                            used_local_fetchers = run_local_fetchers(
                                city,
                                repo_root,
                                output_path,
                                previous_aggregate,
                                local_portals=effective_local_portals,
                                merge_local_results=args.merge_local_results,
                            )
                            if REALITY_IDNES_PORTAL in effective_local_portals:
                                circuit_breaker.record_success(REALITY_IDNES_PORTAL)
                            if used_local_fetchers:
                                print(f"  used local cached-detail fetchers for {city}", flush=True)
                            break
                        except LocalFetcherBlockedError as local_exc:
                            disabled_now = circuit_breaker.record_failure(local_exc)
                            can_retry_without_portal = local_exc.portal == REALITY_IDNES_PORTAL and len(effective_local_portals) > 1
                            if can_retry_without_portal:
                                retry_local_portals = set(effective_local_portals) - {local_exc.portal}
                                if disabled_now:
                                    print(
                                        f"  disabled {local_exc.portal} for rest of run after "
                                        f"{circuit_breaker.threshold} consecutive failures; retrying {city} without it",
                                        flush=True,
                                    )
                                else:
                                    consecutive_failures = circuit_breaker.consecutive_failures.get(local_exc.portal, 0)
                                    print(
                                        f"  {local_exc.portal} failed "
                                        f"({consecutive_failures}/{circuit_breaker.threshold}); "
                                        f"retrying {city} without it",
                                        flush=True,
                                    )
                                continue
                            if args.local_only:
                                raise RuntimeError(f"local fetchers failed for {city}: {local_exc}") from local_exc
                            print(f"  local fetchers failed for {city}: {local_exc}; falling back to Codex", flush=True)
                            break
                        except Exception as local_exc:
                            if args.local_only:
                                raise RuntimeError(f"local fetchers failed for {city}: {local_exc}") from local_exc
                            print(f"  local fetchers failed for {city}: {local_exc}; falling back to Codex", flush=True)
                            break
                if not used_local_fetchers:
                    if args.local_only:
                        raise RuntimeError(f"local-only requested but no cached detail URLs were verified for {city}")
                    run_city(
                        city,
                        repo_root,
                        schema_path,
                        output_path,
                        args.codex_bin,
                        args.model,
                        cached_ads=cached_ads_for_prompt(previous_aggregate, city),
                    )
                completed_cities.append(city)
                refreshed_cities.append(city)
                failed_cities.pop(city, None)
                if args.daily_refresh:
                    record_daily_refresh_city_completion(state_path, state, city=city)
                if args.aggregate_after_each:
                    aggregate_outputs(schools_input, raw_dir, aggregate_output, previous_aggregate=previous_aggregate)
                    print_city_refresh_summary(aggregate_output, previous_aggregate, city)
            except Exception as exc:
                failed_cities[city] = {
                    "failed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "error": str(exc),
                }
                print(f"FAILED {city}: {exc}", file=sys.stderr, flush=True)
            finally:
                save_state(
                    state_path,
                    state,
                    schools_input,
                    raw_dir,
                    aggregate_output,
                    completed_cities,
                    failed_cities,
                    None,
                    remaining_cities,
                    "running" if not STOP_REQUESTED else "interrupted",
                )
        aggregate_outputs(schools_input, raw_dir, aggregate_output, previous_aggregate=previous_aggregate)
        if not args.aggregate_after_each:
            for city in refreshed_cities:
                print_city_refresh_summary(aggregate_output, previous_aggregate, city)
    finally:
        final_status = "completed"
        if STOP_REQUESTED:
            final_status = "interrupted"
        elif failed_cities:
            final_status = "completed-with-failures"
        remaining_cities = [city for city in all_cities if city not in completed_cities and city not in failed_cities]
        save_state(
            state_path,
            state,
            schools_input,
            raw_dir,
            aggregate_output,
            completed_cities,
            failed_cities,
            None,
            remaining_cities,
            final_status,
        )


if __name__ == "__main__":
    main()
