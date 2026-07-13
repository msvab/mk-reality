from __future__ import annotations

import json
import time
from html import escape
from pathlib import Path

from .build_html_ads import load_real_estate_ads_by_city, render_ads_count_cell, render_ads_drawer_assets
from .build_html_urls import safe_href
from .paths import HTML_PATH, SCHOOLS_JSON_PATH


def load_cached_school_rows(path: Path = SCHOOLS_JSON_PATH) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist; run a full python -m reality.build_html first.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON array.")
    return [row for row in payload if isinstance(row, dict)]


def render_html(rows: list[dict]) -> str:
    ads_by_city = load_real_estate_ads_by_city()
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
          .ads-drawer-header {{ padding: 16px 16px 8px; }}
          .ads-drawer-meta {{ padding: 12px 16px 0; }}
          .ads-provider-coverage {{ padding: 10px 16px 0; }}
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
      {ads_drawer_assets}
    </body>
    </html>
    """


def write_html(rows: list[dict]) -> None:
    HTML_PATH.write_text(render_html(rows), encoding="utf-8")
