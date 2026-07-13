from __future__ import annotations

import math
import re
import urllib.parse


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c


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


def is_generic_primary_school_name(name: str) -> bool:
    return normalize_text(name) in {
        "zakladni skola",
        "zakladni skola a materska skola",
        "zs",
        "zs a ms",
    }


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


def expand_school_name_queries(school_name: str, city: str) -> list[str]:
    raw = (school_name or "").strip()
    city = (city or "").strip()
    variants = []

    def add(query: str) -> None:
        query = re.sub(r"\s+", " ", (query or "").strip())
        if query and query not in variants:
            variants.append(query)

    expanded = raw
    expanded = re.sub(r"\bZŠ\b", "Základní škola", expanded)
    expanded = re.sub(r"\bMŠ\b", "mateřská škola", expanded)
    compact = raw.replace("-", " ").replace(",", " ")
    compact = re.sub(r"\s+", " ", compact).strip()

    add(f"{raw} {city}")
    add(f"{expanded} {city}")
    add(f"{compact} {city}")
    add(f"{raw} {city} oficiální stránky")
    add(f"{expanded} {city} oficiální stránky")
    add(f"{raw} {city} základní škola")
    add(f"{expanded} {city} základní škola")

    # Generic OSM names need city-driven fallbacks with common Czech variants.
    generic_names = {
        "základní škola",
        "základní škola a mateřská škola",
        "zš",
        "zš a mš",
    }
    if normalize_text(raw) in generic_names:
        add(f"ZŠ {city}")
        add(f"Základní škola {city}")
        add(f"ZŠ a MŠ {city}")
        add(f"Základní škola a mateřská škola {city}")

    return variants


def score_school_url(url: str, city: str, school_name: str = "") -> int:
    host = urllib.parse.urlparse(url).netloc.lower()
    path = urllib.parse.urlparse(url).path.lower()
    text = f"{host}{path}"
    normalized_text = normalize_text(text.replace(".", " ").replace("/", " ").replace("-", " ").replace("_", " "))
    city_tokens = set(normalize_text(city).split())
    school_tokens = school_name_tokens(school_name)
    score = 0
    if "zs" in text or "skola" in text or "edu" in text:
        score += 3
    if normalize_text(city).replace(" ", "") in normalized_text.replace(" ", ""):
        score += 2
    score += len(city_tokens & set(normalized_text.split())) * 2
    score += len(school_tokens & set(normalized_text.split())) * 3
    if school_tokens and len(school_tokens & set(normalized_text.split())) >= 2:
        score += 2
    if any(x in host for x in ["zs", "skola", "edu"]):
        score += 2
    if host.endswith(".cz"):
        score += 1
    return score


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
    return "primary" in operator_type


def looks_kindergarten_hint(tags: dict, name: str) -> bool:
    n = (name or "").lower()
    school_type = (tags.get("school") or "").lower()
    desc = (tags.get("description") or "").lower()
    combined = " ".join([n, school_type, desc])
    return any(
        x in combined
        for x in ["mateřská škola", "materska skola", " mš", " ms", "zš a mš", "zs a ms"]
    )


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
        (r"\bod\s*1\.\s*do\s*9\.\s*t", "1-9"),
        (r"\bod\s*1\.\s*do\s*5\.\s*t", "1-5"),
        (r"\bod\s*1\.\s*do\s*4\.\s*t", "1-4"),
        (r"\bod\s*1\.\s*do\s*2\.\s*t", "1-2"),
    ]:
        if re.search(pattern, t):
            return value
    if "první stupeň" in t or "prvni stupen" in t or "i. stupeň" in t or "i. stupen" in t:
        if "druhý stupeň" in t or "druhy stupen" in t or "ii. stupeň" in t or "ii. stupen" in t:
            return "1-9"
        return "1-5"
    return "Neuvedeno"
