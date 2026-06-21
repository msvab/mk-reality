from reality.summarize_real_estate_fetch_errors import iter_candidate_exclusions, iter_warnings


def test_inactive_status_is_candidate_exclusion_not_portal_warning():
    payload = {
        "cities": {
            "Opočno": {
                "portal_status": {
                    "reality.aktualne.cz": {
                        "status": "inactive",
                        "message": "inactive-or-unpriced:https://reality.aktualne.cz/detail/opocno/example.html",
                        "evidence": [
                            "inactive-or-unpriced:https://reality.aktualne.cz/detail/opocno/example.html",
                        ],
                    },
                },
            },
        },
    }

    assert list(iter_warnings(payload)) == []
    exclusions = list(iter_candidate_exclusions(payload))

    assert len(exclusions) == 1
    assert exclusions[0]["city"] == "Opočno"
    assert exclusions[0]["portal"] == "reality.aktualne.cz"
    assert exclusions[0]["status"] == "inactive"


def test_explicit_candidate_exclusions_are_reported():
    payload = {
        "cities": {
            "Opočno": {
                "portal_status": {
                    "reality.aktualne.cz": {
                        "status": "no_results",
                    },
                },
                "candidate_exclusions": [
                    {
                        "portal": "reality.aktualne.cz",
                        "status": "inactive",
                        "message": "inactive-or-unpriced:https://reality.aktualne.cz/detail/opocno/example.html",
                    },
                ],
            },
        },
    }

    assert list(iter_warnings(payload)) == []
    exclusions = list(iter_candidate_exclusions(payload))

    assert len(exclusions) == 1
    assert exclusions[0]["city"] == "Opočno"
    assert exclusions[0]["portal"] == "reality.aktualne.cz"
    assert exclusions[0]["status"] == "inactive"


def test_fetch_failure_status_remains_portal_warning():
    payload = {
        "cities": {
            "Dobruška": {
                "portal_status": {
                    "realitymix.cz": {
                        "status": "rate_limited",
                        "http_status": 429,
                        "stage": "detail_fetch",
                        "message": "HTTP 429",
                    },
                },
            },
        },
    }

    warnings = list(iter_warnings(payload))

    assert len(warnings) == 1
    assert warnings[0]["city"] == "Dobruška"
    assert warnings[0]["portal"] == "realitymix.cz"
    assert warnings[0]["status"] == "rate_limited"
    assert warnings[0]["http_status"] == 429
    assert list(iter_candidate_exclusions(payload)) == []
