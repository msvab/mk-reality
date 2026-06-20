import importlib.util
import json
from html import escape
from pathlib import Path

SCRIPT_PATH = Path(".codex/skills/find-real-estate-ads/scripts/mmreality_fetch.py")


def load_mmreality_fetch():
    spec = importlib.util.spec_from_file_location("mmreality_fetch", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def detail_html(payload: dict) -> str:
    encoded = escape(json.dumps(payload, ensure_ascii=False), quote=True)
    return f"""
        <meta property="og:title" content="{payload["title"]} | M&M reality">
        <vue-property-detail-favorite-button :property="{encoded}"></vue-property-detail-favorite-button>
    """


def test_mmreality_discovery_fetches_default_categories_and_filters_by_municipality(monkeypatch):
    module = load_mmreality_fetch()
    fetched_urls = []
    result_payload = {
        "offers": [
            {"id": 111, "municipality": "Třebechovice pod Orebem", "title": "Rodinný dům"},
            {"id": 222, "municipality": "Praha", "title": "Rodinný dům"},
        ]
    }
    result_html = f':ssr="{escape(json.dumps(result_payload, ensure_ascii=False), quote=True)}"'
    retained_detail = detail_html(
        {
            "title": "Rodinný dům Třebechovice pod Orebem",
            "originalTitle": "Rodinný dům Třebechovice pod Orebem",
            "municipality": "Třebechovice pod Orebem",
            "district": "Hradec Králové",
            "group": {"name": "Dům"},
            "type": {"name": "Rodinný dům"},
            "price": 8990000,
            "usableArea": 180,
            "parcelArea": 1250,
        }
    )

    def fake_fetch(url):
        fetched_urls.append(url)
        if url in module.DISCOVERY_RESULT_URLS.values():
            return result_html
        if url == "https://www.mmreality.cz/nemovitosti/111/":
            return retained_detail
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr(module, "run_fetch", fake_fetch)

    payload = module.build_output(
        "Třebechovice pod Orebem",
        "municipality_only",
        include_houses=True,
        include_land=True,
        result_urls=[],
        detail_urls=[],
        discover_results=True,
    )

    assert module.DISCOVERY_RESULT_URLS["house"] in fetched_urls
    assert module.DISCOVERY_RESULT_URLS["land"] in fetched_urls
    assert "https://www.mmreality.cz/nemovitosti/111/" in fetched_urls
    assert "https://www.mmreality.cz/nemovitosti/222/" not in fetched_urls
    assert [item["title"] for item in payload["listings"]] == ["Rodinný dům Třebechovice pod Orebem"]
    assert payload["portal_status"]["mmreality.cz"]["status"] == "ok"
    assert ("search_fetch", module.DISCOVERY_RESULT_URLS["house"]) in [
        (attempt["stage"], attempt["url"]) for attempt in payload["fetch_attempts"]
    ]
    assert not any(gap.startswith("outside-municipality-result:") for gap in payload["gaps"])
