from build_real_estate_ads_json import build_output


def base_payload(listings):
    return {
        "query": {
            "municipality": "Nový Hrádek",
            "location_scope": "municipality_only",
            "country": "Czech Republic",
            "property_types": ["house", "chalupa", "land"],
            "land_size_min_m2": 1000,
        },
        "coverage": {
            "workers_launched": 3,
            "workers_with_results": 3,
            "candidates_gathered": len(listings),
            "rows_retained": len(listings),
            "zero_result_portals": [],
            "blocked_portals": [],
        },
        "listings": listings,
    }


def test_cross_portal_land_rows_merge_when_title_and_location_wording_differs():
    output = build_output(
        base_payload(
            [
                {
                    "portal": ["mmreality.cz"],
                    "title": "Prodej, Pozemek k bydlení, 1328 m², Nový Hrádek",
                    "location": "Nový Hrádek - Rzy, okres Náchod",
                    "property_type": "land",
                    "price": "1 650 000 Kč",
                    "house_area_m2": "unknown",
                    "land_area_m2": "1328",
                    "urls": ["https://www.mmreality.cz/nemovitosti/943316/"],
                    "notes": [],
                },
                {
                    "portal": ["realitymix.cz"],
                    "title": "Prodej pozemku k bydlení, 1328 m², Nový Hrádek - Rzy",
                    "location": "Rzy, Nový Hrádek, okr. Náchod",
                    "property_type": "land",
                    "price": "1 650 000 Kč",
                    "house_area_m2": "unknown",
                    "land_area_m2": "1328",
                    "urls": ["https://realitymix.cz/detail/novy-hradek/prodej-pozemku-k-bydleni-1328-m-novy-hradek-rzy-8602899.html"],
                    "notes": [],
                },
                {
                    "portal": ["reality.aktualne.cz"],
                    "title": "Prodej pozemku k bydlení, 1328 m², Nový Hrádek - Rzy",
                    "location": "Rzy, Náchod",
                    "property_type": "land",
                    "price": "1 650 000 Kč",
                    "house_area_m2": "unknown",
                    "land_area_m2": "1328",
                    "urls": ["https://reality.aktualne.cz/detail/novy-hradek/prodej-pozemku-k-bydleni-1328-m-novy-hradek-rzy-8602899.html"],
                    "notes": [],
                },
            ]
        )
    )

    assert len(output["listings"]) == 1
    listing = output["listings"][0]
    assert listing["portal"] == ["mmreality.cz", "reality.aktualne.cz", "realitymix.cz"]
    assert len(listing["urls"]) == 3


def test_numbered_same_price_same_area_plots_do_not_merge():
    payload = base_payload(
        [
            {
                "portal": ["realitymix.cz"],
                "title": "Prodej stavebního pozemku č.1 - 1.198 m2 - Dobříkovec u Opočna",
                "location": "Dobříkovec, Opočno",
                "property_type": "land",
                "price": "2 875 200 Kč",
                "house_area_m2": "unknown",
                "land_area_m2": "1198",
                "urls": ["https://realitymix.cz/detail/opocno/pozemek-1.html"],
                "notes": [],
            },
            {
                "portal": ["realitymix.cz"],
                "title": "Prodej stavebního pozemku č.2 - 1.198 m2 - Dobříkovec u Opočna",
                "location": "Dobříkovec, Opočno",
                "property_type": "land",
                "price": "2 875 200 Kč",
                "house_area_m2": "unknown",
                "land_area_m2": "1198",
                "urls": ["https://realitymix.cz/detail/opocno/pozemek-2.html"],
                "notes": [],
            },
        ]
    )
    payload["query"]["municipality"] = "Opočno"

    output = build_output(payload)

    assert len(output["listings"]) == 2
