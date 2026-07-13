import json
import subprocess
import tempfile
from pathlib import Path

from .build_real_estate_ads_json import build_output


def cached_ads_for_prompt(previous_aggregate: dict | None, city: str) -> list[dict]:
    if not isinstance(previous_aggregate, dict):
        return []
    cities = previous_aggregate.get("cities", {})
    if not isinstance(cities, dict):
        return []
    bundle = cities.get(city, {})
    if not isinstance(bundle, dict):
        return []
    ads = bundle.get("ads", [])
    if not isinstance(ads, list):
        ads = []
    hidden_ads = bundle.get("hidden_ads", [])
    if not isinstance(hidden_ads, list):
        hidden_ads = []
    out = []
    for status, ad in [*[("active", ad) for ad in ads], *[("hidden", ad) for ad in hidden_ads]]:
        if not isinstance(ad, dict):
            continue
        out.append(
            {
                "status": ad.get("status") or status,
                "title": ad.get("title"),
                "location": ad.get("location"),
                "property_type": ad.get("property_type"),
                "price": ad.get("price"),
                "house_area_m2": ad.get("house_area_m2"),
                "land_area_m2": ad.get("land_area_m2"),
                "urls": ad.get("urls", []),
            }
        )
    return out


def build_prompt(city: str, cached_ads: list[dict] | None = None) -> str:
    cached_ads = cached_ads or []
    cache_block = ""
    if cached_ads:
        cache_block = f"""
Known ads from the previous local aggregate are provided below. Treat them as a cache, not as proof that the ad is still active. Rows with `status: hidden` were previously seen but missing from the latest successful refresh.

Use this cache to avoid unnecessary detail lookups for ads whose current search result still exposes the same URL/title/location/price/areas. Still run current municipality-level searches so new ads can be discovered and missing cached ads can be omitted from the latest `listings` array.

Previous ads:
{json.dumps(cached_ads, ensure_ascii=False, indent=2)}
"""

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
- include `portal_status` for each checked supported portal so fetch problems are machine-readable; do not hide 429/rate-limit, DNS, timeout, cache-miss, inactive, or fallback-page evidence only in `gaps`
- include `fetch_attempts` for direct portal/search/detail fetches where available, especially every retry or failed detail fetch

Set:
- `city` to `{city}`
- `query.municipality` to `{city}`
- `query.location_scope` to `municipality_only`
- `query.country` to `Czech Republic`
- `query.property_types` to [\"house\", \"chalupa\", \"land\"]
- `query.land_size_min_m2` to 1000

For `portal_status`, use portal domains as keys and objects with:
- include all four supported portal keys: `reality.idnes.cz`, `realitymix.cz`, `reality.aktualne.cz`, and `sreality.cz`
- `status`: one of `ok`, `no_results`, `rate_limited`, `fetch_error`, `dns_error`, `timeout`, `blocked`, `inactive`, `fallback_page`, `partial`, or `unknown`
- `http_status`: include numeric HTTP code when known, e.g. `429`
- `stage`: where it happened, e.g. `search_fetch`, `detail_fetch`, `helper_fetch`, or `browser_open`
- `retained_from_snapshot`: true when a row was kept from a listing/search/indexed snapshot because detail fetch failed
- `message` and `evidence`: concise human-readable detail
- include every key; use null for unknown `http_status`, `stage`, or `retained_from_snapshot`, an empty string for `message`, and an empty array for `evidence`

For `fetch_attempts`, include objects with:
- `portal`, `url`, `stage`, `attempt`, and `status`
- `http_status` when known
- `error` or `message` when the attempt failed or fell back
- include every key; use null for unknown `http_status`, `error`, or `message`
{cache_block}
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


def run_city(
    city: str,
    repo_root: Path,
    schema_path: Path,
    output_path: Path,
    codex_bin: str,
    model: str | None,
    cached_ads: list[dict] | None = None,
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
        build_prompt(city, cached_ads=cached_ads),
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
