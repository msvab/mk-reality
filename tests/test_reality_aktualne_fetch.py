import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(".codex/skills/find-real-estate-ads/scripts/reality_aktualne_fetch.py")


def load_reality_aktualne_fetch():
    spec = importlib.util.spec_from_file_location("reality_aktualne_fetch", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_removed_detail_404_is_non_blocking():
    module = load_reality_aktualne_fetch()

    assert module.is_removed_detail_fetch(
        "https://reality.aktualne.cz/detail/testov/prodej-domu-testov-123.html",
        "detail_fetch",
        404,
    )
    assert module.is_removed_detail_fetch(
        "https://reality.aktualne.cz/detail/testov/prodej-domu-testov-123.html",
        "discovery_detail_fetch",
        404,
    )


def test_search_404_is_not_treated_as_removed_detail():
    module = load_reality_aktualne_fetch()

    assert not module.is_removed_detail_fetch(
        "https://reality.aktualne.cz/vyhledavani/r-3605-nachod/kralovehradecky/prodej-domy_vily.html",
        "search_fetch",
        404,
    )
    assert module.classify_fetch(404, 0, "<html>missing</html>", "") == ("blocked", "HTTP 404")


def test_municipality_search_url_uses_vypis_nabidek_endpoint():
    module = load_reality_aktualne_fetch()

    assert module.municipality_search_url("Deštné v Orlických horách") == (
        "https://reality.aktualne.cz/vypis-nabidek/"
        "?form%5Bsearch_in_city%5D=De%C5%A1tn%C3%A9+v+Orlick%C3%BDch+hor%C3%A1ch"
        "&form%5Bcena_mena%5D=1"
    )


def test_rd_title_is_normalized_to_house():
    module = load_reality_aktualne_fetch()

    assert module.infer_property_type("Prodej RD/penzionu, 3195 m2", {}) == ("house", None)


def test_parcel_area_can_be_extracted_from_description_text():
    module = load_reality_aktualne_fetch()

    assert module.extract_area_after_keywords(
        "užitná plocha 590 m2, pozemek 3 195 m2. Smíšená konstrukce.",
        ["pozemek", "pozemku", "parcela", "parcely"],
    ) == 3195


def test_homonymous_municipality_with_wrong_district_is_excluded():
    module = load_reality_aktualne_fetch()

    assert not module.municipality_matches(
        "Všestary",
        "https://reality.aktualne.cz/detail/vsestary/prodej-rodinneho-domu-280-m2-obloukova-vsestary-8616311.html",
        "Prodej rodinného domu 280 m2 Oblouková, Všestary",
        "",
        "Oblouková, Všestary, Praha-východ",
        "Hradec Králové",
    )


def test_homonymous_municipality_with_expected_district_is_retained():
    module = load_reality_aktualne_fetch()

    assert module.municipality_matches(
        "Všestary",
        "https://reality.aktualne.cz/detail/vsestary/prodej-pozemku-vsestary-123.html",
        "Prodej pozemku Všestary",
        "",
        "Rosnice, Všestary, Hradec Králové",
        "Hradec Králové",
    )


def test_expected_district_is_loaded_from_overpass_metadata():
    module = load_reality_aktualne_fetch()

    assert module.expected_district_for_municipality("Všestary") == "Hradec Králové"
