import json
import math
import re
import time
import atexit
import signal
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.parse
import urllib.request
from pathlib import Path
from html import unescape
from html import escape
import tempfile

DOBRUSKA = (50.2921062, 16.1605457)  # lat, lon
RADIUS_M = 55000
MAX_DRIVE_SEC = 3900
CACHE_PATH = Path("school_url_cache.json")
TYPE_CACHE_PATH = Path("school_type_cache.json")
REGISTRY_CACHE_PATH = Path("school_registry_cache.json")
MALOTRIDKY_CACHE_PATH = Path("mapotic_malotridky_cache.json")


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


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c


def osrm_duration_sec(lat, lon):
    url = (
        "https://router.project-osrm.org/route/v1/driving/"
        f"{DOBRUSKA[1]},{DOBRUSKA[0]};{lon},{lat}?overview=false"
    )
    data = http_get(url)
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    return data["routes"][0]["duration"]


def normalize_url(url):
    if not url:
        return None
    u = url.strip()
    if not u:
        return None
    if " " in u or "barrier=" in u:
        return None
    if not (u.startswith("http://") or u.startswith("https://")):
        u = "https://" + u
    parsed = urllib.parse.urlparse(u)
    if not parsed.netloc or "." not in parsed.netloc:
        return None
    return u


def safe_href(url: str | None) -> str | None:
    cleaned = normalize_url(url)
    if not cleaned:
        return None
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme not in {"http", "https"}:
        return None
    if not parsed.netloc:
        return None
    return cleaned


