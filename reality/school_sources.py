from __future__ import annotations

import argparse
import json
import re
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from html import unescape
from pathlib import Path

from .build_html_urls import is_bad_domain, is_usable_school_url, normalize_url
from .paths import (
    CACHE_DIR,
    MAPOTIC_MALOTRIDKY_CACHE_PATH,
    OVERPASS_DATA_DIR,
    SCHOOL_REGISTRY_CACHE_PATH,
    SCHOOL_TYPE_CACHE_PATH,
    SCHOOL_URL_CACHE_PATH,
)
from .school_normalization import (
    expand_school_name_queries,
    infer_school_type_from_text,
    normalize_text,
    school_name_tokens,
    score_school_url,
)

DOBRUSKA = (50.2921062, 16.1605457)  # lat, lon
RADIUS_M = 55000
MAX_DRIVE_SEC = 3900
DATA_CACHE_DIR = CACHE_DIR
CACHE_PATH = SCHOOL_URL_CACHE_PATH
TYPE_CACHE_PATH = SCHOOL_TYPE_CACHE_PATH
REGISTRY_CACHE_PATH = SCHOOL_REGISTRY_CACHE_PATH
MALOTRIDKY_CACHE_PATH = MAPOTIC_MALOTRIDKY_CACHE_PATH
OVERPASS_CACHE_DIR = OVERPASS_DATA_DIR
MANUAL_CITY_SCHOOL_URLS = {
    "Třebechovice pod Orebem": "https://www.zst.cz/w/zakladni-skola/",
}


