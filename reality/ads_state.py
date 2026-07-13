import json
import re
import tempfile
import time
from pathlib import Path


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
            "remaining_cities": [],
            "daily_refresh": {},
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


def today_string() -> str:
    return time.strftime("%Y-%m-%d")


def daily_refresh_city_completed_today(state: dict, city: str, today: str | None = None) -> bool:
    today = today or today_string()
    daily_refresh = state.get("daily_refresh", {})
    if not isinstance(daily_refresh, dict):
        return False
    cities = daily_refresh.get("cities", {})
    if not isinstance(cities, dict):
        return False
    city_state = cities.get(city, {})
    if not isinstance(city_state, dict):
        return False
    return city_state.get("last_completed_on") == today


def select_cities(all_cities: list[str], city: str | None = None) -> list[str]:
    if city is None:
        return all_cities
    requested = city.strip()
    for known_city in all_cities:
        if known_city == requested:
            return [known_city]
    requested_slug = slugify_city(requested)
    matches = [known_city for known_city in all_cities if slugify_city(known_city) == requested_slug]
    if not matches:
        raise ValueError(f"unknown city: {city}")
    if len(matches) > 1:
        raise ValueError(f"city is ambiguous: {city}")
    return matches


def record_daily_refresh_city_completion(
    path: Path,
    state: dict,
    *,
    city: str,
) -> None:
    daily_refresh = state.get("daily_refresh", {})
    if not isinstance(daily_refresh, dict):
        daily_refresh = {}
    cities = daily_refresh.get("cities", {})
    if not isinstance(cities, dict):
        cities = {}
    cities[city] = {
        "last_completed_on": today_string(),
        "last_completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    daily_refresh["cities"] = cities
    state["daily_refresh"] = daily_refresh
    atomic_write_json(path, state)
