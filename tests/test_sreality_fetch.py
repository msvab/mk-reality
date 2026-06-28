import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(".codex/skills/find-real-estate-ads/scripts/sreality_fetch.py")


def load_sreality_fetch():
    spec = importlib.util.spec_from_file_location("sreality_fetch", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_locality_suggest_url_uses_current_v1_endpoint_and_municipality_category():
    module = load_sreality_fetch()

    url = module.locality_suggest_url("Česká Třebová")

    assert url.startswith("https://www.sreality.cz/api/v1/localities/suggest?")
    assert "category=municipality_cz" in url
    assert "locality_country_id=112" in url
    assert "%C4%8Cesk%C3%A1+T%C5%99ebov%C3%A1" in url


def test_choose_locality_prefers_expected_district_for_homonymous_municipality():
    module = load_sreality_fetch()
    payload = {
        "results": [
            {
                "userData": {
                    "id": 1,
                    "entityType": "municipality",
                    "municipality": "Všestary",
                    "district": "Praha-východ",
                }
            },
            {
                "userData": {
                    "id": 2,
                    "entityType": "municipality",
                    "municipality": "Všestary",
                    "district": "Hradec Králové",
                }
            },
        ]
    }

    assert module.choose_locality(payload, "Všestary", "Hradec Králové")["id"] == 2


def test_search_url_uses_snake_case_backend_params():
    module = load_sreality_fetch()

    url = module.search_url({"id": 2960, "entity_type": "municipality"}, 3, page=2, limit=60)

    assert "category_type_cb=1" in url
    assert "category_main_cb=3" in url
    assert "locality_entity_id=2960" in url
    assert "locality_entity_type=municipality" in url
    assert "estate_area_from=1000" in url
    assert "categoryTypeCb" not in url


def test_unfiltered_generic_search_response_is_rejected():
    module = load_sreality_fetch()

    assert not module.response_is_filtered({"search_title": "Všechny reality"}, ["Česká Třebová", "od 1 000"])
    assert module.response_is_filtered(
        {"search_title": "Pozemky na prodej Česká Třebová, plocha pozemku od 1 000 m²"},
        ["Česká Třebová", "od 1 000"],
    )


def test_per_square_meter_price_is_normalized_to_full_price():
    module = load_sreality_fetch()
    item = {
        "price_czk": 1000,
        "price_unit_cb": {"name": "za m²", "value": 3},
    }

    assert module.full_price_czk(item, 2590) == 2590000


def test_price_summary_is_already_full_price_for_per_square_meter_rows():
    module = load_sreality_fetch()
    item = {
        "price_summary_czk": 3468000,
        "price_czk": 1000,
        "price_unit_cb": {"name": "za m²", "value": 3},
        "price_summary_unit_cb": {"name": "za nemovitost", "value": 1},
    }

    assert module.full_price_czk(item, 3468) == 3468000


def test_listing_from_detail_normalizes_house_row():
    module = load_sreality_fetch()
    detail = {
        "result": {
            "hash_id": 556405580,
            "advert_name": "Prodej rodinného domu 650 m², pozemek 3071 m²",
            "advert_description": "Rodinný dům v České Třebové.",
            "category_type_cb": {"name": "Prodej", "value": 1},
            "category_main_cb": {"name": "Domy", "value": 2},
            "category_sub_cb": {"name": "Rodinný", "value": 37},
            "locality": {
                "city": "Česká Třebová",
                "city_seo_name": "ceska-trebova",
                "citypart_seo_name": "ceska-trebova",
                "street": "Moravská",
                "street_seo_name": "moravska",
                "district": "Ústí nad Orlicí",
                "municipality_id": 2960,
            },
            "estate_area": 3071,
            "usable_area": 650,
            "price_summary_czk": 26900000,
            "price_unit_cb": {"name": "za nemovitost", "value": 1},
            "price_summary_unit_cb": {"name": "za nemovitost", "value": 1},
        }
    }

    listing, reason = module.listing_from_detail(
        detail,
        "Česká Třebová",
        {"id": 2960, "entity_type": "municipality"},
        "Ústí nad Orlicí",
    )

    assert reason is None
    assert listing["portal"] == ["sreality.cz"]
    assert listing["property_type"] == "house"
    assert listing["price"] == "26 900 000 Kč"
    assert listing["house_area_m2"] == "650"
    assert listing["land_area_m2"] == "3071"
    assert listing["urls"] == [
        "https://www.sreality.cz/detail/prodej/dum/rodinny/ceska-trebova-moravska/556405580"
    ]


def test_land_requires_buildable_or_residential_signal():
    module = load_sreality_fetch()
    detail = {
        "result": {
            "hash_id": 3132616780,
            "advert_name": "Dražba lesa 2439 m²",
            "advert_description": "Lesní pozemek.",
            "category_type_cb": {"name": "Prodej", "value": 1},
            "category_main_cb": {"name": "Pozemky", "value": 3},
            "category_sub_cb": {"name": "Les", "value": 99},
            "locality": {
                "city": "Česká Třebová",
                "city_seo_name": "ceska-trebova",
                "district": "Ústí nad Orlicí",
                "municipality_id": 2960,
            },
            "estate_area": 2439,
            "price_summary_czk": 75000,
            "price_unit_cb": {"name": "za nemovitost", "value": 1},
        }
    }

    listing, reason = module.listing_from_detail(
        detail,
        "Česká Třebová",
        {"id": 2960, "entity_type": "municipality"},
        "Ústí nad Orlicí",
    )

    assert listing is None
    assert reason == "non-buildable-land"


def test_chata_inflection_is_excluded():
    module = load_sreality_fetch()
    detail = {
        "result": {
            "hash_id": 347947852,
            "advert_name": "Prodej chaty 43 m², pozemek 2578 m²",
            "advert_description": "Rekreační chata v Rzech.",
            "category_type_cb": {"name": "Prodej", "value": 1},
            "category_main_cb": {"name": "Domy", "value": 2},
            "category_sub_cb": {"name": "Chata", "value": 33},
            "locality": {
                "city": "Nový Hrádek",
                "city_seo_name": "novy-hradek",
                "district": "Náchod",
                "municipality_id": 2961,
            },
            "estate_area": 2578,
            "usable_area": 43,
            "price_summary_czk": 2890000,
        }
    }

    listing, reason = module.listing_from_detail(
        detail,
        "Nový Hrádek",
        {"id": 2961, "entity_type": "municipality"},
        "Náchod",
    )

    assert listing is None
    assert reason == "excluded-chata"


def test_garden_without_buildable_signal_is_not_buildable_land():
    module = load_sreality_fetch()
    item = {
        "advert_name": "Prodej zahrady 4137 m²",
        "advert_description": "Zahrada u obce.",
        "category_sub_cb": {"name": "Zahrady"},
    }

    assert not module.land_is_buildable(item)


def test_garden_with_only_future_building_potential_is_not_buildable_land():
    module = load_sreality_fetch()
    item = {
        "advert_name": "Prodej zahrady 4137 m²",
        "advert_description": "Investiční příležitost se zhodnocením cenou budoucích stavebních parcel.",
        "category_sub_cb": {"name": "Zahrady"},
    }

    assert not module.land_is_buildable(item)
