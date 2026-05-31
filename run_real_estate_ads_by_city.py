import argparse
import json
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from build_real_estate_ads_by_city import build_aggregate_output, load_school_cities
from build_real_estate_ads_json import build_output

DEFAULT_RAW_DIR = Path("data/real_estate_ads_raw")
DEFAULT_STATE_PATH = Path("real_estate_ads_run_state.json")
DEFAULT_AGGREGATE_PATH = Path("real_estate_ads_by_city.json")
DEFAULT_SCHEMA_PATH = Path("real_estate_ads_exec_output.schema.json")

STOP_REQUESTED = False


def request_stop(_signum, _frame) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def slugify_city(city: str) -> str:
    slug = city.strip().lower()
    replacements = {
        "á": "a", "ä": "a", "č": "c", "ď": "d", "é": "e", "ě": "e",
        "í": "i", "ň": "n", "ó": "o", "ř": "r", "š": "s", "ť": "t",
        "ú": "u", "ů": "u", "ý": "y", "ž": "z",
    }
    for src, dst in replacements.items():
        slug = slug.replace(src, dst)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-") or "city"


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "updated_at": None,
            "status": "idle",
            "schools_input": None,
            "raw_dir": None,
            "aggregate_output": None,
            "completed_cities": [],
            "failed_cities": {},
            "last_completed_city": None,
            "current_city": None,
            "remaining_cities": []
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(
    path: Path,
    state: dict,
    schools_input: Path,
    raw_dir: Path,
    aggregate_output: Path,
    completed_cities: list[str],
    failed_cities: dict,
    current_city: str | None,
    remaining_cities: list[str],
    status: str,
) -> None:
    state.update(
        {
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "status": status,
            "schools_input": str(schools_input),
            "raw_dir": str(raw_dir),
            "aggregate_output": str(aggregate_output),
            "completed_cities": completed_cities,
            "failed_cities": failed_cities,
            "last_completed_city": completed_cities[-1] if completed_cities else None,
            "current_city": current_city,
            "remaining_cities": remaining_cities,
        }
    )
    atomic_write_json(path, state)


def build_prompt(city: str) -> str:
    return f"""Use the `find-real-estate-ads` skill.

Search only sale listings in the Czech municipality `{city}`.

Return only JSON matching the provided output schema. Do not return markdown. Do not wrap the JSON in code fences.

Requirements:
- country: Czech Republic
- location_scope: municipality_only
- property_types: house, chalupa, land
- land_size_min_m2: 1000
- exclude chata
- keep only buildable or residential land
- use the skill's deduplication and ranking rules
- if no credible listings are found, still return a valid JSON object with an empty `listings` array and explain any coverage/gaps in `gaps`
- do not spawn sub-agents in this run; use the skill's documented single-agent fallback while preserving the same portal-by-portal discipline

Set:
- `city` to `{city}`
- `query.municipality` to `{city}`
- `query.location_scope` to `municipality_only`
- `query.country` to `Czech Republic`
- `query.property_types` to [\"house\", \"chalupa\", \"land\"]
- `query.land_size_min_m2` to 1000
"""


def validate_raw_output(payload: dict, city: str) -> None:
    if not isinstance(payload, dict):
        raise ValueError("raw output must be a JSON object")
    payload_city = str(payload.get("city", "")).strip()
    if not payload_city:
        query = payload.get("query", {})
        if isinstance(query, dict):
            payload_city = str(query.get("municipality", "")).strip()
    if payload_city != city:
        raise ValueError(f"raw output city mismatch: expected {city!r}, got {payload_city!r}")
    build_output(payload)


def aggregate_outputs(schools_input: Path, raw_dir: Path, aggregate_output: Path) -> None:
    payload = build_aggregate_output(schools_input, raw_dir)
    atomic_write_json(aggregate_output, payload)


def run_city(
    city: str,
    repo_root: Path,
    schema_path: Path,
    output_path: Path,
    codex_bin: str,
    model: str | None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output_path.parent, delete=False, suffix=".json") as handle:
        temp_output_path = Path(handle.name)
    cmd = [
        codex_bin,
        "--search",
        "exec",
        "--color",
        "never",
        "-C",
        str(repo_root),
        "--output-schema",
        str(schema_path),
        "-o",
        str(temp_output_path),
        build_prompt(city),
    ]
    if model:
        cmd[2:2] = ["--model", model]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        payload = json.loads(temp_output_path.read_text(encoding="utf-8"))
        validate_raw_output(payload, city)
        temp_output_path.replace(output_path)
    except subprocess.CalledProcessError as exc:
        if temp_output_path.exists():
            temp_output_path.unlink()
        details = []
        if exc.stdout and exc.stdout.strip():
            details.append(exc.stdout.strip())
        if exc.stderr and exc.stderr.strip():
            details.append(exc.stderr.strip())
        if details:
            raise RuntimeError("\n".join(details)) from exc
        raise
    except Exception:
        if temp_output_path.exists():
            temp_output_path.unlink()
        raise


def should_skip_city(city: str, output_path: Path, overwrite: bool) -> bool:
    if overwrite or not output_path.exists():
        return False
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        validate_raw_output(payload, city)
        return True
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the real estate ads skill for all school municipalities with resumable per-city outputs.")
    parser.add_argument("--schools-input", default="dobruska_primary_schools.json", help="Path to the source city list JSON.")
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR), help="Directory for raw per-city skill outputs.")
    parser.add_argument("--aggregate-output", default=str(DEFAULT_AGGREGATE_PATH), help="Aggregate JSON output path.")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH), help="Path to the resumable run-state JSON file.")
    parser.add_argument("--schema-path", default=str(DEFAULT_SCHEMA_PATH), help="Path to the Codex exec output schema file.")
    parser.add_argument("--codex-bin", default="codex", help="Codex CLI binary to invoke.")
    parser.add_argument("--model", default=None, help="Optional model override passed to codex exec.")
    parser.add_argument("--limit", type=int, default=None, help="Optional maximum number of cities to process this run.")
    parser.add_argument("--overwrite", action="store_true", help="Re-run even when a valid raw output file already exists.")
    parser.add_argument("--retry-failed", action="store_true", help="Retry cities recorded as failed in the state file.")
    parser.add_argument("--aggregate-after-each", action="store_true", help="Refresh the aggregate JSON after every successful city.")
    args = parser.parse_args()

    repo_root = Path.cwd()
    schools_input = Path(args.schools_input)
    raw_dir = Path(args.raw_dir)
    aggregate_output = Path(args.aggregate_output)
    state_path = Path(args.state_path)
    schema_path = Path(args.schema_path)

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    state = load_state(state_path)
    all_cities = load_school_cities(schools_input)
    failed_cities = state.get("failed_cities", {}) if isinstance(state.get("failed_cities"), dict) else {}
    completed_cities = []
    pending_cities = []

    for city in all_cities:
        output_path = raw_dir / f"{slugify_city(city)}.json"
        if should_skip_city(city, output_path, args.overwrite):
            completed_cities.append(city)
            failed_cities.pop(city, None)
            continue
        if city in failed_cities and not args.retry_failed:
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
                run_city(city, repo_root, schema_path, output_path, args.codex_bin, args.model)
                completed_cities.append(city)
                failed_cities.pop(city, None)
                if args.aggregate_after_each:
                    aggregate_outputs(schools_input, raw_dir, aggregate_output)
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
        aggregate_outputs(schools_input, raw_dir, aggregate_output)
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
