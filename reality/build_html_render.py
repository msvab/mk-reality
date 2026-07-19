from __future__ import annotations

import json
import time
from html import escape
from pathlib import Path

from .build_html_ads import load_real_estate_ads_by_city, render_ads_count_cell, render_ads_drawer_assets
from .build_html_urls import safe_href
from .paths import HTML_PATH, OVERPASS_MUNICIPALITIES_PATH, SCHOOLS_JSON_PATH
from .school_sources import DOBRUSKA


def load_cached_school_rows(path: Path = SCHOOLS_JSON_PATH) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist; run a full python -m reality.build_html first.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON array.")
    return [row for row in payload if isinstance(row, dict)]


def load_municipality_centers(path: Path = OVERPASS_MUNICIPALITIES_PATH) -> dict[str, tuple[float, float]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    response = payload.get("response", {}) if isinstance(payload, dict) else {}
    centers = {}
    if not isinstance(response, dict):
        return centers
    for el in response.get("elements", []):
        if not isinstance(el, dict):
            continue
        tags = el.get("tags", {})
        name = tags.get("name") if isinstance(tags, dict) else None
        if not name:
            continue
        if "lat" in el and "lon" in el:
            lat, lon = el["lat"], el["lon"]
        elif isinstance(el.get("center"), dict):
            lat, lon = el["center"].get("lat"), el["center"].get("lon")
        else:
            continue
        try:
            centers.setdefault(str(name), (float(lat), float(lon)))
        except (TypeError, ValueError):
            continue
    return centers


def _row_coordinates(row: dict, centers: dict[str, tuple[float, float]]) -> tuple[float, float] | None:
    try:
        return (float(row["lat"]), float(row["lon"]))
    except (KeyError, TypeError, ValueError):
        return centers.get(str(row.get("city", "")))


def _ad_count_for_city(ads_by_city: dict | None, city: str) -> int:
    if not ads_by_city:
        return 0
    bundle = ads_by_city.get("cities", {}).get(city, {})
    if not isinstance(bundle, dict):
        return 0
    count = bundle.get("count", len(bundle.get("ads", [])))
    return count if isinstance(count, int) else 0


def _map_marker_class(school_type: str, ad_count: int) -> str:
    if ad_count >= 10:
        size = "map-marker-large"
    elif ad_count > 0:
        size = "map-marker-medium"
    else:
        size = "map-marker-empty"
    if school_type == "1-9":
        school_class = "map-marker-school-full"
    elif school_type == "1-5":
        school_class = "map-marker-school-lower"
    elif school_type == "Malotřídka":
        school_class = "map-marker-school-small"
    else:
        school_class = "map-marker-school-unknown"
    return f"{size} {school_class}"


def render_map_section(rows: list[dict], ads_by_city: dict | None) -> str:
    centers = load_municipality_centers()
    points = []
    for row in rows:
        coords = _row_coordinates(row, centers)
        if coords is None:
            continue
        lat, lon = coords
        points.append({
            "row": row,
            "lat": lat,
            "lon": lon,
            "ads": _ad_count_for_city(ads_by_city, str(row.get("city", ""))),
        })
    if not points:
        return ""

    points.append({
        "row": {"city": "Dobruška", "drive_min": 0, "school_type": "referenční bod", "population": None},
        "lat": DOBRUSKA[0],
        "lon": DOBRUSKA[1],
        "ads": 0,
        "is_origin": True,
    })
    lat_values = [point["lat"] for point in points]
    lon_values = [point["lon"] for point in points]
    lat_min, lat_max = min(lat_values), max(lat_values)
    lon_min, lon_max = min(lon_values), max(lon_values)
    lat_pad = max((lat_max - lat_min) * 0.08, 0.02)
    lon_pad = max((lon_max - lon_min) * 0.08, 0.02)
    lat_min -= lat_pad
    lat_max += lat_pad
    lon_min -= lon_pad
    lon_max += lon_pad

    markers = []
    for point in points:
        row = point["row"]
        city = str(row.get("city", ""))
        left = 100 * (point["lon"] - lon_min) / (lon_max - lon_min)
        top = 100 * (lat_max - point["lat"]) / (lat_max - lat_min)
        if point.get("is_origin"):
            markers.append(
                f'<span class="map-origin" style="left: {left:.2f}%; top: {top:.2f}%;" title="Dobruška">Dobruška</span>'
            )
            continue
        ad_count = int(point["ads"])
        school_type = str(row.get("school_type", ""))
        marker_class = _map_marker_class(school_type, ad_count)
        city_attr = escape(city, quote=True)
        title = (
            f"{city}: {ad_count} inzerátů, dojezd {row.get('drive_min')} min, "
            f"škola {school_type}, obyvatel {row.get('population') or 'N/A'}"
        )
        label = f'<span class="map-marker-label">{escape(city)}</span>' if ad_count >= 10 or row.get("drive_min", 999) <= 20 else ""
        markers.append(
            f'<button type="button" class="map-marker {marker_class}" data-map-city="{city_attr}" '
            f'style="left: {left:.2f}%; top: {top:.2f}%;" title="{escape(title, quote=True)}" '
            f'aria-label="{escape(title, quote=True)}"><span class="map-marker-dot">{ad_count}</span>{label}</button>'
        )

    marker_markup = "\n          ".join(markers)
    return f"""<section class="map-section" aria-label="Mapa obcí">
        <div class="map-header">
          <div>
            <div class="map-title">Mapa obcí a inzerátů</div>
            <p>Velikost bodu odpovídá počtu aktivních inzerátů. Kliknutím otevřete detail obce.</p>
          </div>
          <div class="map-legend" aria-label="Legenda mapy">
            <span><i class="map-legend-dot map-marker-school-full"></i>1-9</span>
            <span><i class="map-legend-dot map-marker-school-lower"></i>1-5</span>
            <span><i class="map-legend-dot map-marker-school-small"></i>Malotřídka</span>
            <span><i class="map-legend-dot map-marker-school-unknown"></i>Neuvedeno</span>
          </div>
        </div>
        <div class="map-canvas">
          <div class="map-grid" aria-hidden="true"></div>
          {marker_markup}
        </div>
      </section>"""


def render_html(rows: list[dict]) -> str:
    ads_by_city = load_real_estate_ads_by_city()
    map_section = render_map_section(rows, ads_by_city)
    html_rows = []
    for r in rows:
        pop = f"{r['population']:,}".replace(",", " ") if r["population"] is not None else "N/A"
        city_text = escape(str(r["city"]))
        pop_text = escape(pop)
        drive_text = escape(str(r["drive_min"]))
        amenities_text = escape(str(r["amenities"]))
        school_type_text = escape(str(r["school_type"]))
        school_name_text = escape(str(r["school_name"]))
        ads_count_cell = render_ads_count_cell(r["city"], ads_by_city)
        href = safe_href(r.get("school_url"))
        if href:
            school_cell = f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">{school_name_text}</a>'
        else:
            school_cell = school_name_text
        html_rows.append(
            f"<tr><td>{city_text}</td><td>{pop_text}</td><td>{drive_text}</td><td>{amenities_text}</td><td>{ads_count_cell}</td><td>{school_type_text}</td><td>{school_cell}</td></tr>"
        )

    ads_drawer_assets = render_ads_drawer_assets(ads_by_city)
    generated_on = time.strftime("%Y-%m-%d")
    return f"""<!doctype html>
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
        .view-switch {{ display: flex; gap: 8px; margin: 20px 0 12px; }}
        .view-switch-button {{ border: 1px solid #d1d5db; border-radius: 999px; background: #fff; color: #374151; padding: 7px 14px; font: inherit; cursor: pointer; }}
        .view-switch-button[aria-selected="true"] {{ background: #0f766e; border-color: #0f766e; color: #fff; }}
        .report-view[hidden] {{ display: none; }}
        .map-section {{ margin: 20px 0; border: 1px solid #d1d5db; border-radius: 8px; overflow: hidden; background: #fff; }}
        .map-header {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 12px 16px; background: #f9fafb; border-bottom: 1px solid #e5e7eb; }}
        .map-title {{ font-weight: 700; color: #111827; }}
        .map-legend {{ display: flex; flex-wrap: wrap; gap: 8px 12px; color: #374151; font-size: 13px; }}
        .map-legend span {{ display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }}
        .map-legend-dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 999px; border: 1px solid rgba(17, 24, 39, 0.3); }}
        .map-canvas {{ position: relative; height: clamp(320px, 48vw, 620px); overflow: hidden; background: #eef7f3; }}
        .map-grid {{ position: absolute; inset: 0; background-image: linear-gradient(rgba(15, 23, 42, 0.08) 1px, transparent 1px), linear-gradient(90deg, rgba(15, 23, 42, 0.08) 1px, transparent 1px); background-size: 12.5% 12.5%; }}
        .map-marker {{ position: absolute; transform: translate(-50%, -50%); border: 2px solid #fff; border-radius: 999px; box-shadow: 0 2px 8px rgba(15, 23, 42, 0.25); color: #fff; cursor: pointer; padding: 0; display: inline-flex; align-items: center; justify-content: center; }}
        .map-marker:focus-visible {{ outline: 3px solid #111827; outline-offset: 3px; }}
        .map-marker:hover {{ z-index: 5; transform: translate(-50%, -50%) scale(1.12); }}
        .map-marker-dot {{ display: inline-flex; align-items: center; justify-content: center; width: 100%; height: 100%; font-weight: 700; font-size: 11px; line-height: 1; }}
        .map-marker-empty {{ width: 14px; height: 14px; color: transparent; }}
        .map-marker-medium {{ width: 24px; height: 24px; }}
        .map-marker-large {{ width: 34px; height: 34px; }}
        .map-marker-school-full {{ background: #2563eb; }}
        .map-marker-school-lower {{ background: #16a34a; }}
        .map-marker-school-small {{ background: #d97706; }}
        .map-marker-school-unknown {{ background: #6b7280; }}
        .map-marker-label {{ position: absolute; left: calc(100% + 5px); top: 50%; transform: translateY(-50%); border-radius: 4px; background: rgba(255, 255, 255, 0.92); color: #111827; border: 1px solid #e5e7eb; padding: 2px 5px; font-size: 12px; font-weight: 700; white-space: nowrap; pointer-events: none; }}
        .map-origin {{ position: absolute; transform: translate(-50%, -50%); background: #111827; color: #fff; border: 2px solid #fff; border-radius: 999px; padding: 4px 8px; font-size: 12px; font-weight: 700; box-shadow: 0 2px 8px rgba(15, 23, 42, 0.25); z-index: 4; }}
        .ads-count {{ display: inline-flex; align-items: center; justify-content: center; min-width: 36px; padding: 4px 10px; border-radius: 999px; font-size: 14px; }}
        .ads-count-empty {{ background: #ececec; color: #666; }}
        .ads-count-button {{ border: 0; background: #0f766e; color: #fff; cursor: pointer; }}
        .ads-count-button:hover {{ background: #115e59; }}
        .ads-count-button.ads-count-empty {{ background: #ececec; color: #666; }}
        .ads-count-button.ads-count-empty:hover {{ background: #d7d7d7; }}
        .ads-changes {{ margin: 20px 0; border: 1px solid #d1d5db; border-radius: 8px; overflow: hidden; }}
        .ads-changes-header {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; background: #f9fafb; border-bottom: 1px solid #e5e7eb; }}
        .ads-changes-title {{ font-weight: 700; color: #111827; }}
        .ads-changes-tabs {{ display: flex; flex-wrap: wrap; gap: 6px; }}
        .ads-changes-tab {{ border: 1px solid #d1d5db; border-radius: 999px; background: #fff; color: #374151; padding: 5px 10px; font: inherit; font-size: 13px; cursor: pointer; }}
        .ads-changes-tab-active {{ background: #0f766e; border-color: #0f766e; color: #fff; }}
        .ads-changes-body {{ padding: 8px 16px 12px; }}
        .ads-changes-list {{ display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }}
        .ads-changes-item {{ display: grid; grid-template-columns: minmax(160px, 1fr) auto; gap: 8px 16px; align-items: start; padding: 8px 0; border-bottom: 1px solid #f3f4f6; }}
        .ads-changes-item:last-child {{ border-bottom: 0; }}
        .ads-changes-city {{ color: #0f766e; font-weight: 700; }}
        .ads-changes-name {{ color: #111827; font-weight: 700; }}
        .ads-changes-listing-link {{ display: block; color: #111827; text-decoration: none; }}
        .ads-changes-listing-link:hover {{ color: #0f766e; text-decoration: underline; }}
        .ads-changes-listing-link:focus-visible {{ outline: 2px solid #0f766e; outline-offset: 2px; }}
        .ads-changes-meta {{ color: #6b7280; font-size: 13px; margin-top: 2px; }}
        .ads-changes-price {{ color: #111827; font-weight: 700; white-space: nowrap; }}
        .ads-changes-empty {{ color: #6b7280; margin: 0; }}
        .ads-link-button {{ border: 0; background: transparent; padding: 0; font: inherit; cursor: pointer; text-align: left; }}
        .ads-link-button:hover {{ text-decoration: underline; }}
        .ads-drawer-backdrop {{ position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45); }}
        .ads-drawer {{ position: fixed; top: 0; right: 0; width: min(820px, 100vw); height: 100vh; background: #fff; box-shadow: -10px 0 30px rgba(0, 0, 0, 0.18); transform: translateX(100%); transition: transform 0.2s ease; z-index: 20; display: flex; flex-direction: column; min-height: 0; }}
        .ads-drawer-open {{ transform: translateX(0); }}
        .ads-drawer-header {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 20px 24px 8px; border-bottom: 1px solid #e5e7eb; }}
        .ads-drawer-header h2 {{ margin: 0 0 6px; }}
        .ads-drawer-close {{ border: 0; background: transparent; font-size: 32px; line-height: 1; cursor: pointer; color: #555; }}
        .ads-drawer-meta {{ padding: 12px 24px 0; color: #555; font-size: 14px; }}
        .ads-provider-coverage {{ display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 24px 0; }}
        .ads-provider-chip {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 3px 8px; font-size: 12px; font-weight: 700; line-height: 1.35; }}
        .ads-provider-ok {{ background: #dcfce7; color: #166534; }}
        .ads-provider-empty {{ background: #f3f4f6; color: #4b5563; }}
        .ads-provider-warning {{ background: #fee2e2; color: #991b1b; }}
        .ads-trust-panel {{ margin: 12px 24px 0; border: 1px solid #e5e7eb; border-radius: 8px; background: #f9fafb; padding: 10px 12px; color: #374151; font-size: 13px; line-height: 1.4; }}
        .ads-trust-panel strong {{ color: #111827; }}
        .ads-trust-panel-warning {{ border-color: #fecaca; background: #fff7ed; }}
        .ads-trust-summary {{ margin-bottom: 6px; }}
        .ads-trust-stats {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }}
        .ads-trust-stat {{ border-radius: 999px; background: #fff; border: 1px solid #e5e7eb; padding: 2px 8px; white-space: nowrap; }}
        .ads-trust-details {{ margin: 0; padding-left: 18px; color: #4b5563; }}
        .ads-drawer-controls {{ display: flex; align-items: center; gap: 8px; padding: 12px 24px 0; color: #374151; font-size: 14px; }}
        .ads-drawer-controls select {{ border: 1px solid #d1d5db; border-radius: 6px; background: #fff; color: #111827; padding: 5px 28px 5px 8px; font: inherit; }}
        .ads-drawer-table-wrap {{ flex: 1 1 auto; min-height: 0; overflow: auto; padding: 16px 24px 24px; }}
        .ad-listing-cell {{ min-width: 240px; }}
        .ad-listing-title {{ font-weight: 700; }}
        .ad-listing-location {{ margin-top: 4px; color: #6b7280; font-size: 13px; line-height: 1.35; }}
        .ad-badges {{ display: inline-flex; flex-wrap: wrap; gap: 4px; margin-left: 8px; vertical-align: 1px; }}
        .ad-badge {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 2px 7px; font-size: 11px; font-weight: 700; line-height: 1.3; white-space: nowrap; }}
        .ad-badge-new {{ background: #dcfce7; color: #166534; }}
        .ad-badge-price {{ background: #fef3c7; color: #92400e; }}
        .ads-price-cell {{ white-space: nowrap; }}
        .ads-empty-row {{ color: #6b7280; text-align: center; }}
        @media (max-width: 720px) {{
          body {{ margin: 12px; }}
          th, td {{ padding: 6px; font-size: 14px; }}
          .map-header {{ flex-direction: column; gap: 10px; }}
          .map-canvas {{ height: 360px; }}
          .map-marker-label {{ display: none; }}
          .ads-drawer-header {{ padding: 16px 16px 8px; }}
          .ads-drawer-meta {{ padding: 12px 16px 0; }}
          .ads-provider-coverage {{ padding: 10px 16px 0; }}
          .ads-trust-panel {{ margin: 12px 16px 0; }}
          .ads-drawer-controls {{ padding: 12px 16px 0; }}
          .ads-drawer-table-wrap {{ padding: 16px; }}
          .ads-changes-header {{ align-items: flex-start; flex-direction: column; }}
          .ads-changes-item {{ grid-template-columns: 1fr; }}
          .ads-changes-price {{ white-space: normal; }}
          .ad-listing-cell {{ min-width: 180px; }}
        }}
      </style>
    </head>
    <body>
      <h1>Kde bydlet?</h1>
      <p>Zdroj dat: OpenStreetMap (obce/školy/populace) + OSRM routing. Vygenerováno dne {generated_on}. Záznamů: {len(rows)}.</p>
      <div class="view-switch" role="tablist" aria-label="Zobrazení přehledu">
        <button type="button" class="view-switch-button" id="table-view-button" role="tab" aria-controls="table-view" aria-selected="true">Tabulka</button>
        <button type="button" class="view-switch-button" id="map-view-button" role="tab" aria-controls="map-view" aria-selected="false">Mapa</button>
      </div>
      <section class="ads-changes" id="ads-changes" hidden>
        <div class="ads-changes-header">
          <div>
            <div class="ads-changes-title">Změny v inzerátech</div>
            <p id="ads-changes-summary">Přehled změn od poslední aktualizace.</p>
          </div>
          <div class="ads-changes-tabs" role="tablist" aria-label="Změny v inzerátech">
            <button type="button" class="ads-changes-tab" data-change-filter="new" aria-expanded="false">Nové <span id="ads-changes-count-new">0</span></button>
            <button type="button" class="ads-changes-tab" data-change-filter="price" aria-expanded="false">Změny cen <span id="ads-changes-count-price">0</span></button>
            <button type="button" class="ads-changes-tab" data-change-filter="hidden" aria-expanded="false">Skryté <span id="ads-changes-count-hidden">0</span></button>
          </div>
        </div>
        <div class="ads-changes-body" id="ads-changes-body" hidden></div>
      </section>
      <section class="report-view" id="table-view" role="tabpanel" aria-labelledby="table-view-button">
      <table>
        <thead>
          <tr>
            <th>Město</th>
            <th>Počet obyvatel</th>
            <th>Dojezd z Dobrušky (min)</th>
            <th>Vybavenost</th>
            <th>Počet inzerátů</th>
            <th>Typ školy</th>
            <th>Základní škola</th>
          </tr>
        </thead>
        <tbody>
          {''.join(html_rows)}
        </tbody>
      </table>
      </section>
      <section class="report-view" id="map-view" role="tabpanel" aria-labelledby="map-view-button" hidden>
      {map_section}
      </section>
      {ads_drawer_assets}
      <script>
        (() => {{
          const views = [
            [document.getElementById("table-view-button"), document.getElementById("table-view")],
            [document.getElementById("map-view-button"), document.getElementById("map-view")],
          ];
          views.forEach(([button, view]) => button?.addEventListener("click", () => {{
            views.forEach(([otherButton, otherView]) => {{
              const selected = otherButton === button;
              otherButton.setAttribute("aria-selected", String(selected));
              otherView.hidden = !selected;
            }});
          }}));
        }})();
      </script>
    </body>
    </html>
    """


def write_html(rows: list[dict]) -> None:
    HTML_PATH.write_text(render_html(rows), encoding="utf-8")
