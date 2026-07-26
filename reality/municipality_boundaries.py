from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

from .paths import MUNICIPALITY_BOUNDARIES_PATH, OVERPASS_MUNICIPALITIES_PATH, SCHOOLS_JSON_PATH


def load_boundaries(path: Path = MUNICIPALITY_BOUNDARIES_PATH) -> dict[str, dict]:
    """Return cached municipality boundaries keyed by their display name."""
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(boundary["city"]): boundary
        for boundary in payload.get("boundaries", [])
        if isinstance(boundary, dict) and boundary.get("city")
    }


def _stitch_segments(segments: list[list[list[float]]]) -> list[list[list[float]]]:
    """Join Overpass outer-way fragments into closed rings.

    The map cache stores simplified way geometries.  Keeping the endpoints lets
    us still reconstruct enough of each multipolygon for municipality matching.
    """
    pending = [[list(point) for point in segment] for segment in segments if len(segment) >= 2]
    rings = []
    while pending:
        ring = pending.pop()
        changed = True
        while changed:
            changed = False
            for index, segment in enumerate(pending):
                if ring[-1] == segment[0]:
                    ring.extend(segment[1:])
                elif ring[-1] == segment[-1]:
                    ring.extend(reversed(segment[:-1]))
                elif ring[0] == segment[-1]:
                    ring[:0] = segment[:-1]
                elif ring[0] == segment[0]:
                    ring[:0] = reversed(segment[1:])
                else:
                    continue
                pending.pop(index)
                changed = True
                break
        if len(ring) >= 3 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


def boundary_contains_point(boundary: dict, lat: float, lon: float) -> bool:
    """Whether a latitude/longitude falls within a cached municipality boundary."""
    for ring in _stitch_segments(boundary.get("segments", [])):
        inside = False
        for (lat_a, lon_a), (lat_b, lon_b) in zip(ring, ring[1:]):
            if (lon_a > lon) == (lon_b > lon):
                continue
            crossing_lat = (lat_b - lat_a) * (lon - lon_a) / (lon_b - lon_a) + lat_a
            if lat < crossing_lat:
                inside = not inside
        if inside:
            return True
    return False


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
