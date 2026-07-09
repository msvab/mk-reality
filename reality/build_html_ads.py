from __future__ import annotations

import json
from html import escape

from .paths import REAL_ESTATE_ADS_BY_CITY_PATH


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
        (() => {{
          const dataNode = document.getElementById("ads-by-city-data");
          if (!dataNode) return;
          const adsByCity = JSON.parse(dataNode.textContent || "{{}}");
          window.adsByCityForTest = adsByCity;
          const drawer = document.getElementById("ads-drawer");
          const backdrop = document.getElementById("ads-drawer-backdrop");
          const closeButton = document.getElementById("ads-drawer-close");
          const title = document.getElementById("ads-drawer-title");
          const summary = document.getElementById("ads-drawer-summary");
          const meta = document.getElementById("ads-drawer-meta");
          const providerCoverage = document.getElementById("ads-provider-coverage");
          const body = document.getElementById("ads-drawer-body");
          const sortSelect = document.getElementById("ads-drawer-sort");
          const changesPanel = document.getElementById("ads-changes");
          const changesSummary = document.getElementById("ads-changes-summary");
          const changesBody = document.getElementById("ads-changes-body");
          const changeTabs = Array.from(document.querySelectorAll(".ads-changes-tab"));
          let currentBundle = null;
          let currentChangeFilter = null;

          const escapeHtml = (value) => String(value ?? "—")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");

          const displayValue = (value) => {{
            if (Array.isArray(value)) {{
              const joined = value.filter(Boolean).join(", ");
              return joined || "—";
            }}
            if (value === null || value === undefined || value === "") return "—";
            return String(value);
          }};

          const renderLinks = (urls) => {{
            if (!Array.isArray(urls) || urls.length === 0) return "—";
            return urls.map((url) => {{
              try {{
                const host = new URL(url).host || "odkaz";
                return `<a href="${{escapeHtml(url)}}" target="_blank" rel="noopener noreferrer">${{escapeHtml(host)}}</a>`;
              }} catch (_err) {{
                return "";
              }}
            }}).filter(Boolean).join("<br>");
          }};

          const datePart = (value) => String(value || "").slice(0, 10);
          const formatTimestamp = (value) => {{
            const match = String(value || "").match(/^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})T(\\d{{2}}):(\\d{{2}})/);
            if (!match) return displayValue(value);
            return `${{Number(match[3])}}. ${{Number(match[2])}}. ${{match[1]}} ${{match[4]}}:${{match[5]}}`;
          }};
          const numericValue = (value) => Number.isFinite(Number(value)) ? Number(value) : null;
          const normalizeIdentityPart = (value) => String(value || "")
            .toLowerCase()
            .normalize("NFD")
            .replace(/[\\u0300-\\u036f]/g, "")
            .replace(/\\s+/g, " ")
            .trim();
          const adIdentityKeys = (ad) => {{
            const keys = [];
            for (const url of (Array.isArray(ad.urls) ? ad.urls : [])) {{
              if (url) keys.push(`url:${{String(url).split("?")[0].split("#")[0]}}`);
            }}
            keys.push([
              "shape",
              normalizeIdentityPart(ad.title),
              normalizeIdentityPart(ad.location),
              normalizeIdentityPart(ad.property_type),
              displayValue(ad.land_area_m2),
              displayValue(ad.house_area_m2),
            ].join("|"));
            return keys;
          }};
          const priceValues = (ad) => (Array.isArray(ad.price_history) ? ad.price_history : [])
            .map((entry) => numericValue(entry?.price_czk))
            .filter((value) => value !== null);
          const hasPriceChanged = (ad) => new Set(priceValues(ad)).size > 1;
          const isNewListing = (ad, bundle) => {{
            const generatedDate = datePart(bundle?.generated_at);
            return generatedDate && datePart(ad.first_seen_at) === generatedDate;
          }};
          const isHiddenListing = (ad, bundle) => {{
            const generatedDate = datePart(bundle?.generated_at);
            return generatedDate && datePart(ad.hidden_at) === generatedDate;
          }};
          const latestPriceHistory = (ad) => {{
            const history = Array.isArray(ad.price_history) ? ad.price_history : [];
            if (history.length < 2) return null;
            const previous = history[history.length - 2];
            const current = history[history.length - 1];
            if (numericValue(previous?.price_czk) === numericValue(current?.price_czk)) return null;
            return {{ previous, current }};
          }};
          const formatCzk = (value) => {{
            const number = numericValue(value);
            if (number === null) return "—";
            return `${{number.toLocaleString("cs-CZ")}} Kč`;
          }};
          const formatPriceChange = (priceChange) => {{
            if (!priceChange) return null;
            const previousText = displayValue(priceChange.previous?.price);
            const currentText = displayValue(priceChange.current?.price);
            if (previousText !== currentText) return `${{previousText}} → ${{currentText}}`;
            return `${{formatCzk(priceChange.previous?.price_czk)}} → ${{formatCzk(priceChange.current?.price_czk)}}`;
          }};

          const renderBadges = (ad, bundle) => {{
            const badges = [];
            if (isNewListing(ad, bundle)) {{
              badges.push('<span class="ad-badge ad-badge-new">Nové</span>');
            }}
            if (hasPriceChanged(ad)) {{
              badges.push('<span class="ad-badge ad-badge-price">Změna ceny</span>');
            }}
            return badges.length ? `<div class="ad-badges">${{badges.join("")}}</div>` : "";
          }};

          const changeRows = () => {{
            const rows = {{ new: [], price: [], hidden: [] }};
            for (const [city, bundle] of Object.entries(adsByCity)) {{
              const hiddenKeys = new Set();
              for (const ad of (bundle.hidden_ads || [])) {{
                if (!isHiddenListing(ad, bundle)) continue;
                rows.hidden.push({{ city, ad, bundle }});
                adIdentityKeys(ad).forEach((key) => hiddenKeys.add(key));
              }}
              for (const ad of (bundle.ads || [])) {{
                if (isNewListing(ad, bundle)) rows.new.push({{ city, ad, bundle }});
                if (hasPriceChanged(ad) && !adIdentityKeys(ad).some((key) => hiddenKeys.has(key))) {{
                  rows.price.push({{ city, ad, bundle, priceChange: latestPriceHistory(ad) }});
                }}
              }}
            }}
            rows.new.sort((a, b) => String(b.ad.first_seen_at || "").localeCompare(String(a.ad.first_seen_at || "")));
            rows.price.sort((a, b) => String(b.priceChange?.current?.seen_at || "").localeCompare(String(a.priceChange?.current?.seen_at || "")));
            rows.hidden.sort((a, b) => String(b.ad.hidden_at || "").localeCompare(String(a.ad.hidden_at || "")));
            return rows;
          }};

          const renderChangeRows = (rows, filter) => {{
            if (!changesBody) return;
            const labels = {{ new: "nových", price: "se změnou ceny", hidden: "skrytých" }};
            changesSummary.textContent = `${{rows[filter].length}} ${{labels[filter]}} inzerátů.`;
            if (rows[filter].length === 0) {{
              changesBody.innerHTML = '<p class="ads-changes-empty">Žádné změny v této kategorii.</p>';
              return;
            }}
            changesBody.innerHTML = `
              <ul class="ads-changes-list">
                ${{rows[filter].slice(0, 20).map((item) => {{
                  const priceChange = item.priceChange;
                  const priceText = formatPriceChange(priceChange) || displayValue(item.ad.price);
                  const timestamp = filter === "hidden" ? item.ad.hidden_at : (priceChange?.current?.seen_at || item.ad.first_seen_at || item.bundle.generated_at);
                  return `
                    <li class="ads-changes-item">
                      <div>
                        <button type="button" class="ads-changes-city ads-link-button" data-change-city="${{escapeHtml(item.city)}}">${{escapeHtml(item.city)}}</button>
                        <div class="ads-changes-name">${{escapeHtml(displayValue(item.ad.title))}}</div>
                        <div class="ads-changes-meta">${{escapeHtml(formatTimestamp(timestamp))}} · ${{escapeHtml(displayValue(item.ad.location))}}</div>
                      </div>
                      <div class="ads-changes-price">${{escapeHtml(priceText)}}</div>
                    </li>
                  `;
                }}).join("")}}
              </ul>
            `;
          }};

          const renderChangesPanel = () => {{
            if (!changesPanel || !changesBody) return;
            const rows = changeRows();
            const total = rows.new.length + rows.price.length + rows.hidden.length;
            document.getElementById("ads-changes-count-new").textContent = rows.new.length;
            document.getElementById("ads-changes-count-price").textContent = rows.price.length;
            document.getElementById("ads-changes-count-hidden").textContent = rows.hidden.length;
            changesPanel.hidden = false;
            changesBody.hidden = true;
            changesSummary.textContent = total
              ? "Kliknutím na štítek zobrazíte detail změn."
              : "Žádné změny od poslední aktualizace.";
            changeTabs.forEach((tab) => {{
              tab.classList.remove("ads-changes-tab-active");
              tab.setAttribute("aria-expanded", "false");
              tab.onclick = () => {{
                const nextFilter = tab.dataset.changeFilter || "new";
                const collapse = currentChangeFilter === nextFilter && !changesBody.hidden;
                currentChangeFilter = collapse ? null : nextFilter;
                changesBody.hidden = collapse;
                changeTabs.forEach((item) => {{
                  const active = !collapse && item === tab;
                  item.classList.toggle("ads-changes-tab-active", active);
                  item.setAttribute("aria-expanded", active ? "true" : "false");
                }});
                if (collapse) {{
                  changesSummary.textContent = total
                    ? "Kliknutím na štítek zobrazíte detail změn."
                    : "Žádné změny od poslední aktualizace.";
                  changesBody.innerHTML = "";
                }} else {{
                  renderChangeRows(rows, currentChangeFilter);
                }}
              }};
            }});
            changesBody.addEventListener("click", (event) => {{
              const button = event.target.closest("[data-change-city]");
              if (button) openDrawer(button.dataset.changeCity);
            }});
          }};

          const portalLabels = {{
            "reality.idnes.cz": "iDNES",
            "realitymix.cz": "RealityMix",
            "reality.aktualne.cz": "Aktuálně",
            "sreality.cz": "Sreality",
          }};

          const statusLabels = {{
            ok: "OK",
            no_results: "bez výsledků",
            rate_limited: "limit",
            blocked: "blokováno",
            fetch_error: "chyba",
            fallback_page: "fallback",
          }};

          const statusClass = (status) => {{
            if (status === "ok") return "ads-provider-ok";
            if (status === "no_results") return "ads-provider-empty";
            return "ads-provider-warning";
          }};

          const renderProviderCoverage = (bundle) => {{
            const statuses = bundle?.portal_status || {{}};
            const portals = Object.keys(portalLabels);
            const chips = portals.map((portal) => {{
              const status = statuses[portal]?.status || "unknown";
              const label = portalLabels[portal];
              const text = statusLabels[status] || status;
              const title = statuses[portal]?.message || "";
              return `<span class="ads-provider-chip ${{statusClass(status)}}" title="${{escapeHtml(title)}}">${{escapeHtml(label)}}: ${{escapeHtml(text)}}</span>`;
            }});
            providerCoverage.innerHTML = chips.join("");
          }};

          const renderListingCell = (ad, bundle) => {{
            const title = escapeHtml(displayValue(ad.title));
            const location = escapeHtml(displayValue(ad.location));
            return `
              <div class="ad-listing-cell">
                <div class="ad-listing-title">${{title}}${{renderBadges(ad, bundle)}}</div>
                <div class="ad-listing-location">${{location}}</div>
              </div>
            `;
          }};

          const compareNumbers = (left, right, direction) => {{
            const leftValue = numericValue(left);
            const rightValue = numericValue(right);
            if (leftValue === null && rightValue === null) return 0;
            if (leftValue === null) return 1;
            if (rightValue === null) return -1;
            return direction === "asc" ? leftValue - rightValue : rightValue - leftValue;
          }};

          const sortedAds = (bundle) => {{
            const ads = [...(bundle?.ads || [])];
            switch (sortSelect?.value) {{
              case "price-desc":
                return ads.sort((a, b) => compareNumbers(a.price_czk, b.price_czk, "desc"));
              case "price-asc":
                return ads.sort((a, b) => compareNumbers(a.price_czk, b.price_czk, "asc"));
              case "land-desc":
                return ads.sort((a, b) => compareNumbers(a.land_area_m2, b.land_area_m2, "desc"));
              case "land-asc":
                return ads.sort((a, b) => compareNumbers(a.land_area_m2, b.land_area_m2, "asc"));
              case "newest":
                return ads.sort((a, b) => String(b.first_seen_at || "").localeCompare(String(a.first_seen_at || "")));
              default:
                return ads;
            }}
          }};

          const renderRows = (bundle) => {{
            body.innerHTML = "";
            const ads = sortedAds(bundle);
            if (ads.length === 0) {{
              body.innerHTML = '<tr><td colspan="6" class="ads-empty-row">Žádné inzeráty po ověření portálů.</td></tr>';
              return;
            }}
            for (const ad of ads) {{
              const row = document.createElement("tr");
              const cells = [
                {{ html: renderListingCell(ad, bundle) }},
                {{ html: escapeHtml(displayValue(ad.property_type)) }},
                {{ html: `<span class="ads-price-cell">${{escapeHtml(displayValue(ad.price))}}</span>` }},
                {{ html: escapeHtml(displayValue(ad.house_area_m2)) }},
                {{ html: escapeHtml(displayValue(ad.land_area_m2)) }},
                {{ html: renderLinks(ad.urls) }},
              ];
              row.innerHTML = cells.map((cell) => `<td>${{cell.html}}</td>`).join("");
              body.appendChild(row);
            }}
          }};

          const closeDrawer = () => {{
            drawer.setAttribute("aria-hidden", "true");
            drawer.classList.remove("ads-drawer-open");
            backdrop.hidden = true;
          }};

          const openDrawer = (city) => {{
            const bundle = adsByCity[city];
            if (!bundle) return;
            currentBundle = bundle;
            if (sortSelect) sortSelect.value = "default";
            title.textContent = `Inzeráty: ${{city}}`;
            summary.textContent = `Počet inzerátů: ${{bundle.count ?? 0}}`;
            const workers = bundle.coverage || {{}};
            const metaParts = [];
            if (workers.workers_with_results !== undefined && workers.workers_launched !== undefined) {{
              metaParts.push(`Portály s výsledky: ${{workers.workers_with_results}}/${{workers.workers_launched}}`);
            }}
            if (bundle.generated_at) {{
              metaParts.push(`Aktualizováno: ${{formatTimestamp(bundle.generated_at)}}`);
            }}
            meta.textContent = metaParts.join(" | ");
            renderProviderCoverage(bundle);
            renderRows(bundle);

            drawer.setAttribute("aria-hidden", "false");
            drawer.classList.add("ads-drawer-open");
            backdrop.hidden = false;
          }};

          document.querySelectorAll(".ads-count-button").forEach((button) => {{
            button.addEventListener("click", () => openDrawer(button.dataset.city));
          }});
          sortSelect?.addEventListener("change", () => {{
            if (currentBundle) renderRows(currentBundle);
          }});
          closeButton?.addEventListener("click", closeDrawer);
          backdrop?.addEventListener("click", closeDrawer);
          document.addEventListener("keydown", (event) => {{
            if (event.key === "Escape" && drawer.classList.contains("ads-drawer-open")) {{
              closeDrawer();
            }}
          }});
          window.renderAdsChangesPanelForTest = renderChangesPanel;
          renderChangesPanel();
        }})();
      </script>
    """

