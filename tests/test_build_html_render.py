import json

import pytest

from reality import build_html_render
from reality.build_html_render import load_cached_school_rows, render_html
from reality.build_html_urls import is_usable_school_url, normalize_url, safe_href


def test_url_helpers_normalize_and_reject_unsafe_school_links():
    assert normalize_url("zs.example.cz/path?utm=1#top") == "https://zs.example.cz/path"
    assert safe_href("https://zs.example.cz/skola?x=1#kontakt") == "https://zs.example.cz/skola"
    assert safe_href("mailto:info@example.cz") is None
    assert safe_href("https://example.cz/bad path") is None
    assert not is_usable_school_url("https://facebook.com/example-school")
    assert is_usable_school_url("https://zs-dobruska.cz/")


def test_load_cached_school_rows_filters_non_object_rows(tmp_path):
    path = tmp_path / "schools.json"
    path.write_text(
        json.dumps(
            [
                {"city": "Dobruška"},
                "bad-row",
                ["bad-row"],
                {"city": "Opočno"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert load_cached_school_rows(path) == [{"city": "Dobruška"}, {"city": "Opočno"}]


def test_load_cached_school_rows_rejects_non_array_payload(tmp_path):
    path = tmp_path / "schools.json"
    path.write_text('{"city": "Dobruška"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="must contain a JSON array"):
        load_cached_school_rows(path)


def test_render_html_escapes_school_rows_and_renders_ads_count(monkeypatch):
    feed = {
        "generated_at": "2026-07-09T12:00:00+0000",
        "cities": {
            "Dobruška & okolí": {
                "count": 2,
                "ads": [],
                "hidden_ads": [],
                "coverage": {},
                "portal_status": {},
                "assumptions": [],
                "gaps": [],
            }
        },
    }
    monkeypatch.setattr(build_html_render, "load_real_estate_ads_by_city", lambda: feed)
    monkeypatch.setattr(build_html_render.time, "strftime", lambda _fmt: "2026-07-09")

    html = render_html(
        [
            {
                "city": "Dobruška & okolí",
                "population": 12345,
                "drive_min": 17,
                "amenities": "MŠ",
                "school_type": "1-5",
                "school_name": 'ZŠ "Test" <main>',
                "school_url": "zs.example.cz/path?utm=1#top",
            }
        ]
    )

    assert "Dobruška &amp; okolí" in html
    assert "12 345" in html
    assert 'href="https://zs.example.cz/path"' in html
    assert "ZŠ &quot;Test&quot; &lt;main&gt;" in html
    assert 'class="ads-count ads-count-button"' in html
    assert 'data-city="Dobruška &amp; okolí"' in html
    assert '<script id="ads-by-city-data" type="application/json">' in html

