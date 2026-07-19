from __future__ import annotations

import argparse
import atexit
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .build_html_urls import is_usable_school_url, normalize_url
from .school_normalization import (
    amenity_bucket,
    amenity_city_key,
    haversine_km,
    infer_school_type,
    is_generic_primary_school_name,
    is_selected_school_malotridka,
    looks_kindergarten_hint,
    looks_primary_school,
)
from .school_sources import (
    DOBRUSKA,
    MAX_DRIVE_SEC,
    fetch_mapotic_malotridky,
    find_school_website,
    find_school_website_by_city,
    infer_type_from_website,
    load_cache,
    load_malotridky_cache,
    load_overpass_inputs,
    load_registry_cache,
    load_type_cache,
    manual_city_school_url,
    osrm_duration_sec,
    registry_city_has_kindergarten,
    registry_school_website,
    registry_type_for_city_primary,
    registry_type_for_school,
    save_cache,
    save_malotridky_cache,
    save_registry_cache,
    save_type_cache,
)


def build_school_rows(args: argparse.Namespace) -> list[dict]:
    places, schools, amenities = load_overpass_inputs(args)

    municipalities = []
    for el in places.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        if "lat" in el and "lon" in el:
            lat = el["lat"]
            lon = el["lon"]
        elif "center" in el:
            lat = el["center"]["lat"]
            lon = el["center"]["lon"]
        else:
            continue
        pop = tags.get("population")
        pop_num = None
        if pop:
            digits = "".join(ch for ch in pop if ch.isdigit())
            if digits:
                pop_num = int(digits)
        municipalities.append({
            "name": name,
            "lat": lat,
            "lon": lon,
            "population": pop_num,
            "schools": [],
            "amenities": {
                "kindergarten": False,
                "cinema": False,
                "theatre": False,
            },
        })

    by_name = {}
    for m in municipalities:
        d = haversine_km(DOBRUSKA[0], DOBRUSKA[1], m["lat"], m["lon"])
        cur = by_name.get(m["name"])
        if cur is None or d < cur["_d"]:
            m["_d"] = d
            by_name[m["name"]] = m
    municipalities = list(by_name.values())

    school_points = []
    for el in schools.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("official_name") or tags.get("name:cs") or ""
        if "lat" in el and "lon" in el:
            lat, lon = el["lat"], el["lon"]
        elif "center" in el:
            lat, lon = el["center"]["lat"], el["center"]["lon"]
        else:
            continue
        website = normalize_url(tags.get("website") or tags.get("contact:website") or tags.get("url"))
        school_points.append({"name": name, "lat": lat, "lon": lon, "website": website, "tags": tags})

    for el in amenities.get("elements", []):
        tags = el.get("tags", {})
        bucket = amenity_bucket(tags.get("amenity", ""))
        if not bucket:
            continue
        if "lat" in el and "lon" in el:
            lat, lon = el["lat"], el["lon"]
        elif "center" in el:
            lat, lon = el["center"]["lat"], el["center"]["lon"]
        else:
            continue

        explicit_city = amenity_city_key(tags.get("addr:city") or tags.get("is_in:city") or "")
        if explicit_city:
            matched = False
            for m in municipalities:
                if amenity_city_key(m["name"]) == explicit_city:
                    m["amenities"][bucket] = True
                    matched = True
                    break
            if matched:
                continue

        nearest = None
        nearest_d = 999.0
        for m in municipalities:
            d = haversine_km(lat, lon, m["lat"], m["lon"])
            if d < nearest_d:
                nearest = m
                nearest_d = d
        if nearest is not None and nearest_d <= 2.5:
            nearest["amenities"][bucket] = True

    unnamed_school_points = []
    for s in school_points:
        if not looks_primary_school(s["tags"]):
            if not s["name"] and s["tags"].get("amenity") == "school":
                unnamed_school_points.append(s)
            continue
        nearest = None
        nearest_d = 999
        for m in municipalities:
            d = haversine_km(s["lat"], s["lon"], m["lat"], m["lon"])
            if d < nearest_d:
                nearest = m
                nearest_d = d
        if nearest is not None and nearest_d <= 6:
            nearest["schools"].append(s)
            if looks_kindergarten_hint(s["tags"], s["name"]):
                nearest["amenities"]["kindergarten"] = True

    for m in municipalities:
        if m["schools"]:
            continue
        nearest = None
        nearest_d = 999.0
        for s in unnamed_school_points:
            d = haversine_km(s["lat"], s["lon"], m["lat"], m["lon"])
            if d < nearest_d:
                nearest = s
                nearest_d = d
        if nearest is not None and nearest_d <= 1.0:
            m["schools"].append({
                "name": f"Základní škola ({m['name']})",
                "lat": nearest["lat"],
                "lon": nearest["lon"],
                "website": nearest.get("website"),
                "tags": nearest.get("tags", {}),
                "synthetic": True,
            })

    municipalities = [m for m in municipalities if m["schools"]]

    rows = []
    url_cache = load_cache()
    type_cache = load_type_cache()
    registry_cache = load_registry_cache()
    malotridky_cache = load_malotridky_cache()
    print(f"Loaded URL cache entries: {len(url_cache)}", flush=True)
    print(f"Loaded type cache entries: {len(type_cache)}", flush=True)
    print(f"Loaded registry cache entries: {len(registry_cache)}", flush=True)
    malotridky_points = fetch_mapotic_malotridky(malotridky_cache)
    print(f"Loaded malotridky points: {len(malotridky_points)}", flush=True)

    cache_io_lock = threading.Lock()

    def save_all_caches() -> None:
        with cache_io_lock:
            save_cache(url_cache)
            save_type_cache(type_cache)
            save_registry_cache(registry_cache)
            save_malotridky_cache(malotridky_cache)

    def _on_interrupt(signum, _frame):
        print(f"Received signal {signum}, saving caches...", flush=True)
        save_all_caches()
        raise KeyboardInterrupt

    atexit.register(save_all_caches)
    signal.signal(signal.SIGINT, _on_interrupt)
    signal.signal(signal.SIGTERM, _on_interrupt)

    try:
        url_cache_lock = cache_io_lock
        type_cache_lock = cache_io_lock
        registry_cache_lock = cache_io_lock

        def process_municipality(m: dict) -> dict | None:
            dur = osrm_duration_sec(m["lat"], m["lon"])
            time.sleep(0.08)
            if dur is None or dur > MAX_DRIVE_SEC:
                return None
            school = sorted(
                m["schools"],
                key=lambda x: (
                    1 if is_generic_primary_school_name(x["name"]) else 0,
                    0 if x.get("website") else 1,
                    haversine_km(m["lat"], m["lon"], x["lat"], x["lon"]),
                    x["name"],
                ),
            )[0]
            synthetic_school = bool(school.get("synthetic"))
            school_url = manual_city_school_url(m["name"]) or school.get("website")
            if not is_usable_school_url(school_url):
                school_url = None
            if not school_url:
                school_url = registry_school_website(
                    m["name"],
                    school["name"],
                    registry_cache,
                    registry_cache_lock,
                )
            if not school_url and not synthetic_school:
                school_url = find_school_website(school["name"], m["name"], url_cache, url_cache_lock)
            detected_type = infer_school_type(school["tags"], school["name"])
            if detected_type == "Neuvedeno":
                registry_name = "Základní škola" if synthetic_school else school["name"]
                detected_type = registry_type_for_school(
                    m["name"],
                    registry_name,
                    registry_cache,
                    registry_cache_lock,
                    force_refresh_if_unknown=synthetic_school,
                )
            if detected_type == "Neuvedeno" and synthetic_school:
                detected_type = registry_type_for_city_primary(m["name"], registry_cache, registry_cache_lock)
            if detected_type == "Neuvedeno" and school_url and not synthetic_school:
                detected_type = infer_type_from_website(
                    school_url, m["name"], school["name"], type_cache, type_cache_lock
                )
            if is_selected_school_malotridka(school, malotridky_points):
                detected_type = "Malotřídka"

            return {
                "city": m["name"],
                "lat": m["lat"],
                "lon": m["lon"],
                "population": m["population"],
                "drive_min": int(round(dur / 60)),
                "amenities": ", ".join(
                    x for x, ok in [
                        ("MŠ", m["amenities"]["kindergarten"]),
                        ("kino", m["amenities"]["cinema"]),
                        ("divadlo", m["amenities"]["theatre"]),
                    ] if ok
                ) or "—",
                "school_type": detected_type,
                "school_name": school["name"],
                "school_url": school_url,
            }

        max_workers = 10
        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(process_municipality, m) for m in municipalities]
            for fut in as_completed(futures):
                try:
                    row = fut.result()
                except Exception as e:
                    print(f"Worker failed: {e}", flush=True)
                    row = None
                completed += 1
                if row is not None:
                    rows.append(row)
                if completed % 10 == 0:
                    save_all_caches()
                if completed % 30 == 0:
                    print(f"processed {completed}/{len(municipalities)}", flush=True)
    except KeyboardInterrupt:
        print("Interrupted. Caches were saved.", flush=True)
        raise
    finally:
        save_all_caches()

    for r in rows:
        if r.get("school_type") != "Neuvedeno":
            continue
        if not str(r.get("school_name", "")).startswith("Základní škola ("):
            continue
        resolved = registry_type_for_city_primary(r["city"], registry_cache, registry_cache_lock)
        if resolved != "Neuvedeno":
            r["school_type"] = resolved

    for r in rows:
        if is_usable_school_url(r.get("school_url")):
            continue
        filled = manual_city_school_url(r["city"])
        synthetic_school = str(r.get("school_name", "")).startswith("Základní škola (")
        if not filled:
            filled = registry_school_website(r["city"], r["school_name"], registry_cache, registry_cache_lock)
        if not filled and not synthetic_school:
            filled = find_school_website_by_city(r["city"], url_cache, url_cache_lock)
        if filled:
            r["school_url"] = filled

    for r in rows:
        if not is_usable_school_url(r.get("school_url")):
            r["school_url"] = None

    for r in rows:
        forced_url = manual_city_school_url(r["city"])
        if forced_url:
            r["school_url"] = forced_url

    for r in rows:
        if "MŠ" in r.get("amenities", ""):
            continue
        if registry_city_has_kindergarten(r["city"], registry_cache, registry_cache_lock):
            r["amenities"] = "MŠ" if r["amenities"] == "—" else f"MŠ, {r['amenities']}"

    rows.sort(key=lambda r: (r["drive_min"], r["city"]))

    return rows
