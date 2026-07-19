from __future__ import annotations

import json
from html import escape

from .paths import ADS_DRAWER_JS_PATH, REAL_ESTATE_ADS_BY_CITY_PATH


def load_real_estate_ads_by_city() -> dict | None:
    if not REAL_ESTATE_ADS_BY_CITY_PATH.exists():
        return None
    try:
        payload = json.loads(REAL_ESTATE_ADS_BY_CITY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"Skipping real estate ads feed: invalid JSON in {REAL_ESTATE_ADS_BY_CITY_PATH}: {e}", flush=True)
        return None
    if not isinstance(payload, dict):
        print(f"Skipping real estate ads feed: expected object in {REAL_ESTATE_ADS_BY_CITY_PATH}", flush=True)
        return None
    cities = payload.get("cities", {})
    if not isinstance(cities, dict):
        print(f"Skipping real estate ads feed: expected 'cities' object in {REAL_ESTATE_ADS_BY_CITY_PATH}", flush=True)
        return None
    payload["cities"] = cities
    return payload


def _display_join(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if str(item).strip()) or "—"
    text = str(value).strip() if value is not None else ""
    return text or "—"


def _json_script_payload(value) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def _drawer_js_payload() -> str:
    js = ADS_DRAWER_JS_PATH.read_text(encoding="utf-8").replace("</script", "<\\/script")
    return "".join(f"        {line}\n" if line else "\n" for line in js.splitlines())


def city_ads_bundle(feed: dict | None, city: str) -> dict:
    if not feed:
        return {"count": 0, "ads": [], "coverage": {}, "portal_status": {}, "assumptions": [], "gaps": []}
    cities = feed.get("cities", {})
    bundle = cities.get(city, {})
    if not isinstance(bundle, dict):
        return {"count": 0, "ads": [], "coverage": {}, "portal_status": {}, "assumptions": [], "gaps": []}
    ads = bundle.get("ads", [])
    if not isinstance(ads, list):
        ads = []
    hidden_ads = bundle.get("hidden_ads", [])
    if not isinstance(hidden_ads, list):
        hidden_ads = []
    coverage = bundle.get("coverage", {})
    if not isinstance(coverage, dict):
        coverage = {}
    portal_status = bundle.get("portal_status", {})
    if not isinstance(portal_status, dict):
        portal_status = {}
    assumptions = bundle.get("assumptions", [])
    if not isinstance(assumptions, list):
        assumptions = []
    gaps = bundle.get("gaps", [])
    if not isinstance(gaps, list):
        gaps = []
    count = bundle.get("count", len(ads))
    if not isinstance(count, int):
        count = len(ads)
    return {
        "count": count,
        "ads": ads,
        "hidden_ads": hidden_ads,
        "coverage": coverage,
        "portal_status": portal_status,
        "assumptions": assumptions,
        "gaps": gaps,
    }


def render_ads_count_cell(city: str, feed: dict | None) -> str:
    bundle = city_ads_bundle(feed, city)
    count = bundle["count"]
    if not feed:
        return '<span class="ads-count ads-count-empty">0</span>'
    city_attr = escape(city, quote=True)
    empty_class = " ads-count-empty" if count <= 0 else ""
    return f'<button type="button" class="ads-count ads-count-button{empty_class}" data-city="{city_attr}">{count}</button>'


def render_ads_drawer_assets(feed: dict | None) -> str:
    if not feed:
        return ""
    payload = {}
    for city, bundle in feed.get("cities", {}).items():
        if not isinstance(bundle, dict):
            continue
        payload[city] = city_ads_bundle(feed, city)
        payload[city]["generated_at"] = feed.get("generated_at")
    return f"""
      <div class="ads-drawer-backdrop" id="ads-drawer-backdrop" hidden></div>
      <aside class="ads-drawer" id="ads-drawer" aria-hidden="true">
        <div class="ads-drawer-header">
          <div>
            <h2 id="ads-drawer-title">Inzeráty</h2>
            <p id="ads-drawer-summary">Vyberte obec.</p>
          </div>
          <button type="button" class="ads-drawer-close" id="ads-drawer-close" aria-label="Zavřít">×</button>
        </div>
        <div class="ads-drawer-meta" id="ads-drawer-meta"></div>
        <div class="ads-provider-coverage" id="ads-provider-coverage"></div>
        <div class="ads-trust-panel" id="ads-trust-panel"></div>
        <div class="ads-drawer-controls">
          <label for="ads-drawer-sort">Řazení</label>
          <select id="ads-drawer-sort">
            <option value="default">Výchozí</option>
            <option value="price-desc">Cena sestupně</option>
            <option value="price-asc">Cena vzestupně</option>
            <option value="land-desc">Pozemek sestupně</option>
            <option value="land-asc">Pozemek vzestupně</option>
            <option value="newest">Nejnovější</option>
          </select>
        </div>
        <div class="ads-drawer-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Nabídka</th>
                <th>Typ</th>
                <th>Cena</th>
                <th>Dům m2</th>
                <th>Pozemek m2</th>
                <th>Odkazy</th>
              </tr>
            </thead>
            <tbody id="ads-drawer-body"></tbody>
          </table>
        </div>
      </aside>
      <script id="ads-by-city-data" type="application/json">{_json_script_payload(payload)}</script>
      <script>
{_drawer_js_payload()}      </script>
    """
