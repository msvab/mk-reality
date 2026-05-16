import json
import math
import re
import time
import atexit
import signal
import urllib.parse
import urllib.request
from pathlib import Path
from html import unescape

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


def is_bad_domain(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    blocked = [
        "google.",
        "bing.com",
        "r.bing.com",
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


def find_school_website(school_name: str, city: str, cache: dict) -> str | None:
    key = f"{school_name}||{city}"
    cached = cache.get(key)
    if cached:
        return cached

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
        cache[key] = ranked[0][1]
        return ranked[0][1]
    if candidates:
        cache[key] = candidates[0]
        return candidates[0]
    cache[key] = ""
    return None


def load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def load_type_cache() -> dict:
    if not TYPE_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(TYPE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_type_cache(cache: dict) -> None:
    TYPE_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def load_registry_cache() -> dict:
    if not REGISTRY_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(REGISTRY_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_registry_cache(cache: dict) -> None:
    REGISTRY_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def load_malotridky_cache() -> dict:
    if not MALOTRIDKY_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(MALOTRIDKY_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_malotridky_cache(cache: dict) -> None:
    MALOTRIDKY_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


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

endpoints = ["https://overpass.kumi.systems/api/interpreter"]

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

print("Fetching municipalities from Overpass...", flush=True)
places = overpass_query(place_query)
print("Fetching schools from Overpass...", flush=True)
schools = overpass_query(school_query)

municipalities = []
for el in places.get("elements", []):
    tags = el.get("tags", {})
    name = tags.get("name")
    if not name:
        continue
    lat = el.get("lat")
    lon = el.get("lon")
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
        "schools": []
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
    name = tags.get("name")
    if not name:
        continue
    if "lat" in el and "lon" in el:
        lat, lon = el["lat"], el["lon"]
    elif "center" in el:
        lat, lon = el["center"]["lat"], el["center"]["lon"]
    else:
        continue
    website = normalize_url(tags.get("website") or tags.get("contact:website") or tags.get("url"))
    school_points.append({"name": name, "lat": lat, "lon": lon, "website": website, "tags": tags})


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
        return "Malotridka"

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
            return f"Malotridka (1-{m.group(1)})"
        return "Malotridka"
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


def infer_type_from_website(url: str, city: str, school_name: str, type_cache: dict) -> str:
    key = f"{url}||{city}||{school_name}"
    if key in type_cache:
        return type_cache[key]
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


def registry_type_for_school(city: str, school_name: str, registry_cache: dict) -> str:
    key = f"{city}||{school_name}"
    if key in registry_cache:
        return registry_cache[key]
    try:
        candidates = registry_search_by_city(city)
        picked = registry_pick_candidate(city, school_name, candidates)
        if not picked:
            registry_cache[key] = "Neuvedeno"
            return "Neuvedeno"
        sub_id = picked["id"]
        date_s = time.strftime("%Y-%m-%d")
        sub = http_get(f"https://isv.gov.cz/rssz/api/v1/sub/{sub_id}?stavKeDni={date_s}")
        zs_units = [z for z in sub.get("zarizeni", []) if z.get("druhSkoly") == "B00"]
        if not zs_units:
            registry_cache[key] = "Neuvedeno"
            return "Neuvedeno"
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
        registry_cache[key] = out
        return out
    except Exception:
        registry_cache[key] = "Neuvedeno"
        return "Neuvedeno"

# assign schools to nearest municipality within 6km
for s in school_points:
    if not looks_primary_school(s["tags"]):
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

def save_all_caches() -> None:
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
    for i, m in enumerate(municipalities):
        dur = osrm_duration_sec(m["lat"], m["lon"])
        time.sleep(0.08)
        if dur is None or dur > MAX_DRIVE_SEC:
            continue
        school = sorted(m["schools"], key=lambda x: (0 if x.get("website") else 1, x["name"]))[0]
        school_url = school.get("website")
        if not school_url:
            school_url = find_school_website(school["name"], m["name"], url_cache)
        detected_type = infer_school_type(school["tags"], school["name"])
        if detected_type == "Neuvedeno":
            detected_type = registry_type_for_school(m["name"], school["name"], registry_cache)
        if detected_type == "Neuvedeno" and school_url:
            detected_type = infer_type_from_website(school_url, m["name"], school["name"], type_cache)
        is_malotridka = is_selected_school_malotridka(school, malotridky_points)
        if is_malotridka:
            detected_type = "Malotridka"

        rows.append({
            "city": m["name"],
            "population": m["population"],
            "drive_min": int(round(dur / 60)),
            "school_type": detected_type,
            "school_name": school["name"],
            "school_url": school_url
        })
        if (i + 1) % 10 == 0:
            save_all_caches()
        if (i + 1) % 30 == 0:
            print(f"processed {i+1}/{len(municipalities)}", flush=True)
except KeyboardInterrupt:
    print("Interrupted. Caches were saved.", flush=True)
    raise
finally:
    save_all_caches()

rows.sort(key=lambda r: (r["drive_min"], r["city"]))

html_rows = []
for r in rows:
    pop = f"{r['population']:,}".replace(",", " ") if r["population"] is not None else "N/A"
    if r["school_url"]:
        school_cell = f'<a href="{r["school_url"]}" target="_blank" rel="noopener noreferrer">{r["school_name"]}</a>'
    else:
        school_cell = r["school_name"]
    html_rows.append(
        f"<tr><td>{r['city']}</td><td>{pop}</td><td>{r['drive_min']}</td><td>{r['school_type']}</td><td>{school_cell}</td></tr>"
    )

html = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Municipalities within ~65 minutes drive from Dobruška with primary schools</title>
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
  <h1>Municipalities within ~65 minutes drive from Dobruška (CZ) with primary schools</h1>
  <p>Data source: OpenStreetMap (municipalities/schools/population tags) + OSRM routing. Generated on 2026-05-16. Records: {len(rows)}.</p>
  <table>
    <thead>
      <tr>
        <th>City</th>
        <th>Population</th>
        <th>Drive from Dobruška in minutes</th>
        <th>School type</th>
        <th>Primary school</th>
      </tr>
    </thead>
    <tbody>
      {''.join(html_rows)}
    </tbody>
  </table>
</body>
</html>
"""

Path("dobruska_primary_schools.html").write_text(html, encoding="utf-8")
Path("dobruska_primary_schools.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Wrote {len(rows)} rows")