def is_bad_domain(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    blocked = [
        "google.",
        "bing.com",
        "r.bing.com",
        "seznam.cz",
        "facebook.com",
        "instagram.com",
        "youtube.com",
        "mapy.cz",
        "firmy.cz",
        "netfirmy.cz",
        "atlasfirem.info",
        "edb.cz",
        "zlatestranky.cz",
        "wikipedia.org",
        "twitter.com",
        "x.com",
    ]
    return any(x in host for x in blocked)


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


def score_school_url(url: str, city: str) -> int:
    host = urllib.parse.urlparse(url).netloc.lower()
    path = urllib.parse.urlparse(url).path.lower()
    text = f"{host}{path}"
    score = 0
    if "zs" in text or "skola" in text or "edu" in text:
        score += 3
    if city.lower().replace(" ", "") in text.replace("-", "").replace("_", ""):
        score += 2
    if host.endswith(".cz"):
        score += 1
    return score


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

    q = urllib.parse.quote_plus(f"{school_name} {city} základní škola")
    search_urls = [
        f"https://duckduckgo.com/html/?q={q}",
        f"https://search.seznam.cz/?q={q}",
        f"https://www.bing.com/search?q={q}",
    ]
    candidates = []
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
        ranked.append((score_school_url(cleaned, city), cleaned))
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


def load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Corrupted cache file: {CACHE_PATH}") from e


def save_cache(cache: dict) -> None:
    _write_json_atomic(CACHE_PATH, cache)


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
    payload = json.dumps(data, ensure_ascii=False, indent=2)
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


def normalize_text(s: str) -> str:
    t = (s or "").lower()
    repl = {
        "á": "a", "č": "c", "ď": "d", "é": "e", "ě": "e", "í": "i", "ň": "n",
        "ó": "o", "ř": "r", "š": "s", "ť": "t", "ú": "u", "ů": "u", "ý": "y", "ž": "z",
    }
    for k, v in repl.items():
        t = t.replace(k, v)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def school_name_tokens(name: str) -> set[str]:
    stop = {"zakladni", "skola", "a", "ms", "zs", "materska", "okres"}
    toks = set(normalize_text(name).split())
    return {t for t in toks if len(t) > 2 and t not in stop}


def is_selected_school_malotridka(selected_school: dict, malotridky_points: list[dict]) -> bool:
    school_name_n = normalize_text(selected_school.get("name", ""))
    school_tokens = school_name_tokens(school_name_n)
    if not school_tokens:
        return False
    for p in malotridky_points:
        d = haversine_km(selected_school["lat"], selected_school["lon"], p["lat"], p["lon"])
        if d > 0.35:
            continue
        point_tokens = school_name_tokens(p.get("name", ""))
        if not point_tokens:
            continue
        overlap = len(school_tokens & point_tokens)
        if overlap >= 2:
            return True
    return False


place_query = f"""
[out:json][timeout:180];
area["ISO3166-1"="CZ"][admin_level=2]->.cz;
(
  node(area.cz)(around:{RADIUS_M},{DOBRUSKA[0]},{DOBRUSKA[1]})["place"~"city|town|village"];
  way(area.cz)(around:{RADIUS_M},{DOBRUSKA[0]},{DOBRUSKA[1]})["place"~"city|town|village"];
  relation(area.cz)(around:{RADIUS_M},{DOBRUSKA[0]},{DOBRUSKA[1]})["place"~"city|town|village"];
);
out body;
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
  node(area.cz)(around:{RADIUS_M},{DOBRUSKA[0]},{DOBRUSKA[1]})["amenity"~"kindergarten|cinema|theatre"];
  way(area.cz)(around:{RADIUS_M},{DOBRUSKA[0]},{DOBRUSKA[1]})["amenity"~"kindergarten|cinema|theatre"];
  relation(area.cz)(around:{RADIUS_M},{DOBRUSKA[0]},{DOBRUSKA[1]})["amenity"~"kindergarten|cinema|theatre"];
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


def main() -> None:
    print("Fetching municipalities from Overpass...", flush=True)
    places = overpass_query(place_query)
    print("Fetching schools from Overpass...", flush=True)
    schools = overpass_query(school_query)
    print("Fetching amenities from Overpass...", flush=True)
    amenities = overpass_query(amenity_query)
    
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
            lat = None
            lon = None
        if lat is None or lon is None:
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
            }
        })
    
    # deduplicate by name using closest to Dobruska
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
    
    
    def amenity_bucket(value: str) -> str | None:
        v = (value or "").lower()
        if v == "kindergarten":
            return "kindergarten"
        if v == "cinema":
            return "cinema"
        if v == "theatre":
            return "theatre"
        return None
    
    
    def amenity_city_key(s: str) -> str:
        t = (s or "").lower()
        repl = {
            "á": "a", "č": "c", "ď": "d", "é": "e", "ě": "e", "í": "i", "ň": "n",
            "ó": "o", "ř": "r", "š": "s", "ť": "t", "ú": "u", "ů": "u", "ý": "y", "ž": "z",
        }
        for k, v in repl.items():
            t = t.replace(k, v)
        t = re.sub(r"[^a-z0-9 ]+", " ", t)
        return re.sub(r"\s+", " ", t).strip()
    
    
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
    
    
    def looks_primary_school(tags: dict) -> bool:
        name = (tags.get("name") or "").lower()
        isced = (tags.get("isced:level") or "").replace(" ", "")
        school_type = (tags.get("school") or "").lower()
        operator_type = (tags.get("operator:type") or "").lower()
        if "základní škola" in name or "zakladni skola" in name or " zš" in f" {name}":
            return True
        if "1" in isced:
            return True
        if "primary" in school_type or "elementary" in school_type:
            return True
        if "primary" in operator_type:
            return True
        return False
    
    
    def infer_school_type(tags: dict, name: str) -> str:
        n = (name or "").lower()
        grades = (tags.get("grades") or "").lower()
        isced = (tags.get("isced:level") or "").replace(" ", "")
        school_type = (tags.get("school") or "").lower()
        description = (tags.get("description") or "").lower()
        combined = " ".join([n, grades, isced, school_type, description])
    
        if "malotř" in combined or "malotr" in combined:
            return "Malotřídka"
    
        compact_grades = grades.replace(" ", "")
        if compact_grades:
            if "1-2" in compact_grades or compact_grades in {"1,2", "1;2"}:
                return "1-2"
            if "1-5" in compact_grades or compact_grades in {"1,2,3,4,5", "1;2;3;4;5"}:
                return "1-5"
            if "1-9" in compact_grades or compact_grades in {"1,2,3,4,5,6,7,8,9", "1;2;3;4;5;6;7;8;9"}:
                return "1-9"
            if compact_grades in {"1", "grade1"}:
                return "1."
    
        if isced in {"1;2", "1,2", "1-2", "12"}:
            return "1-9"
        if isced == "1":
            return "1-5"
    
        if "1. stupe" in combined or "i. stupe" in combined or "1.stupe" in combined:
            if "2. stupe" in combined or "ii. stupe" in combined or "2.stupe" in combined:
                return "1-9"
            return "1-5"
        return "Neuvedeno"
    
    
    def infer_school_type_from_text(text: str) -> str:
        t = text.lower()
        if "malotř" in t or "malotr" in t:
            m = re.search(r"\b1\s*[-–]\s*([2-9])\b", t)
            if m:
                return f"Malotřídka (1-{m.group(1)})"
            return "Malotřídka"
        for pattern, value in [
            (r"\b1\.\s*-\s*9\.", "1-9"),
            (r"\b1\.\s*-\s*5\.", "1-5"),
            (r"\b1\.\s*-\s*4\.", "1-4"),
            (r"\b1\.\s*-\s*2\.", "1-2"),
            (r"\b1\s*[-–]\s*9\b", "1-9"),
            (r"\b1\s*[-–]\s*5\b", "1-5"),
            (r"\b1\s*[-–]\s*4\b", "1-4"),
            (r"\b1\s*[-–]\s*2\b", "1-2"),
        ]:
            if re.search(pattern, t):
                return value
        if "první stupeň" in t or "prvni stupen" in t or "i. stupeň" in t or "i. stupen" in t:
            if "druhý stupeň" in t or "druhy stupen" in t or "ii. stupeň" in t or "ii. stupen" in t:
                return "1-9"
            return "1-5"
        return "Neuvedeno"
    
    
    def infer_type_from_website(url: str, city: str, school_name: str, type_cache: dict, cache_lock: threading.Lock | None = None) -> str:
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
                    l = u.lower()
                    if any(k in l for k in ["o-skole", "o-skola", "o-nas", "charakteristika", "zakladni-skola", "zs/"]):
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
    
    
    def normalize_text(s: str) -> str:
        t = (s or "").lower()
        repl = {
            "á": "a", "č": "c", "ď": "d", "é": "e", "ě": "e", "í": "i", "ň": "n",
            "ó": "o", "ř": "r", "š": "s", "ť": "t", "ú": "u", "ů": "u", "ý": "y", "ž": "z",
        }
        for k, v in repl.items():
            t = t.replace(k, v)
        t = re.sub(r"[^a-z0-9 ]+", " ", t)
        return re.sub(r"\s+", " ", t).strip()
    
    
    def school_name_tokens(name: str) -> set[str]:
        stop = {"zakladni", "skola", "a", "ms", "zs", "materska", "okres"}
        toks = set(normalize_text(name).split())
        return {t for t in toks if len(t) > 2 and t not in stop}
    
    
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
    
    unnamed_school_points = []
    # assign primary schools to nearest municipality within 6km
    for s in school_points:
        if not looks_primary_school(s["tags"]):
            # Keep unnamed schools as fallback signal for municipalities where OSM lacks school names/tags.
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

    # Fallback: if a municipality has no matched primary school, use a very nearby unnamed school.
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
    
    # only municipalities with at least one matched primary school
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
            school = sorted(m["schools"], key=lambda x: (0 if x.get("website") else 1, x["name"]))[0]
            synthetic_school = bool(school.get("synthetic"))
            school_url = school.get("website")
            # Skip lookup when URL is already present from OSM.
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
            # Skip website scrape unless still unresolved and URL exists.
            if detected_type == "Neuvedeno" and school_url and not synthetic_school:
                detected_type = infer_type_from_website(
                    school_url, m["name"], school["name"], type_cache, type_cache_lock
                )
            is_malotridka = is_selected_school_malotridka(school, malotridky_points)
            if is_malotridka:
                detected_type = "Malotřídka"
    
            return {
                "city": m["name"],
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
                "school_url": school_url
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

    # Final sequential pass for synthetic fallback schools that stayed unresolved.
    for r in rows:
        if r.get("school_type") != "Neuvedeno":
            continue
        if not str(r.get("school_name", "")).startswith("Základní škola ("):
            continue
        resolved = registry_type_for_city_primary(r["city"], registry_cache, registry_cache_lock)
        if resolved != "Neuvedeno":
            r["school_type"] = resolved

    rows.sort(key=lambda r: (r["drive_min"], r["city"]))
    
    html_rows = []
    for r in rows:
        pop = f"{r['population']:,}".replace(",", " ") if r["population"] is not None else "N/A"
        city_text = escape(str(r["city"]))
        pop_text = escape(pop)
        drive_text = escape(str(r["drive_min"]))
        amenities_text = escape(str(r["amenities"]))
        school_type_text = escape(str(r["school_type"]))
        school_name_text = escape(str(r["school_name"]))
        href = safe_href(r.get("school_url"))
        if href:
            school_cell = f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">{school_name_text}</a>'
        else:
            school_cell = school_name_text
        html_rows.append(
            f"<tr><td>{city_text}</td><td>{pop_text}</td><td>{drive_text}</td><td>{amenities_text}</td><td>{school_type_text}</td><td>{school_cell}</td></tr>"
        )
    
    generated_on = time.strftime("%Y-%m-%d")
    html = f"""<!doctype html>
    <html lang=\"en\">
    <head>
      <meta charset=\"utf-8\" />
      <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
      <title>Kde bydlet?</title>
      <style>
        body {{ font-family: Arial, sans-serif; margin: 24px; }}
        h1 {{ margin-bottom: 8px; }}
        p {{ color: #444; margin-top: 0; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background: #f4f4f4; }}
        tr:nth-child(even) {{ background: #fafafa; }}
      </style>
    </head>
    <body>
      <h1>Kde bydlet?</h1>
      <p>Zdroj dat: OpenStreetMap (obce/školy/populace) + OSRM routing. Vygenerováno dne {generated_on}. Záznamů: {len(rows)}.</p>
      <table>
        <thead>
          <tr>
            <th>Město</th>
            <th>Počet obyvatel</th>
            <th>Dojezd z Dobrušky (min)</th>
            <th>Vybavenost</th>
            <th>Typ školy</th>
            <th>Základní škola</th>
          </tr>
        </thead>
        <tbody>
          {''.join(html_rows)}
        </tbody>
      </table>
    </body>
    </html>
    """
    
    Path("index.html").write_text(html, encoding="utf-8")
    Path("dobruska_primary_schools.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(rows)} rows")


if __name__ == "__main__":
    main()
