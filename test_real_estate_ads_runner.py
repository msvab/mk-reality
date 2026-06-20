import json
import subprocess

from run_real_estate_ads_by_city import (
    cached_detail_urls_by_portal,
    cached_mmreality_result_page_urls,
    cached_reality_aktualne_result_page_urls,
    cached_reality_idnes_result_page_urls,
    cached_realitymix_result_page_urls,
    city_refresh_summary,
    combine_local_fetcher_payloads,
    daily_refresh_city_completed_today,
    format_delta,
    run_local_fetchers,
    select_cities,
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


def test_select_cities_returns_exact_city():
    cities = ["Opočno", "Deštné v Orlických horách"]

    assert select_cities(cities, "Deštné v Orlických horách") == ["Deštné v Orlických horách"]


def test_select_cities_accepts_slug_equivalent_city():
    cities = ["Opočno", "Deštné v Orlických horách"]

    assert select_cities(cities, "Destne v Orlickych horach") == ["Deštné v Orlických horách"]


def test_select_cities_raises_for_unknown_city():
    try:
        select_cities(["Opočno"], "Unknown")
    except ValueError as exc:
        assert "unknown city" in str(exc)
    else:
        raise AssertionError("expected unknown city to fail")


def test_city_refresh_summary_reports_deltas_new_ads_and_price_changes():
    previous_aggregate = {
        "cities": {
            "Opočno": {
                "ads": [
                    {
                        "title": "Existing house",
                        "location": "Opočno",
                        "property_type": "house",
                        "price_czk": 5000000,
                        "house_area_m2": 120,
                        "land_area_m2": 1200,
                        "urls": ["https://example.test/existing"],
                    },
                    {
                        "title": "Removed house",
                        "location": "Opočno",
                        "property_type": "house",
                        "price_czk": 4000000,
                        "house_area_m2": 90,
                        "land_area_m2": 1100,
                        "urls": ["https://example.test/removed"],
                    },
                ],
                "hidden_ads": [],
            }
        }
    }
    current_aggregate = {
        "cities": {
            "Opočno": {
                "ads": [
                    {
                        "title": "Existing house",
                        "location": "Opočno",
                        "property_type": "house",
                        "price_czk": 4750000,
                        "house_area_m2": 120,
                        "land_area_m2": 1200,
                        "urls": ["https://example.test/existing"],
                    },
                    {
                        "title": "New house",
                        "location": "Opočno",
                        "property_type": "house",
                        "price_czk": 6000000,
                        "house_area_m2": 130,
                        "land_area_m2": 1300,
                        "urls": ["https://example.test/new"],
                    },
                ],
                "hidden_ads": [
                    {
                        "title": "Removed house",
                        "location": "Opočno",
                        "property_type": "house",
                        "price_czk": 4000000,
                        "house_area_m2": 90,
                        "land_area_m2": 1100,
                        "urls": ["https://example.test/removed"],
                    }
                ],
            }
        }
    }

    assert city_refresh_summary(current_aggregate, previous_aggregate, "Opočno") == {
        "active": 2,
        "active_delta": 0,
        "hidden": 1,
        "hidden_delta": 1,
        "new": 1,
        "price_changed": 1,
    }


def test_format_delta_shows_explicit_plus_for_positive_values():
    assert format_delta(2) == "+2"
    assert format_delta(0) == "0"
    assert format_delta(-1) == "-1"


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
    assert urls["reality.idnes.cz"] == ["https://reality.idnes.cz/detail/prodej/dum/opocno/abc/"]


def test_cached_detail_urls_include_hidden_historical_ads():
    previous_aggregate = {
        "cities": {
            "Třebechovice pod Orebem": {
                "ads": [
                    {
                        "urls": [
                            "https://reality.aktualne.cz/detail/trebechovice-pod-orebem/current.html",
                            "https://realitymix.cz/detail/trebechovice-pod-orebem/old-mix.html",
                        ]
                    }
                ],
                "hidden_ads": [
                    {
                        "urls": [
                            "https://realitymix.cz/detail/trebechovice-pod-orebem/old-mix.html",
                            "https://reality.idnes.cz/detail/prodej/dum/trebechovice-pod-orebem/old-idnes/",
                            "https://www.mmreality.cz/nemovitosti/123456/",
                        ]
                    }
                ],
            }
        }
    }

    urls = cached_detail_urls_by_portal(previous_aggregate, "Třebechovice pod Orebem")

    assert urls["reality.aktualne.cz"] == ["https://reality.aktualne.cz/detail/trebechovice-pod-orebem/current.html"]
    assert urls["realitymix.cz"] == ["https://realitymix.cz/detail/trebechovice-pod-orebem/old-mix.html"]
    assert urls["reality.idnes.cz"] == ["https://reality.idnes.cz/detail/prodej/dum/trebechovice-pod-orebem/old-idnes/"]
    assert urls["mmreality.cz"] == ["https://www.mmreality.cz/nemovitosti/123456/"]


def test_cached_realitymix_result_page_urls_are_grouped_by_category():
    previous_aggregate = {
        "cities": {
            "Opočno": {
                "fetch_attempts": [
                    {
                        "portal": "realitymix.cz",
                        "url": "https://realitymix.cz/reality/domy/prodej/kralovehradecky/rychnov-nad-kneznou/opocno",
                        "stage": "house_result_fetch",
                        "status": "ok",
                    },
                    {
                        "portal": "realitymix.cz",
                        "url": "https://realitymix.cz/reality/pozemky/pro-bydleni/kralovehradecky/rychnov-nad-kneznou/opocno",
                        "stage": "land_result_fetch",
                        "status": "ok",
                    },
                    {
                        "portal": "realitymix.cz",
                        "url": "https://realitymix.cz/reality/pozemky/pro-bydleni/kralovehradecky/rychnov-nad-kneznou/opocno",
                        "stage": "land_result_fetch",
                        "status": "fetch_error",
                    },
                ]
            }
        }
    }

    urls = cached_realitymix_result_page_urls(previous_aggregate, "Opočno")

    assert urls == {
        "house": "https://realitymix.cz/reality/domy/prodej/kralovehradecky/rychnov-nad-kneznou/opocno",
        "land": "https://realitymix.cz/reality/pozemky/pro-bydleni/kralovehradecky/rychnov-nad-kneznou/opocno",
    }


def test_cached_mmreality_result_page_urls_ignore_detail_pages():
    previous_aggregate = {
        "cities": {
            "Opočno": {
                "fetch_attempts": [
                    {
                        "portal": "mmreality.cz",
                        "url": "https://www.mmreality.cz/nemovitosti/prodej/rodinne-domy/kralovehradecky-kraj/",
                        "stage": "search_fetch",
                        "status": "ok",
                    },
                    {
                        "portal": "mmreality.cz",
                        "url": "https://www.mmreality.cz/nemovitosti/prodej/pozemky/rychnov-nad-kneznou/",
                        "stage": "search_fetch",
                        "status": "ok",
                    },
                    {
                        "portal": "mmreality.cz",
                        "url": "https://www.mmreality.cz/nemovitosti/123456/",
                        "stage": "detail_fetch",
                        "status": "ok",
                    },
                    {
                        "portal": "mmreality.cz",
                        "url": "https://www.mmreality.cz/nemovitosti/prodej/byty/kralovehradecky-kraj/",
                        "stage": "search_fetch",
                        "status": "fetch_error",
                    },
                ]
            }
        }
    }

    urls = cached_mmreality_result_page_urls(previous_aggregate, "Opočno")

    assert urls == [
        "https://www.mmreality.cz/nemovitosti/prodej/rodinne-domy/kralovehradecky-kraj/",
        "https://www.mmreality.cz/nemovitosti/prodej/pozemky/rychnov-nad-kneznou/",
    ]


def test_cached_reality_aktualne_result_page_urls_ignore_detail_pages():
    previous_aggregate = {
        "cities": {
            "České Meziříčí": {
                "fetch_attempts": [
                    {
                        "portal": "reality.aktualne.cz",
                        "url": "https://reality.aktualne.cz/vyhledavani/r-3607-rychnov-nad-kneznou/kralovehradecky/prodej-domy_vily.html",
                        "stage": "search_fetch",
                        "status": "ok",
                    },
                    {
                        "portal": "reality.aktualne.cz",
                        "url": "https://reality.aktualne.cz/detail/ceske-mezirici/example.html",
                        "stage": "detail_fetch",
                        "status": "ok",
                    },
                    {
                        "portal": "reality.aktualne.cz",
                        "url": "https://reality.aktualne.cz/vyhledavani/r-3607-rychnov-nad-kneznou/kralovehradecky/prodej-pozemky.html",
                        "stage": "search_fetch",
                        "status": "fetch_error",
                    },
                ]
            }
        }
    }

    urls = cached_reality_aktualne_result_page_urls(previous_aggregate, "České Meziříčí")

    assert urls == [
        "https://reality.aktualne.cz/vyhledavani/r-3607-rychnov-nad-kneznou/kralovehradecky/prodej-domy_vily.html",
    ]


def test_cached_reality_idnes_result_page_urls_ignore_detail_pages():
    previous_aggregate = {
        "cities": {
            "Librantice": {
                "fetch_attempts": [
                    {
                        "portal": "reality.idnes.cz",
                        "url": "https://reality.idnes.cz/s/prodej/domy/?s-l=CAST_OBCE-83488",
                        "stage": "search_fetch",
                        "status": "ok",
                    },
                    {
                        "portal": "reality.idnes.cz",
                        "url": "https://reality.idnes.cz/detail/prodej/dum/librantice/example/",
                        "stage": "detail_fetch",
                        "status": "ok",
                    },
                    {
                        "portal": "reality.idnes.cz",
                        "url": "https://reality.idnes.cz/s/prodej/pozemky/?s-l=CAST_OBCE-83488",
                        "stage": "search_fetch",
                        "status": "fetch_error",
                    },
                ]
            }
        }
    }

    urls = cached_reality_idnes_result_page_urls(previous_aggregate, "Librantice")

    assert urls == ["https://reality.idnes.cz/s/prodej/domy/?s-l=CAST_OBCE-83488"]


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


def test_local_fetchers_run_realitymix_discovery_without_cached_urls(tmp_path, monkeypatch):
    commands = []

    def fake_run(cmd, check, capture_output, text):
        commands.append(cmd)
        payload = {
            "city": "Zero Cache",
            "query": {
                "municipality": "Zero Cache",
                "location_scope": "municipality_only",
                "country": "Czech Republic",
                "property_types": ["house", "chalupa", "land"],
                "land_size_min_m2": 1000,
            },
            "assumptions": [],
            "coverage": {
                "workers_launched": 1,
                "workers_with_results": 0,
                "candidates_gathered": 0,
                "rows_retained": 0,
                "zero_result_portals": ["realitymix.cz"],
                "blocked_portals": [],
            },
            "portal_status": {"realitymix.cz": {"status": "no_results"}},
            "fetch_attempts": [],
            "gaps": [],
            "listings": [],
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("run_real_estate_ads_by_city.subprocess.run", fake_run)

    output_path = tmp_path / "raw.json"
    assert run_local_fetchers("Zero Cache", tmp_path, output_path, previous_aggregate=None)

    realitymix_commands = [cmd for cmd in commands if cmd[1].endswith("realitymix_fetch.py")]
    assert len(realitymix_commands) == 1
    assert "--discover-results" in realitymix_commands[0]
    assert "--detail-url" not in realitymix_commands[0]
    assert output_path.exists()


def test_local_fetchers_pass_cached_realitymix_result_page_urls(tmp_path, monkeypatch):
    commands = []

    def fake_run(cmd, check, capture_output, text):
        commands.append(cmd)
        payload = {
            "city": "Opočno",
            "query": {
                "municipality": "Opočno",
                "location_scope": "municipality_only",
                "country": "Czech Republic",
                "property_types": ["house", "chalupa", "land"],
                "land_size_min_m2": 1000,
            },
            "assumptions": [],
            "coverage": {
                "workers_launched": 1,
                "workers_with_results": 0,
                "candidates_gathered": 0,
                "rows_retained": 0,
                "zero_result_portals": ["realitymix.cz"],
                "blocked_portals": [],
            },
            "portal_status": {"realitymix.cz": {"status": "no_results"}},
            "fetch_attempts": [],
            "gaps": [],
            "listings": [],
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    previous_aggregate = {
        "cities": {
            "Opočno": {
                "fetch_attempts": [
                    {
                        "portal": "realitymix.cz",
                        "url": "https://realitymix.cz/reality/domy/prodej/kralovehradecky/rychnov-nad-kneznou/opocno",
                        "stage": "house_result_fetch",
                        "status": "ok",
                    },
                    {
                        "portal": "realitymix.cz",
                        "url": "https://realitymix.cz/reality/pozemky/pro-bydleni/kralovehradecky/rychnov-nad-kneznou/opocno",
                        "stage": "land_result_fetch",
                        "status": "ok",
                    },
                ],
            }
        }
    }
    monkeypatch.setattr("run_real_estate_ads_by_city.subprocess.run", fake_run)

    assert run_local_fetchers("Opočno", tmp_path, tmp_path / "raw.json", previous_aggregate)

    realitymix_commands = [cmd for cmd in commands if cmd[1].endswith("realitymix_fetch.py")]
    assert len(realitymix_commands) == 1
    assert "--house-page-url" in realitymix_commands[0]
    assert "--land-page-url" in realitymix_commands[0]
    assert "https://realitymix.cz/reality/domy/prodej/kralovehradecky/rychnov-nad-kneznou/opocno" in realitymix_commands[0]
    assert "https://realitymix.cz/reality/pozemky/pro-bydleni/kralovehradecky/rychnov-nad-kneznou/opocno" in realitymix_commands[0]


def test_local_fetchers_pass_cached_mmreality_result_page_urls(tmp_path, monkeypatch):
    commands = []

    def fake_run(cmd, check, capture_output, text):
        commands.append(cmd)
        payload = {
            "city": "Opočno",
            "query": {
                "municipality": "Opočno",
                "location_scope": "municipality_only",
                "country": "Czech Republic",
                "property_types": ["house", "chalupa", "land"],
                "land_size_min_m2": 1000,
            },
            "assumptions": [],
            "coverage": {
                "workers_launched": 1,
                "workers_with_results": 0,
                "candidates_gathered": 0,
                "rows_retained": 0,
                "zero_result_portals": ["mmreality.cz"],
                "blocked_portals": [],
            },
            "portal_status": {"mmreality.cz": {"status": "no_results"}},
            "fetch_attempts": [],
            "gaps": [],
            "listings": [],
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    previous_aggregate = {
        "cities": {
            "Opočno": {
                "fetch_attempts": [
                    {
                        "portal": "mmreality.cz",
                        "url": "https://www.mmreality.cz/nemovitosti/prodej/rodinne-domy/kralovehradecky-kraj/",
                        "stage": "search_fetch",
                        "status": "ok",
                    }
                ],
            }
        }
    }
    monkeypatch.setattr("run_real_estate_ads_by_city.subprocess.run", fake_run)

    assert run_local_fetchers("Opočno", tmp_path, tmp_path / "raw.json", previous_aggregate)

    mmreality_commands = [cmd for cmd in commands if cmd[1].endswith("mmreality_fetch.py")]
    assert len(mmreality_commands) == 1
    assert "--discover-results" in mmreality_commands[0]
    assert "--result-url" in mmreality_commands[0]
    assert "https://www.mmreality.cz/nemovitosti/prodej/rodinne-domy/kralovehradecky-kraj/" in mmreality_commands[0]
    assert "--detail-url" not in mmreality_commands[0]


def test_local_fetchers_pass_cached_reality_idnes_detail_urls(tmp_path, monkeypatch):
    commands = []

    def fake_run(cmd, check, capture_output, text):
        commands.append(cmd)
        payload = {
            "city": "Librantice",
            "query": {
                "municipality": "Librantice",
                "location_scope": "municipality_only",
                "country": "Czech Republic",
                "property_types": ["house", "chalupa", "land"],
                "land_size_min_m2": 1000,
            },
            "assumptions": [],
            "coverage": {
                "workers_launched": 1,
                "workers_with_results": 1,
                "candidates_gathered": 1,
                "rows_retained": 1,
                "zero_result_portals": [],
                "blocked_portals": [],
            },
            "portal_status": {"reality.idnes.cz": {"status": "ok"}},
            "fetch_attempts": [],
            "gaps": [],
            "listings": [{"portal": ["reality.idnes.cz"], "title": "iDNES land"}],
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    previous_aggregate = {
        "cities": {
            "Librantice": {
                "ads": [
                    {
                        "portal": ["reality.idnes.cz"],
                        "urls": ["https://reality.idnes.cz/detail/prodej/pozemek/librantice/6915d21cf78ea8ee7a08c865/"],
                    }
                ]
            }
        }
    }
    monkeypatch.setattr("run_real_estate_ads_by_city.subprocess.run", fake_run)

    assert run_local_fetchers("Librantice", tmp_path, tmp_path / "raw.json", previous_aggregate)

    assert commands[0][1].endswith("reality_idnes_fetch.py")
    assert "--discover-results" in commands[0]
    assert "--detail-url" in commands[0]
    assert "https://reality.idnes.cz/detail/prodej/pozemek/librantice/6915d21cf78ea8ee7a08c865/" in commands[0]


def test_local_fetchers_pass_cached_reality_idnes_result_page_urls_without_cached_details(tmp_path, monkeypatch):
    commands = []

    def fake_run(cmd, check, capture_output, text):
        commands.append(cmd)
        payload = {
            "city": "Librantice",
            "query": {
                "municipality": "Librantice",
                "location_scope": "municipality_only",
                "country": "Czech Republic",
                "property_types": ["house", "chalupa", "land"],
                "land_size_min_m2": 1000,
            },
            "assumptions": [],
            "coverage": {
                "workers_launched": 1,
                "workers_with_results": 0,
                "candidates_gathered": 0,
                "rows_retained": 0,
                "zero_result_portals": ["reality.idnes.cz"],
                "blocked_portals": [],
            },
            "portal_status": {"reality.idnes.cz": {"status": "no_results"}},
            "fetch_attempts": [],
            "gaps": [],
            "listings": [],
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    previous_aggregate = {
        "cities": {
            "Librantice": {
                "fetch_attempts": [
                    {
                        "portal": "reality.idnes.cz",
                        "url": "https://reality.idnes.cz/s/prodej/domy/?s-l=CAST_OBCE-83488",
                        "stage": "search_fetch",
                        "status": "ok",
                    }
                ]
            }
        }
    }
    monkeypatch.setattr("run_real_estate_ads_by_city.subprocess.run", fake_run)

    assert run_local_fetchers("Librantice", tmp_path, tmp_path / "raw.json", previous_aggregate)

    assert commands[0][1].endswith("reality_idnes_fetch.py")
    assert "--result-url" in commands[0]
    assert "--discover-results" in commands[0]
    assert "https://reality.idnes.cz/s/prodej/domy/?s-l=CAST_OBCE-83488" in commands[0]
    assert "--detail-url" not in commands[0]


def test_local_fetchers_pass_cached_reality_aktualne_result_page_urls_and_discovery(tmp_path, monkeypatch):
    commands = []

    def fake_run(cmd, check, capture_output, text):
        commands.append(cmd)
        payload = {
            "city": "České Meziříčí",
            "query": {
                "municipality": "České Meziříčí",
                "location_scope": "municipality_only",
                "country": "Czech Republic",
                "property_types": ["house", "chalupa", "land"],
                "land_size_min_m2": 1000,
            },
            "assumptions": [],
            "coverage": {
                "workers_launched": 1,
                "workers_with_results": 0,
                "candidates_gathered": 0,
                "rows_retained": 0,
                "zero_result_portals": ["reality.aktualne.cz"],
                "blocked_portals": [],
            },
            "portal_status": {"reality.aktualne.cz": {"status": "no_results"}},
            "fetch_attempts": [],
            "gaps": [],
            "listings": [],
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    previous_aggregate = {
        "cities": {
            "České Meziříčí": {
                "fetch_attempts": [
                    {
                        "portal": "reality.aktualne.cz",
                        "url": "https://reality.aktualne.cz/vyhledavani/r-3607-rychnov-nad-kneznou/kralovehradecky/prodej-domy_vily.html",
                        "stage": "search_fetch",
                        "status": "ok",
                    }
                ],
            }
        }
    }
    monkeypatch.setattr("run_real_estate_ads_by_city.subprocess.run", fake_run)

    assert run_local_fetchers("České Meziříčí", tmp_path, tmp_path / "raw.json", previous_aggregate)

    matching_commands = [cmd for cmd in commands if cmd[1].endswith("reality_aktualne_fetch.py")]
    assert len(matching_commands) == 1
    assert "--discover-results" in matching_commands[0]
    assert "--result-url" in matching_commands[0]
    assert "https://reality.aktualne.cz/vyhledavani/r-3607-rychnov-nad-kneznou/kralovehradecky/prodej-domy_vily.html" in matching_commands[0]


def test_local_fetchers_raise_when_helper_reports_blocked_requests(tmp_path, monkeypatch):
    def fake_run(cmd, check, capture_output, text):
        payload = {
            "city": "Blocked",
            "query": {
                "municipality": "Blocked",
                "location_scope": "municipality_only",
                "country": "Czech Republic",
                "property_types": ["house", "chalupa", "land"],
                "land_size_min_m2": 1000,
            },
            "assumptions": [],
            "coverage": {
                "workers_launched": 1,
                "workers_with_results": 0,
                "candidates_gathered": 0,
                "rows_retained": 0,
                "zero_result_portals": ["realitymix.cz"],
                "blocked_portals": ["realitymix.cz root fetch failed"],
            },
            "portal_status": {"realitymix.cz": {"status": "fetch_error"}},
            "fetch_attempts": [],
            "gaps": ["category-fetch-error:land"],
            "listings": [],
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("run_real_estate_ads_by_city.subprocess.run", fake_run)

    output_path = tmp_path / "raw.json"
    try:
        run_local_fetchers("Blocked", tmp_path, output_path, previous_aggregate=None)
    except RuntimeError as exc:
        assert "blocked or failed requests" in str(exc)
    else:
        raise AssertionError("expected helper-reported blocked requests to fail local fetch")

    assert not output_path.exists()
