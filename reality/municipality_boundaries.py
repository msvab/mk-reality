from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

from .paths import MUNICIPALITY_BOUNDARIES_PATH, OVERPASS_MUNICIPALITIES_PATH, SCHOOLS_JSON_PATH


def _sample(points: list[dict]) -> list[list[float]]:
    if len(points) < 3:
        return []
    step = max(1, len(points) // 8)
    sampled = points[::step]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return [[round(float(point["lat"]), 6), round(float(point["lon"]), 6)] for point in sampled]


def refresh_boundaries(output_path: Path = MUNICIPALITY_BOUNDARIES_PATH) -> int:
    rows = json.loads(SCHOOLS_JSON_PATH.read_text(encoding="utf-8"))
    cities = {str(row["city"]) for row in rows if isinstance(row, dict) and row.get("city")}
    municipalities = json.loads(OVERPASS_MUNICIPALITIES_PATH.read_text(encoding="utf-8"))["response"]["elements"]
    ids = [str(element["id"]) for element in municipalities if element.get("tags", {}).get("name") in cities]
    query = f"[out:json][timeout:180];relation(id:{','.join(ids)});out geom;"
    request = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
        method="POST",
        headers={"User-Agent": "mk-reality/1.0 (municipality boundary cache refresh)"},
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        elements = json.loads(response.read().decode("utf-8"))["elements"]

    boundaries = []
    for element in elements:
        name = element.get("tags", {}).get("name")
        if name not in cities:
            continue
        segments = []
        for member in element.get("members", []):
            if member.get("role") != "outer" or member.get("type") != "way":
                continue
            sampled = _sample(member.get("geometry", []))
            if sampled:
                segments.append(sampled)
        if segments:
            boundaries.append({"city": name, "segments": segments})
    output_path.write_text(json.dumps({"boundaries": boundaries}, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(boundaries)


if __name__ == "__main__":
    print(f"Wrote {refresh_boundaries()} municipality boundaries")
