import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(".codex/skills/find-real-estate-ads/scripts/reality_idnes_fetch.py")


def load_reality_idnes_fetch():
    spec = importlib.util.spec_from_file_location("reality_idnes_fetch", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_idnes_land_detail_is_retained_from_metadata():
    module = load_reality_idnes_fetch()
    html = """
        <meta name="cXenseParse:qiw-reaCategory" content="Pozemek/Stavební pozemek">
        <meta name="cXenseParse:qiw-reaCity" content="Librantice">
        <meta name="cXenseParse:qiw-reaDistrict" content="Hradec Králové">
        <meta property="og:title" content="Prodej zasíťovaného stavebního pozemku, 1150 m2, Librantice, okr. Hradec Králové">
        <meta property="og:description" content="Prodej stavebního pozemku 1 150 m², Librantice, okres Hradec Králové.">
        <p class="b-detail__price"><strong>5&nbsp;980&nbsp;000&nbsp;Kč</strong></p>
        <script>
          dataLayer.push({
            "listing_price":5980000,
            "listing_category":"Pozemek/Stavební pozemek",
            "listing_localityCity":"Librantice",
            "listing_localityDistrict":"Hradec Králové",
            "listing_area":null,
            "listing_landArea":1150
          });
        </script>
    """

    listing, reason = module.listing_from_detail(
        "https://reality.idnes.cz/detail/prodej/pozemek/librantice/6915d21cf78ea8ee7a08c865/",
        html,
        "Librantice",
    )

    assert reason is None
    assert listing["property_type"] == "land"
    assert listing["price"] == "5 980 000 Kč"
    assert listing["land_area_m2"] == "1150"
    assert listing["notes"] == ["detail-url-verified:reality.idnes.cz", "buildable-land"]


def test_idnes_outside_municipality_is_excluded():
    module = load_reality_idnes_fetch()
    html = """
        <meta name="cXenseParse:qiw-reaCategory" content="Pozemek/Stavební pozemek">
        <meta name="cXenseParse:qiw-reaCity" content="Hradec Králové">
        <meta property="og:title" content="Prodej stavebního pozemku 1 150 m²">
    """

    listing, reason = module.listing_from_detail(
        "https://reality.idnes.cz/detail/prodej/pozemek/hradec-kralove/example/",
        html,
        "Librantice",
    )

    assert listing is None
    assert reason == "outside-municipality"


def test_idnes_result_detail_urls_are_extracted_and_canonicalized():
    module = load_reality_idnes_fetch()
    html = """
        <a href="https://reality.idnes.cz/detail/prodej/dum/librantice/abc">Detail</a>
        <a href="/detail/prodej/pozemek/librantice/def/?utm=ignored">Detail</a>
        <a href="/detail/pronajem/dum/librantice/ignored/">Ignored</a>
    """

    assert module.extract_detail_urls(html) == [
        "https://reality.idnes.cz/detail/prodej/dum/librantice/abc/",
        "https://reality.idnes.cz/detail/prodej/pozemek/librantice/def/",
    ]


def test_idnes_discovery_uses_locality_id_from_cached_detail(monkeypatch):
    module = load_reality_idnes_fetch()
    fetched_urls = []
    cached_detail_url = "https://reality.idnes.cz/detail/prodej/pozemek/librantice/cached/"
    new_detail_url = "https://reality.idnes.cz/detail/prodej/dum/librantice/new/"
    detail_html = """
        <meta name="cXenseParse:qiw-reaCategory" content="Pozemek/Stavební pozemek">
        <meta name="cXenseParse:qiw-reaCity" content="Librantice">
        <meta name="cXenseParse:qiw-reaDistrict" content="Hradec Králové">
        <meta property="og:title" content="Prodej stavebního pozemku 1 150 m²">
        <meta property="og:description" content="Prodej stavebního pozemku 1 150 m².">
        <a href="/s/prodej/pozemky/?s-l=CAST_OBCE-83488">Librantice</a>
        <script>
          dataLayer.push({
            "listing_price":5980000,
            "listing_localityCity":"Librantice",
            "listing_landArea":1150
          });
        </script>
    """
    new_detail_html = """
        <meta name="cXenseParse:qiw-reaCategory" content="Dům/Rodinný dům">
        <meta name="cXenseParse:qiw-reaCity" content="Librantice">
        <meta name="cXenseParse:qiw-reaDistrict" content="Hradec Králové">
        <meta property="og:title" content="Prodej rodinného domu, Librantice">
        <meta property="og:description" content="Rodinný dům s pozemkem 1 250 m².">
        <script>
          dataLayer.push({
            "listing_price":8990000,
            "listing_localityCity":"Librantice",
            "listing_area":180,
            "listing_landArea":1250
          });
        </script>
    """
    result_html = f'<a href="{new_detail_url}">New detail</a>'

    def fake_fetch(url, *, attempts=None, stage="fetch"):
        fetched_urls.append((stage, url))
        if url == cached_detail_url:
            return detail_html
        if url == new_detail_url:
            return new_detail_html
        if url in {
            "https://reality.idnes.cz/s/prodej/domy/?s-l=CAST_OBCE-83488",
            "https://reality.idnes.cz/s/prodej/pozemky/?s-l=CAST_OBCE-83488",
        }:
            return result_html
        raise AssertionError(f"unexpected fetch: {stage} {url}")

    monkeypatch.setattr(module, "run_fetch", fake_fetch)

    payload = module.build_output("Librantice", "municipality_only", [cached_detail_url], discover_results=True)

    assert [item["title"] for item in payload["listings"]] == [
        "Prodej rodinného domu, Librantice",
        "Prodej stavebního pozemku 1 150 m²",
    ]
    assert ("search_fetch", "https://reality.idnes.cz/s/prodej/domy/?s-l=CAST_OBCE-83488") in fetched_urls
    assert ("search_fetch", "https://reality.idnes.cz/s/prodej/pozemky/?s-l=CAST_OBCE-83488") in fetched_urls
    assert payload["coverage"]["candidates_gathered"] == 2


def test_idnes_discovery_uses_autocomplete_without_cached_details(monkeypatch):
    module = load_reality_idnes_fetch()
    fetched_urls = []
    new_detail_url = "https://reality.idnes.cz/detail/prodej/dum/trebechovice-pod-orebem/new/"
    autocomplete_html = """
        [{"label":"Třebechovice pod Orebem","value":"CAST_OBCE-12345"}]
    """
    result_html = f'<a href="{new_detail_url}">New detail</a>'
    detail_html = """
        <meta name="cXenseParse:qiw-reaCategory" content="Dům/Rodinný dům">
        <meta name="cXenseParse:qiw-reaCity" content="Třebechovice pod Orebem">
        <meta name="cXenseParse:qiw-reaDistrict" content="Hradec Králové">
        <meta property="og:title" content="Prodej rodinného domu, Třebechovice pod Orebem">
        <meta property="og:description" content="Rodinný dům s pozemkem 1 250 m².">
        <script>
          dataLayer.push({
            "listing_price":8990000,
            "listing_localityCity":"Třebechovice pod Orebem",
            "listing_area":180,
            "listing_landArea":1250
          });
        </script>
    """

    def fake_fetch(url, *, attempts=None, stage="fetch"):
        fetched_urls.append((stage, url))
        if stage == "locality_autocomplete_fetch":
            return autocomplete_html
        if url in {
            "https://reality.idnes.cz/s/prodej/domy/?s-l=CAST_OBCE-12345",
            "https://reality.idnes.cz/s/prodej/pozemky/?s-l=CAST_OBCE-12345",
        }:
            return result_html
        if url == new_detail_url:
            return detail_html
        raise AssertionError(f"unexpected fetch: {stage} {url}")

    monkeypatch.setattr(module, "run_fetch", fake_fetch)

    payload = module.build_output("Třebechovice pod Orebem", "municipality_only", [], discover_results=True)

    assert [item["title"] for item in payload["listings"]] == ["Prodej rodinného domu, Třebechovice pod Orebem"]
    assert ("locality_autocomplete_fetch", module.AUTOCOMPLETE_LOCALITY_URL.format(query="T%C5%99ebechovice+pod+Orebem")) in fetched_urls
    assert ("search_fetch", "https://reality.idnes.cz/s/prodej/domy/?s-l=CAST_OBCE-12345") in fetched_urls
