from reality.school_normalization import (
    amenity_bucket,
    amenity_city_key,
    infer_school_type,
    infer_school_type_from_text,
    looks_kindergarten_hint,
    looks_primary_school,
)


def test_amenity_helpers_normalize_supported_values_and_city_names():
    assert amenity_bucket("kindergarten") == "kindergarten"
    assert amenity_bucket("cinema") == "cinema"
    assert amenity_bucket("restaurant") is None
    assert amenity_city_key("Třebechovice pod Orebem") == "trebechovice pod orebem"
    assert amenity_city_key("Nové-Město!") == "nove mesto"


def test_school_tag_helpers_detect_primary_school_and_kindergarten_hint():
    assert looks_primary_school({"name": "Základní škola Dobruška"})
    assert looks_primary_school({"isced:level": "1;2"})
    assert looks_primary_school({"school": "primary"})
    assert not looks_primary_school({"name": "Gymnázium Dobruška", "school": "secondary"})

    assert looks_kindergarten_hint({"description": "ZŠ a MŠ v jedné budově"}, "Základní škola")
    assert looks_kindergarten_hint({"school": "mateřská škola"}, "MŠ Dobruška")
    assert not looks_kindergarten_hint({"school": "primary"}, "Základní škola")


def test_school_type_inference_from_tags_and_text():
    assert infer_school_type({"grades": "1-5"}, "Základní škola") == "1-5"
    assert infer_school_type({"isced:level": "1;2"}, "Základní škola") == "1-9"
    assert infer_school_type({}, "Malotřídní základní škola") == "Malotřídka"
    assert infer_school_type({}, "Základní škola") == "Neuvedeno"

    assert infer_school_type_from_text("Výuka od 1. do 5. třídy.") == "1-5"
    assert infer_school_type_from_text("První stupeň i druhý stupeň základní školy.") == "1-9"
    assert infer_school_type_from_text("Malotřídní škola 1-4") == "Malotřídka (1-4)"
    assert infer_school_type_from_text("Mateřská škola bez základní školy.") == "Neuvedeno"
