from run_real_estate_ads_by_city import (
    cached_detail_urls_by_portal,
    combine_local_fetcher_payloads,
    daily_refresh_city_completed_today,
)


def test_daily_refresh_guard_is_per_municipality():
    state = {
        "daily_refresh": {
            "cities": {
                "Opočno": {
                    "last_completed_on": "2026-06-05",
                    "last_completed_at": "2026-06-05T09:00:00+0200",
                }
            }
        }
    }

    assert daily_refresh_city_completed_today(state, "Opočno", today="2026-06-05")
    assert not daily_refresh_city_completed_today(state, "Nové Město nad Metují", today="2026-06-05")


def test_daily_refresh_guard_expires_next_day():
    state = {
        "daily_refresh": {
            "cities": {
                "Opočno": {
                    "last_completed_on": "2026-06-05",
                    "last_completed_at": "2026-06-05T09:00:00+0200",
                }
            }
        }
    }

    assert not daily_refresh_city_completed_today(state, "Opočno", today="2026-06-06")


def test_cached_detail_urls_are_grouped_by_supported_local_fetcher_portal():
    previous_aggregate = {
        "cities": {
            "Opočno": {
                "ads": [
                    {
                        "urls": [
                            "https://www.mmreality.cz/nemovitosti/123/",
                            "https://realitymix.cz/detail/opocno/example-456.html",
                            "https://reality.aktualne.cz/detail/opocno/example-789.html",
                            "https://reality.idnes.cz/detail/prodej/dum/opocno/abc/",
                        ]
                    }
                ]
            }
        }
    }

    urls = cached_detail_urls_by_portal(previous_aggregate, "Opočno")

    assert urls["mmreality.cz"] == ["https://www.mmreality.cz/nemovitosti/123/"]
    assert urls["realitymix.cz"] == ["https://realitymix.cz/detail/opocno/example-456.html"]
    assert urls["reality.aktualne.cz"] == ["https://reality.aktualne.cz/detail/opocno/example-789.html"]
    assert "reality.idnes.cz" not in urls


def test_local_fetcher_payloads_are_combined_into_raw_city_payload():
    payload = combine_local_fetcher_payloads(
        "Opočno",
        [
            {
                "assumptions": ["worker assumption"],
                "coverage": {
                    "workers_with_results": 1,
                    "candidates_gathered": 2,
                    "rows_retained": 1,
                    "zero_result_portals": [],
                    "blocked_portals": [],
                },
                "portal_status": {"realitymix.cz": {"status": "ok"}},
                "fetch_attempts": [{"portal": "realitymix.cz", "url": "https://example.test", "stage": "detail_fetch", "attempt": 1, "status": "ok"}],
                "gaps": ["gap"],
                "listings": [{"title": "Listing"}],
            }
        ],
    )

    assert payload["city"] == "Opočno"
    assert payload["coverage"]["workers_launched"] == 1
    assert payload["coverage"]["candidates_gathered"] == 2
    assert payload["coverage"]["rows_retained"] == 1
    assert payload["portal_status"]["realitymix.cz"]["status"] == "ok"
    assert payload["listings"] == [{"title": "Listing"}]
