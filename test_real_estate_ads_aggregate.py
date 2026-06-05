import json

from build_real_estate_ads_by_city import build_aggregate_output


def write_school_and_raw(tmp_path, *, price: str = "5 000 000 Kč"):
    schools_input = tmp_path / "schools.json"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    schools_input.write_text('[{"city": "Dobruška"}]\n', encoding="utf-8")
    raw_dir.joinpath("dobruska.json").write_text(
        json.dumps(
            {
                "city": "Dobruška",
                "query": {
                    "municipality": "Dobruška",
                    "location_scope": "municipality_only",
                    "country": "Czech Republic",
                    "property_types": ["house", "chalupa", "land"],
                    "price_min": None,
                    "price_max": None,
                    "house_size_min_m2": None,
                    "house_size_max_m2": None,
                    "land_size_min_m2": 1000,
                    "land_size_max_m2": None,
                    "must_have": ["sale listings"],
                    "exclude": ["chata"],
                },
                "assumptions": [],
                "coverage": {
                    "workers_launched": 4,
                    "workers_with_results": 1,
                    "candidates_gathered": 1,
                    "rows_retained": 1,
                    "zero_result_portals": [],
                    "blocked_portals": [],
                },
                "gaps": [],
                "listings": [
                    {
                        "portal": ["reality.idnes.cz"],
                        "title": "New house",
                        "location": "Dobruška",
                        "property_type": "house",
                        "price": price,
                        "house_area_m2": "120",
                        "land_area_m2": "1200",
                        "urls": ["https://reality.idnes.cz/detail/new"],
                        "notes": [],
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return schools_input, raw_dir


def test_previous_ads_missing_from_latest_snapshot_are_hidden(tmp_path):
    schools_input, raw_dir = write_school_and_raw(tmp_path)
    previous_aggregate = {
        "cities": {
            "Dobruška": {
                "count": 1,
                "ads": [
                    {
                        "portal": ["reality.idnes.cz"],
                        "title": "Old house",
                        "location": "Dobruška",
                        "property_type": "house",
                        "price": "4 000 000 Kč",
                        "price_czk": 4000000,
                        "house_area_m2": 100,
                        "land_area_m2": 1100,
                        "urls": ["https://reality.idnes.cz/detail/old"],
                        "notes": [],
                        "status": "active",
                        "first_seen_at": "2026-06-01T00:00:00+0200",
                        "last_seen_at": "2026-06-01T00:00:00+0200",
                    }
                ],
                "hidden_ads": [],
            }
        }
    }

    output = build_aggregate_output(schools_input, raw_dir, previous_aggregate=previous_aggregate)
    bundle = output["cities"]["Dobruška"]

    assert bundle["count"] == 1
    assert [ad["title"] for ad in bundle["ads"]] == ["New house"]
    assert bundle["ads"][0]["status"] == "active"
    assert [ad["title"] for ad in bundle["hidden_ads"]] == ["Old house"]
    assert bundle["hidden_ads"][0]["status"] == "hidden"
    assert output["coverage"]["hidden_ads"] == 1


def test_price_history_is_preserved_when_price_is_unchanged(tmp_path):
    schools_input, raw_dir = write_school_and_raw(tmp_path, price="5 000 000 Kč")
    previous_aggregate = {
        "cities": {
            "Dobruška": {
                "count": 1,
                "ads": [
                    {
                        "portal": ["reality.idnes.cz"],
                        "title": "New house",
                        "location": "Dobruška",
                        "property_type": "house",
                        "price": "5 000 000 Kč",
                        "price_czk": 5000000,
                        "house_area_m2": 120,
                        "land_area_m2": 1200,
                        "urls": ["https://reality.idnes.cz/detail/new"],
                        "notes": [],
                        "status": "active",
                        "first_seen_at": "2026-06-01T00:00:00+0200",
                        "last_seen_at": "2026-06-01T00:00:00+0200",
                        "price_history": [
                            {
                                "seen_at": "2026-06-01T00:00:00+0200",
                                "price": "5 000 000 Kč",
                                "price_czk": 5000000,
                            }
                        ],
                    }
                ],
                "hidden_ads": [],
            }
        }
    }

    output = build_aggregate_output(schools_input, raw_dir, previous_aggregate=previous_aggregate)
    ad = output["cities"]["Dobruška"]["ads"][0]

    assert len(ad["price_history"]) == 1
    assert ad["price_history"][0]["price_czk"] == 5000000
    assert ad["last_seen_at"] == output["generated_at"]


def test_price_history_appends_only_when_price_changes(tmp_path):
    schools_input, raw_dir = write_school_and_raw(tmp_path, price="4 750 000 Kč")
    previous_aggregate = {
        "cities": {
            "Dobruška": {
                "count": 1,
                "ads": [
                    {
                        "portal": ["reality.idnes.cz"],
                        "title": "New house",
                        "location": "Dobruška",
                        "property_type": "house",
                        "price": "5 000 000 Kč",
                        "price_czk": 5000000,
                        "house_area_m2": 120,
                        "land_area_m2": 1200,
                        "urls": ["https://reality.idnes.cz/detail/new"],
                        "notes": [],
                        "status": "active",
                        "first_seen_at": "2026-06-01T00:00:00+0200",
                        "last_seen_at": "2026-06-01T00:00:00+0200",
                        "price_history": [
                            {
                                "seen_at": "2026-06-01T00:00:00+0200",
                                "price": "5 000 000 Kč",
                                "price_czk": 5000000,
                            }
                        ],
                    }
                ],
                "hidden_ads": [],
            }
        }
    }

    output = build_aggregate_output(schools_input, raw_dir, previous_aggregate=previous_aggregate)
    ad = output["cities"]["Dobruška"]["ads"][0]

    assert [item["price_czk"] for item in ad["price_history"]] == [5000000, 4750000]
    assert ad["price_history"][-1]["seen_at"] == output["generated_at"]
