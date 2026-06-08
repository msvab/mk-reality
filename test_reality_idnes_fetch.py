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