def http_post(url: str, data: str, timeout: int = 120) -> dict:
    req = urllib.request.Request(url, data=data.encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def http_get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def http_get_text(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; school-list-bot/1.0)"
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        content_type = r.headers.get("Content-Type", "")
    if "charset=" in content_type.lower():
        enc = content_type.lower().split("charset=", 1)[1].split(";")[0].strip()
    else:
        enc = "utf-8"
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def strip_html_text(html: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def osrm_duration_sec(lat, lon):
    url = (
        "https://router.project-osrm.org/route/v1/driving/"
        f"{DOBRUSKA[1]},{DOBRUSKA[0]};{lon},{lat}?overview=false"
    )
    data = http_get(url)
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    return data["routes"][0]["duration"]


def email_domain_candidates(emails: list[str]) -> list[str]:
    blocked_domains = {
        "gmail.com",
        "seznam.cz",
        "email.cz",
        "centrum.cz",
        "atlas.cz",
        "post.cz",
        "volny.cz",
        "tiscali.cz",
        "icloud.com",
        "outlook.com",
        "hotmail.com",
    }
    out = []
    seen = set()
    for email in emails or []:
        if "@" not in email:
            continue
        domain = email.split("@", 1)[1].strip().lower()
        domain = domain.strip(" .")
        if not domain or domain in blocked_domains or "." not in domain:
            continue
        for candidate in (f"https://{domain}/", f"https://www.{domain}/"):
            cleaned = normalize_url(candidate)
            if not cleaned or cleaned in seen or is_bad_domain(cleaned):
                continue
            seen.add(cleaned)
            out.append(cleaned)
    return out


def extract_candidate_links(html: str) -> list[str]:
    links = re.findall(r"""href=['"]([^'"]+)['"]""", html, flags=re.IGNORECASE)
    out = []
    seen = set()
    for link in links:
        link = unescape(link)
        if "duckduckgo.com/l/?" in link and "uddg=" in link:
            parsed = urllib.parse.urlparse(link)
            params = urllib.parse.parse_qs(parsed.query)
            if "uddg" in params and params["uddg"]:
                link = urllib.parse.unquote(params["uddg"][0])
        if link.startswith("//"):
            link = "https:" + link
        cleaned = normalize_url(link)
        if not cleaned or cleaned in seen or is_bad_domain(cleaned):
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def extract_internal_links(base_url: str, html: str) -> list[str]:
    parsed_base = urllib.parse.urlparse(base_url)
    base_host = parsed_base.netloc.lower()
    hrefs = re.findall(r'href="([^"]+)"', html, flags=re.IGNORECASE)
    out = []
    seen = set()
    for href in hrefs:
        href = unescape(href).strip()
        if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        abs_url = urllib.parse.urljoin(base_url, href)
        p = urllib.parse.urlparse(abs_url)
        if p.scheme not in {"http", "https"}:
            continue
        if p.netloc.lower() != base_host:
            continue
        cleaned = normalize_url(abs_url)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def find_school_website(school_name: str, city: str, cache: dict, cache_lock: threading.Lock | None = None) -> str | None:
    key = f"{school_name}||{city}"
    if cache_lock:
        with cache_lock:
            has_cached = key in cache
            cached = cache.get(key)
    else:
        has_cached = key in cache
        cached = cache.get(key)
    if has_cached:
        return cached or None

    candidates = []
    for qraw in expand_school_name_queries(school_name, city):
        q = urllib.parse.quote_plus(qraw)
        search_urls = [
            f"https://duckduckgo.com/html/?q={q}",
            f"https://search.seznam.cz/?q={q}",
            f"https://www.bing.com/search?q={q}",
        ]
        for search_url in search_urls:
            try:
                html = http_get_text(search_url, timeout=10)
            except Exception:
                continue
            candidates.extend(extract_candidate_links(html))
    candidates = candidates[:12]

    seen = set()
    ranked = []
    for cleaned in candidates:
        if cleaned in seen:
            continue
        seen.add(cleaned)
        ranked.append((score_school_url(cleaned, city, school_name), cleaned))
    ranked.sort(reverse=True)
    if ranked and ranked[0][0] > 0:
        if cache_lock:
            with cache_lock:
                cache[key] = ranked[0][1]
        else:
            cache[key] = ranked[0][1]
        return ranked[0][1]
    if candidates:
        if cache_lock:
            with cache_lock:
                cache[key] = candidates[0]
        else:
            cache[key] = candidates[0]
        return candidates[0]
    if cache_lock:
        with cache_lock:
            cache[key] = ""
    else:
        cache[key] = ""
    return None


def find_school_website_by_city(city: str, cache: dict, cache_lock: threading.Lock | None = None) -> str | None:
    key = f"__city__||{city}"
    if cache_lock:
        with cache_lock:
            has_cached = key in cache
            cached = cache.get(key)
    else:
        has_cached = key in cache
        cached = cache.get(key)
    if has_cached:
        return cached or None

    queries = [
        f"ZŠ {city}",
        f"Základní škola {city}",
        f"ZŠ a MŠ {city}",
        f"Základní škola a mateřská škola {city}",
    ]
    candidates = []
    for qraw in queries:
        q = urllib.parse.quote_plus(qraw)
        search_urls = [
            f"https://duckduckgo.com/html/?q={q}",
            f"https://search.seznam.cz/?q={q}",
            f"https://www.bing.com/search?q={q}",
        ]
        for search_url in search_urls:
            try:
                html = http_get_text(search_url, timeout=10)
            except Exception:
                continue
            candidates.extend(extract_candidate_links(html))

    ranked = []
    seen = set()
    for url in candidates[:20]:
        if url in seen:
            continue
        seen.add(url)
        score = score_school_url(url, city)
        host = urllib.parse.urlparse(url).netloc.lower()
        if any(x in host for x in ["zs", "skola", "edu"]):
            score += 2
        ranked.append((score, url))
    ranked.sort(reverse=True)

    out = ranked[0][1] if ranked and ranked[0][0] > 0 else ""
    if cache_lock:
        with cache_lock:
            cache[key] = out
    else:
        cache[key] = out
    return out or None


def load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Corrupted cache file: {CACHE_PATH}") from e
    cleaned = {}
    for key, value in raw.items():
        if is_usable_school_url(value):
            cleaned[key] = value
    return cleaned


def manual_city_school_url(city: str) -> str | None:
    return MANUAL_CITY_SCHOOL_URLS.get(city)


def save_cache(cache: dict) -> None:
    filtered = {}
    for key, value in cache.items():
        if is_usable_school_url(value):
            filtered[key] = value
    _write_json_atomic(CACHE_PATH, filtered)


def load_type_cache() -> dict:
    if not TYPE_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(TYPE_CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Corrupted cache file: {TYPE_CACHE_PATH}") from e


def save_type_cache(cache: dict) -> None:
    _write_json_atomic(TYPE_CACHE_PATH, cache)


def load_registry_cache() -> dict:
    if not REGISTRY_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(REGISTRY_CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Corrupted cache file: {REGISTRY_CACHE_PATH}") from e


def save_registry_cache(cache: dict) -> None:
    _write_json_atomic(REGISTRY_CACHE_PATH, cache)


def load_malotridky_cache() -> dict:
    if not MALOTRIDKY_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(MALOTRIDKY_CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Corrupted cache file: {MALOTRIDKY_CACHE_PATH}") from e


def save_malotridky_cache(cache: dict) -> None:
    _write_json_atomic(MALOTRIDKY_CACHE_PATH, cache)


def _write_json_atomic(path: Path, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(payload)
        tmp.flush()
        tmp_name = tmp.name
    Path(tmp_name).replace(path)


def fetch_mapotic_malotridky(cache: dict) -> list[dict]:
    # Public GeoJSON endpoint for mapotic map "Malotřídky v ČR" (map id 2803)
    # Ref: https://www.mapotic.com/malotridky-v-cr
    if cache.get("points"):
        return cache["points"]

    url = "https://www.mapotic.com/api/v1/maps/2803/pois.geojson/"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))

    points = []
    for f in data.get("features", []):
        geom = f.get("geometry", {})
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue
        props = f.get("properties", {}) or {}
        points.append({
            "name": props.get("name", ""),
            "lon": float(coords[0]),
            "lat": float(coords[1]),
        })

    cache["points"] = points
    return points


place_query = f"""
[out:json][timeout:180];
area["ISO3166-1"="CZ"][admin_level=2]->.cz;
(
  relation(area.cz)(around:{RADIUS_M},{DOBRUSKA[0]},{DOBRUSKA[1]})["boundary"="administrative"]["admin_level"="8"];
);
out center tags;
""".strip()

school_query = f"""
[out:json][timeout:180];
area["ISO3166-1"="CZ"][admin_level=2]->.cz;
(
  node(area.cz)(around:{RADIUS_M},{DOBRUSKA[0]},{DOBRUSKA[1]})["amenity"="school"];
  way(area.cz)(around:{RADIUS_M},{DOBRUSKA[0]},{DOBRUSKA[1]})["amenity"="school"];
  relation(area.cz)(around:{RADIUS_M},{DOBRUSKA[0]},{DOBRUSKA[1]})["amenity"="school"];
);
out center tags;
""".strip()

amenity_query = f"""
[out:json][timeout:180];
area["ISO3166-1"="CZ"][admin_level=2]->.cz;
(
  nwr(area.cz)(around:{RADIUS_M},{DOBRUSKA[0]},{DOBRUSKA[1]})["amenity"~"^(kindergarten|cinema|theatre)$"];
);
out center tags;
""".strip()

endpoints = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


def overpass_query(query: str) -> dict:
    payload = urllib.parse.urlencode({"data": query})
    last_err = None
    for ep in endpoints:
        for _ in range(2):
            try:
                return http_post(ep, payload, timeout=180)
            except Exception as e:
                last_err = e
                time.sleep(1.0)
    raise last_err


def load_or_fetch_overpass(name: str, query: str, cache_dir: Path, refresh: bool) -> dict:
    cache_path = cache_dir / f"{name}.json"
    if not refresh and cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("query") == query and isinstance(cached.get("response"), dict):
            print(f"Using cached {name} from {cache_path}", flush=True)
            return cached["response"]
        print(f"Cache miss for {name}: query changed, refreshing Overpass data.", flush=True)

    print(f"Fetching {name} from Overpass...", flush=True)
    response = overpass_query(query)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
                "query": query,
                "response": response,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return response


def load_overpass_inputs(args: argparse.Namespace) -> tuple[dict, dict, dict]:
    cache_dir = Path(args.overpass_cache_dir)
    places = load_or_fetch_overpass("municipalities", place_query, cache_dir, args.refresh_overpass)
    schools = load_or_fetch_overpass("schools", school_query, cache_dir, args.refresh_overpass)
    amenities = load_or_fetch_overpass("amenities", amenity_query, cache_dir, args.refresh_overpass)
    return places, schools, amenities


def infer_type_from_website(url: str, city: str, school_name: str, type_cache: dict, cache_lock: threading.Lock | None = None) -> str:
    if not is_usable_school_url(url):
        return "Neuvedeno"
    key = f"{url}||{city}||{school_name}"
    if cache_lock:
        with cache_lock:
            cached = type_cache.get(key)
    else:
        cached = type_cache.get(key)
    if cached is not None:
        return cached
    try:
        html = http_get_text(url, timeout=8)
        detected = infer_school_type_from_text(strip_html_text(html))
        if detected == "Neuvedeno":
            candidate_pages = extract_internal_links(url, html)
            preferred = []
            for u in candidate_pages:
                lower_url = u.lower()
                if any(k in lower_url for k in ["o-skole", "o-skola", "o-nas", "charakteristika", "zakladni-skola", "zs/"]):
                    preferred.append(u)
            for sub_url in preferred[:4]:
                try:
                    sub_html = http_get_text(sub_url, timeout=6)
                except Exception:
                    continue
                detected = infer_school_type_from_text(strip_html_text(sub_html))
                if detected != "Neuvedeno":
                    break
    except Exception:
        detected = "Neuvedeno"
    if cache_lock:
        with cache_lock:
            type_cache[key] = detected
    else:
        type_cache[key] = detected
    return detected


def registry_search_by_city(city: str) -> list[dict]:
    url = "https://isv.gov.cz/rssz/api/v1/sub/vyhledej"
    payload = json.dumps({"nazev": city}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data.get("list", [])


def registry_pick_candidate(city: str, school_name: str, candidates: list[dict]) -> dict | None:
    city_n = normalize_text(city)
    target_tokens = school_name_tokens(school_name)
    best = None
    best_score = -1
    for c in candidates:
        cname_n = normalize_text(c.get("nazev", ""))
        cadr_n = normalize_text(c.get("adresa", ""))
        score = 0
        if "zakladni skola" in cname_n:
            score += 5
        if city_n and (city_n in cname_n or city_n in cadr_n):
            score += 4
        score += len(target_tokens & school_name_tokens(c.get("nazev", ""))) * 3
        if score > best_score:
            best = c
            best_score = score
    if best_score < 6:
        return None
    return best


def registry_school_website(
    city: str,
    school_name: str,
    registry_cache: dict,
    cache_lock: threading.Lock | None = None,
) -> str | None:
    key = f"{city}||{school_name}||__URL__"
    if cache_lock:
        with cache_lock:
            cached = registry_cache.get(key)
    else:
        cached = registry_cache.get(key)
    if cached is not None:
        return cached or None

    out = ""
    try:
        candidates = registry_search_by_city(city)
        picked = registry_pick_candidate(city, school_name, candidates)
        if picked:
            date_s = time.strftime("%Y-%m-%d")
            sub = http_get(f"https://isv.gov.cz/rssz/api/v1/sub/{picked['id']}?stavKeDni={date_s}")
            urls = []
            for url in email_domain_candidates(sub.get("emaily", [])):
                score = score_school_url(url, city, school_name)
                urls.append((score, url))
            urls.sort(reverse=True)
            if urls:
                best_score, best_url = urls[0]
                best_text = normalize_text(best_url.replace(".", " ").replace("/", " ").replace("-", " ").replace("_", " "))
                best_tokens = set(best_text.split())
                best_host = urllib.parse.urlparse(best_url).netloc.lower()
                has_school_marker = any(x in best_host for x in ["zs", "skola", "edu"])
                token_overlap = len(school_name_tokens(school_name) & best_tokens)
                if best_score > 0 and (has_school_marker or token_overlap >= 1):
                    out = best_url
    except Exception:
        out = ""

    if cache_lock:
        with cache_lock:
            registry_cache[key] = out
    else:
        registry_cache[key] = out
    return out or None


def registry_type_for_school(
    city: str,
    school_name: str,
    registry_cache: dict,
    cache_lock: threading.Lock | None = None,
    force_refresh_if_unknown: bool = False,
) -> str:
    key = f"{city}||{school_name}"
    if cache_lock:
        with cache_lock:
            cached = registry_cache.get(key)
    else:
        cached = registry_cache.get(key)
    if cached is not None and not (force_refresh_if_unknown and cached == "Neuvedeno"):
        return cached
    attempts = 3 if force_refresh_if_unknown else 1
    for attempt in range(attempts):
        try:
            candidates = registry_search_by_city(city)
            picked = registry_pick_candidate(city, school_name, candidates)
            if not picked:
                out = "Neuvedeno"
            else:
                sub_id = picked["id"]
                date_s = time.strftime("%Y-%m-%d")
                sub = http_get(f"https://isv.gov.cz/rssz/api/v1/sub/{sub_id}?stavKeDni={date_s}")
                zs_units = [z for z in sub.get("zarizeni", []) if z.get("druhSkoly") == "B00"]
                if not zs_units:
                    out = "Neuvedeno"
                else:
                    unit_id = zs_units[0]["id"]
                    det = http_get(f"https://isv.gov.cz/rssz/api/v1/skola-skolske-zarizeni/{unit_id}?stavKeDni={date_s}")
                    years = []
                    for o in det.get("obory", []):
                        m = re.match(r"\s*(\d+)\s*r", o.get("delkaVzdelavani", ""))
                        if m:
                            years.append(int(m.group(1)))
                    if not years:
                        out = "Neuvedeno"
                    else:
                        y = max(years)
                        out = {9: "1-9", 5: "1-5", 4: "1-4", 2: "1-2", 1: "1."}.get(y, "1-9" if y > 9 else "Neuvedeno")
            if cache_lock:
                with cache_lock:
                    registry_cache[key] = out
            else:
                registry_cache[key] = out
            return out
        except Exception:
            if attempt < attempts - 1:
                time.sleep(0.5)
                continue
    if cache_lock:
        with cache_lock:
            registry_cache[key] = "Neuvedeno"
    else:
        registry_cache[key] = "Neuvedeno"
    return "Neuvedeno"


def registry_type_for_city_primary(city: str, registry_cache: dict, cache_lock: threading.Lock | None = None) -> str:
    key = f"{city}||__PRIMARY_BY_CITY__"
    if cache_lock:
        with cache_lock:
            cached = registry_cache.get(key)
    else:
        cached = registry_cache.get(key)
    if cached is not None and cached != "Neuvedeno":
        return cached

    city_n = normalize_text(city)
    out = "Neuvedeno"
    for attempt in range(5):
        try:
            candidates = registry_search_by_city(city)
            picked = None
            for c in candidates:
                cname_n = normalize_text(c.get("nazev", ""))
                cadr_n = normalize_text(c.get("adresa", ""))
                if "zakladni skola" in cname_n and (city_n in cname_n or city_n in cadr_n):
                    picked = c
                    break
            if not picked:
                break
            sub_id = picked["id"]
            date_s = time.strftime("%Y-%m-%d")
            sub = http_get(f"https://isv.gov.cz/rssz/api/v1/sub/{sub_id}?stavKeDni={date_s}")
            zs_units = [z for z in sub.get("zarizeni", []) if z.get("druhSkoly") == "B00"]
            if not zs_units:
                break
            unit_id = zs_units[0]["id"]
            det = http_get(f"https://isv.gov.cz/rssz/api/v1/skola-skolske-zarizeni/{unit_id}?stavKeDni={date_s}")
            years = []
            for o in det.get("obory", []):
                m = re.match(r"\s*(\d+)\s*r", o.get("delkaVzdelavani", ""))
                if m:
                    years.append(int(m.group(1)))
            if years:
                y = max(years)
                out = {9: "1-9", 5: "1-5", 4: "1-4", 2: "1-2", 1: "1."}.get(y, "1-9" if y > 9 else "Neuvedeno")
            break
        except Exception:
            if attempt < 4:
                time.sleep(0.8)
                continue

    if cache_lock:
        with cache_lock:
            registry_cache[key] = out
    else:
        registry_cache[key] = out
    return out


def registry_city_has_kindergarten(city: str, registry_cache: dict, cache_lock: threading.Lock | None = None) -> bool:
    key = f"{city}||__HAS_MATERSKA__"
    if cache_lock:
        with cache_lock:
            cached = registry_cache.get(key)
    else:
        cached = registry_cache.get(key)
    if cached is not None:
        return cached == "1"

    out = False
    city_n = normalize_text(city)
    for attempt in range(3):
        try:
            candidates = registry_search_by_city(city)
            for c in candidates:
                cname_n = normalize_text(c.get("nazev", ""))
                cadr_n = normalize_text(c.get("adresa", ""))
                if city_n and not (city_n in cname_n or city_n in cadr_n):
                    continue
                if "materska skola" in cname_n or " m s " in f" {cname_n} ":
                    out = True
                    break
            break
        except Exception:
            if attempt < 2:
                time.sleep(0.5)
                continue
    if cache_lock:
        with cache_lock:
            registry_cache[key] = "1" if out else "0"
    else:
        registry_cache[key] = "1" if out else "0"
    return out
