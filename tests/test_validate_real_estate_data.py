import json

from reality.validate_real_estate_data import render_text_report, validate_data


def write_html(path, count: int = 1) -> None:
    path.write_text(
        (
            '<html><body><script id="ads-by-city-data" type="application/json">'
            f'{{"Opočno": {{"count": {count}}}}}'
            "</script></body></html>"
        ),
        encoding="utf-8",
    )


def aggregate_payload() -> dict:
    return {
        "generated_at": "2026-06-21T10:00:00+0000",
        "coverage": {
            "raw_files_found": 1,
        },
        "unmatched_raw_files": [],
        "cities": {
            "Opočno": {
                "ads": [
                    {
                        "title": "House",
                        "location": "Opočno",
                        "property_type": "house",
                        "price_czk": 5000000,
                        "urls": ["https://example.test/house"],
                    }
                ],
                "hidden_ads": [],
                "portal_status": {
                    "reality.aktualne.cz": {"status": "no_results"},
                },
                "candidate_exclusions": [
                    {
                        "portal": "reality.aktualne.cz",
                        "status": "inactive",
                    }
                ],
            }
        },
    }


def test_validate_data_passes_for_consistent_files(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_dir.joinpath("opocno.json").write_text("{}\n", encoding="utf-8")
    html_path = tmp_path / "index.html"
    write_html(html_path)

    report = validate_data(
        aggregate_payload(),
        {"status": "completed", "failed_cities": {}},
        html_path=html_path,
        raw_dir=raw_dir,
    )

    assert report["ok"]
    assert report["totals"]["active"] == 1
    assert report["candidate_exclusions"]["count"] == 1
    assert "data health: ok" in render_text_report(report)


def test_validate_data_fails_for_raw_count_mismatch(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    html_path = tmp_path / "index.html"
    write_html(html_path)

    report = validate_data(
        aggregate_payload(),
        {"status": "completed", "failed_cities": {}},
        html_path=html_path,
        raw_dir=raw_dir,
    )

    assert not report["ok"]
    assert "raw file count mismatch" in report["errors"][0]


def test_validate_data_fails_for_embedded_count_mismatch(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_dir.joinpath("opocno.json").write_text("{}\n", encoding="utf-8")
    html_path = tmp_path / "index.html"
    write_html(html_path, count=0)

    report = validate_data(
        aggregate_payload(),
        {"status": "completed", "failed_cities": {}},
        html_path=html_path,
        raw_dir=raw_dir,
    )

    assert not report["ok"]
    assert any("embedded ad count mismatches" in error for error in report["errors"])


def test_validate_data_can_fail_on_portal_warnings(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_dir.joinpath("opocno.json").write_text("{}\n", encoding="utf-8")
    html_path = tmp_path / "index.html"
    write_html(html_path)
    aggregate = aggregate_payload()
    aggregate["cities"]["Opočno"]["portal_status"]["realitymix.cz"] = {"status": "fetch_error"}

    report = validate_data(
        aggregate,
        {"status": "completed", "failed_cities": {}},
        html_path=html_path,
        raw_dir=raw_dir,
        fail_on_warnings=True,
    )

    assert not report["ok"]
    assert "portal warnings present" in report["errors"][0]


def test_validation_report_is_json_serializable(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_dir.joinpath("opocno.json").write_text("{}\n", encoding="utf-8")
    html_path = tmp_path / "index.html"
    write_html(html_path)

    report = validate_data(
        aggregate_payload(),
        {"status": "completed", "failed_cities": {}},
        html_path=html_path,
        raw_dir=raw_dir,
    )

    assert json.loads(json.dumps(report))["ok"]
