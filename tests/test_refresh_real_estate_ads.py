import pytest

import reality.refresh_real_estate_ads as refresh
from reality.refresh_real_estate_ads import (
    build_refresh_summary,
    render_refresh_summary,
    validate_no_unmatched_raw_files,
)
from reality.run_real_estate_ads_by_city import prune_retired_city_outputs


def test_refresh_commit_stages_idnes_locality_cache(monkeypatch):
    commands = []

    def fake_capture(args, *, check=True):
        if args == ["git", "status", "--porcelain"]:
            return " M data/cache/reality_idnes_locality_ids.json\n"
        if args == ["git", "diff", "--cached", "--name-only"]:
            return "data/cache/reality_idnes_locality_ids.json\n"
        raise AssertionError(args)

    monkeypatch.setattr(refresh, "capture_command", fake_capture)
    monkeypatch.setattr(refresh, "run_command", lambda args, check=True: commands.append(args))

    refresh.commit_and_push("Refresh real estate ads", push=True)

    assert ["git", "add", "data/real_estate_ads_raw", "data/cache/reality_idnes_locality_ids.json", "data/generated/real_estate_ads_by_city.json", "data/state/real_estate_ads_run_state.json", "index.html"] in commands
    assert ["git", "commit", "-m", "Refresh real estate ads"] in commands
    assert ["git", "push"] in commands


def test_unmatched_raw_file_validation_passes_when_metadata_is_empty(capsys):
    validate_no_unmatched_raw_files({"unmatched_raw_files": []})

    assert "raw files: no unmatched files" in capsys.readouterr().out


def test_unmatched_raw_file_validation_fails_with_stale_raw_file():
    aggregate = {
        "unmatched_raw_files": [
            {
                "city": "Bražec",
                "file": "brazec.json",
            },
        ],
    }

    with pytest.raises(RuntimeError, match="unmatched raw files") as exc:
        validate_no_unmatched_raw_files(aggregate)

    assert "brazec.json: Bražec" in str(exc.value)


def test_retired_municipality_outputs_are_pruned_with_their_refresh_state(tmp_path):
    (tmp_path / "opocno.json").write_text("{}", encoding="utf-8")
    (tmp_path / "cervena-hora.json").write_text("{}", encoding="utf-8")
    state = {
        "completed_cities": ["Opočno", "Červená Hora"],
        "remaining_cities": ["Červená Hora"],
        "failed_cities": {"Červená Hora": {"error": "old"}},
        "daily_refresh": {"cities": {"Opočno": {}, "Červená Hora": {}}},
    }

    removed = prune_retired_city_outputs(["Opočno"], tmp_path, state)

    assert [path.name for path in removed] == ["cervena-hora.json"]
    assert (tmp_path / "opocno.json").exists()
    assert state == {
        "completed_cities": ["Opočno"],
        "remaining_cities": [],
        "failed_cities": {},
        "daily_refresh": {"cities": {"Opočno": {}}},
    }


def test_refresh_summary_reports_totals_warnings_and_city_deltas():
    previous_aggregate = {
        "cities": {
            "Opočno": {
                "ads": [
                    {
                        "title": "Old house",
                        "location": "Opočno",
                        "property_type": "house",
                        "price_czk": 4000000,
                        "urls": ["https://example.test/old"],
                    }
                ],
                "hidden_ads": [],
            }
        }
    }
    current_aggregate = {
        "generated_at": "2026-06-21T10:00:00+0000",
        "coverage": {
            "raw_files_found": 1,
        },
        "unmatched_raw_files": [],
        "cities": {
            "Opočno": {
                "ads": [
                    {
                        "title": "New house",
                        "location": "Opočno",
                        "property_type": "house",
                        "price_czk": 5000000,
                        "urls": ["https://example.test/new"],
                    }
                ],
                "hidden_ads": [
                    {
                        "title": "Old house",
                        "location": "Opočno",
                        "property_type": "house",
                        "price_czk": 4000000,
                        "urls": ["https://example.test/old"],
                    }
                ],
                "portal_status": {
                    "realitymix.cz": {"status": "fetch_error"},
                    "reality.aktualne.cz": {"status": "no_results"},
                },
                "candidate_exclusions": [
                    {
                        "portal": "reality.aktualne.cz",
                        "status": "inactive",
                        "message": "inactive-or-unpriced:https://reality.aktualne.cz/detail/opocno/example.html",
                    }
                ],
            }
        },
    }
    previous_state = {
        "daily_refresh": {
            "cities": {
                "Opočno": {
                    "last_completed_at": "2026-06-20T10:00:00+0000",
                }
            }
        }
    }
    current_state = {
        "failed_cities": {},
        "daily_refresh": {
            "cities": {
                "Opočno": {
                    "last_completed_at": "2026-06-21T10:00:00+0000",
                }
            }
        },
    }

    summary = build_refresh_summary(
        previous_aggregate,
        current_aggregate,
        previous_state,
        current_state,
        did_refresh=True,
    )
    rendered = render_refresh_summary(summary)

    assert summary["totals"] == {"cities": 1, "active": 1, "hidden": 1, "cities_with_ads": 1}
    assert summary["refreshed_cities"][0]["city"] == "Opočno"
    assert summary["refreshed_cities"][0]["active_delta"] == 0
    assert summary["refreshed_cities"][0]["hidden_delta"] == 1
    assert summary["refreshed_cities"][0]["new"] == 1
    assert summary["portal_warnings"]["count"] == 1
    assert summary["candidate_exclusions"]["count"] == 1
    assert "# Real Estate Refresh Summary" in rendered
    assert "| Opočno | 1 (0) | 1 (+1) | 1 | 0 |" in rendered
    assert "- By status: inactive=1" in rendered
