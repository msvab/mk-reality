from reality.build_real_estate_ads_json import build_output, detect_fetch_status, parse_price_czk


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


def test_price_parser_ignores_per_square_meter_unit_digits():
    assert parse_price_czk("2 400 Kč (za m 2 )") == 2400
    assert parse_price_czk("4 500 Kč / (za m 2 )") == 4500
    assert parse_price_czk("1 Kč (za m 2 )") == 1


def test_price_parser_keeps_total_and_million_prices():
    assert parse_price_czk("1 650 000 Kč") == 1650000
    assert parse_price_czk("2,4 mil. Kč") == 2400000


def test_fetch_status_does_not_treat_listing_id_digits_as_http_429():
    assert detect_fetch_status(
        "unsupported-property-type:https://reality.aktualne.cz/detail/jaromer/pronajem-kancelarskych-prostor-142-34-m2-jaromer-8542993.html"
    ) == (None, None)


def test_fetch_status_does_not_treat_area_429_as_http_429():
    assert detect_fetch_status(
        "land-below-threshold:https://reality.aktualne.cz/detail/broumov/prodej-domu-429-m-pozemek-218-m-8566669.html"
    ) == (None, None)


def test_fetch_status_detects_real_http_429():
    assert detect_fetch_status("detail_fetch failed: HTTP 429") == ("rate_limited", 429)


def test_fetch_status_detects_429_too_many_requests():
    assert detect_fetch_status("detail_fetch failed: 429 Too Many Requests") == ("rate_limited", 429)


def test_reality_aktualne_inactive_gap_is_candidate_exclusion_not_portal_status():
    payload = base_payload([])
    payload["coverage"]["zero_result_portals"] = ["reality.aktualne.cz"]
    payload["gaps"] = [
        "inactive-or-unpriced:https://reality.aktualne.cz/detail/opocno/prodej-stavebniho-pozemku-123.html",
    ]

    output = build_output(payload)

    assert output["portal_status"]["reality.aktualne.cz"]["status"] == "no_results"
    assert output["candidate_exclusions"] == [
        {
            "portal": "reality.aktualne.cz",
            "status": "inactive",
            "reason": "inactive-or-unpriced",
            "url": "https://reality.aktualne.cz/detail/opocno/prodej-stavebniho-pozemku-123.html",
            "message": "inactive-or-unpriced:https://reality.aktualne.cz/detail/opocno/prodej-stavebniho-pozemku-123.html",
            "evidence": [
                "inactive-or-unpriced:https://reality.aktualne.cz/detail/opocno/prodej-stavebniho-pozemku-123.html",
            ],
        }
    ]


def test_reality_aktualne_fetch_error_gap_still_sets_portal_status():
    payload = base_payload([])
    payload["coverage"]["zero_result_portals"] = ["reality.aktualne.cz"]
    payload["gaps"] = [
        "reality.aktualne.cz detail fetch failed: https://reality.aktualne.cz/detail/opocno/example.html: HTTP 500",
    ]

    output = build_output(payload)

    assert output["portal_status"]["reality.aktualne.cz"]["status"] == "fetch_error"
    assert output["candidate_exclusions"] == []


def test_stale_detail_attempt_does_not_override_portal_status():
    payload = base_payload([])
    payload["coverage"]["zero_result_portals"] = ["realitymix.cz"]
    payload["fetch_attempts"] = [
        {
            "portal": "realitymix.cz",
            "url": "https://realitymix.cz/detail/opocno/old-123.html",
            "stage": "detail_parse",
            "attempt": 1,
            "status": "fallback_page",
            "message": "Požadovaný inzerát již není v naší databázi",
        },
    ]
    payload["gaps"] = ["removed-fallback-page:https://realitymix.cz/detail/opocno/old-123.html"]

    output = build_output(payload)

    assert output["portal_status"]["realitymix.cz"]["status"] == "no_results"


def test_inactive_gap_does_not_override_active_portal_status():
    payload = base_payload(
        [
            {
                "portal": ["mmreality.cz"],
                "title": "Prodej, Chalupa, 52 m², Nový Hrádek",
                "location": "Nový Hrádek, okres Náchod",
                "property_type": "house",
                "price": "4 190 000 Kč",
                "house_area_m2": 52,
                "land_area_m2": 2008,
                "urls": ["https://www.mmreality.cz/nemovitosti/930853/"],
                "notes": ["detail-url-verified:mmreality.cz"],
            }
        ]
    )
    payload["portal_status"] = {
        "mmreality.cz": {
            "status": "ok",
            "message": "Retained at least one detail-verified M&M Reality row.",
        }
    }
    payload["gaps"] = [
        "inactive-generic-page:https://www.mmreality.cz/nemovitosti/939683/",
    ]

    output = build_output(payload)

    assert output["portal_status"]["mmreality.cz"]["status"] == "ok"


def test_search_fallback_attempt_still_sets_portal_status():
    payload = base_payload([])
    payload["coverage"]["zero_result_portals"] = ["mmreality.cz"]
    payload["fetch_attempts"] = [
        {
            "portal": "mmreality.cz",
            "url": "https://www.mmreality.cz/nemovitosti/prodej/rodinne-domy/",
            "stage": "search_fetch",
            "attempt": 1,
            "status": "blocked",
            "http_status": 403,
            "error": "HTTP 403",
        },
    ]

    output = build_output(payload)

    assert output["portal_status"]["mmreality.cz"]["status"] == "blocked"
    assert output["portal_status"]["mmreality.cz"]["http_status"] == 403
