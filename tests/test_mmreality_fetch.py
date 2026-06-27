import importlib.util
import json
import subprocess
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

    def fake_json_request(url, payload=None):
        return {"items": []}

    monkeypatch.setattr(module, "run_fetch", fake_fetch)
    monkeypatch.setattr(module, "run_json_request", fake_json_request)

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


def test_mmreality_discovery_uses_municipality_api_candidates(monkeypatch):
    module = load_mmreality_fetch()
    fetched_urls = []
    api_payloads = []
    empty_result_html = f':ssr="{escape(json.dumps({"offers": []}), quote=True)}"'
    retained_detail = detail_html(
        {
            "title": "Prodej, Rodinný dům, 590 m², Deštné v Orlických horách",
            "originalTitle": "Prodej RD/penzionu, 3195 m², Deštné v Orlických horách",
            "municipality": "Deštné v Orlických horách",
            "municipalityPart": "Deštné v Orlických horách",
            "district": "Rychnov nad Kněžnou",
            "group": {"name": "Dům"},
            "type": {"name": "Rodinný dům"},
            "price": 18500000,
            "usableArea": 590,
            "parcelArea": 3195,
        }
    )

    def fake_fetch(url):
        fetched_urls.append(url)
        if url in module.DISCOVERY_RESULT_URLS.values():
            return empty_result_html
        if url == "https://www.mmreality.cz/nemovitosti/944560/":
            return retained_detail
        raise AssertionError(f"unexpected fetch: {url}")

    def fake_json_request(url, payload=None):
        api_payloads.append((url, payload))
        if url.startswith(module.LOCATION_SEARCH_URL):
            return {
                "items": [
                    {
                        "id": 300000000576247,
                        "name": "Deštné v Orlických horách",
                        "type": "municipality",
                        "source": 576247,
                    }
                ]
            }
        if url == module.OFFERS_QUERY_URL:
            return {
                "offers": [
                    {
                        "id": 944560,
                        "municipality": "Deštné v Orlických horách",
                        "title": "Prodej, Rodinný dům, 590 m², Deštné v Orlických horách",
                    }
                ]
            }
        raise AssertionError(f"unexpected API request: {url}")

    monkeypatch.setattr(module, "run_fetch", fake_fetch)
    monkeypatch.setattr(module, "run_json_request", fake_json_request)

    payload = module.build_output(
        "Deštné v Orlických horách",
        "municipality_only",
        include_houses=True,
        include_land=True,
        result_urls=[],
        detail_urls=[],
        discover_results=True,
    )

    assert "https://www.mmreality.cz/nemovitosti/944560/" in fetched_urls
    assert any(item[0] == module.OFFERS_QUERY_URL for item in api_payloads)
    assert payload["listings"] == [
        {
            "portal": ["mmreality.cz"],
            "title": "Prodej, Rodinný dům, 590 m², Deštné v Orlických horách",
            "location": "Deštné v Orlických horách, okres Rychnov nad Kněžnou",
            "property_type": "house",
            "price": "18 500 000 Kč",
            "house_area_m2": "590",
            "land_area_m2": "3195",
            "urls": ["https://www.mmreality.cz/nemovitosti/944560/"],
            "notes": ["detail-url-verified:mmreality.cz"],
        }
    ]


def test_mmreality_fetch_records_http_fallback_status(monkeypatch):
    module = load_mmreality_fetch()

    def fake_run(cmd, check, capture_output, text):
        assert check is False
        return subprocess.CompletedProcess(cmd, 0, stdout="<html>not found</html>\n__HTTP_STATUS__:404", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    payload = module.build_output(
        "Třebechovice pod Orebem",
        "municipality_only",
        include_houses=True,
        include_land=True,
        result_urls=["https://www.mmreality.cz/nemovitosti/prodej/rodinne-domy/"],
        detail_urls=[],
    )

    status = payload["portal_status"]["mmreality.cz"]
    assert status["status"] == "fallback_page"
    assert status["http_status"] == 404
    assert status["stage"] == "search_fetch"
    assert payload["fetch_attempts"][0]["status"] == "fallback_page"
    assert payload["fetch_attempts"][0]["http_status"] == 404


def test_mmreality_fetch_records_http_403_as_blocked(monkeypatch):
    module = load_mmreality_fetch()

    def fake_run(cmd, check, capture_output, text):
        assert check is False
        return subprocess.CompletedProcess(cmd, 0, stdout="Forbidden\n__HTTP_STATUS__:403", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    payload = module.build_output(
        "Třebechovice pod Orebem",
        "municipality_only",
        include_houses=True,
        include_land=True,
        result_urls=["https://www.mmreality.cz/nemovitosti/prodej/rodinne-domy/"],
        detail_urls=[],
    )

    status = payload["portal_status"]["mmreality.cz"]
    assert status["status"] == "blocked"
    assert status["http_status"] == 403
    assert status["stage"] == "search_fetch"


def test_mmreality_fetch_records_rate_limit_status(monkeypatch):
    module = load_mmreality_fetch()

    def fake_run(cmd, check, capture_output, text):
        return subprocess.CompletedProcess(cmd, 0, stdout="Too many requests\n__HTTP_STATUS__:429", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    payload = module.build_output(
        "Třebechovice pod Orebem",
        "municipality_only",
        include_houses=True,
        include_land=True,
        result_urls=["https://www.mmreality.cz/nemovitosti/prodej/rodinne-domy/"],
        detail_urls=[],
    )

    status = payload["portal_status"]["mmreality.cz"]
    assert status["status"] == "rate_limited"
    assert status["http_status"] == 429
