from __future__ import annotations

import argparse

import pytest

from reality import build_html


def test_full_build_preserves_existing_artifacts_when_no_rows_are_generated(monkeypatch, tmp_path):
    schools_path = tmp_path / "schools.json"
    schools_path.write_text('[{"city": "Dobruška"}]\n', encoding="utf-8")
    rendered_rows = []

    monkeypatch.setattr(build_html, "SCHOOLS_JSON_PATH", schools_path)
    monkeypatch.setattr(build_html, "parse_args", lambda: argparse.Namespace(ads_only=False))
    monkeypatch.setattr(build_html, "build_school_rows", lambda _args: [])
    monkeypatch.setattr(build_html, "write_html", lambda rows: rendered_rows.append(rows))

    with pytest.raises(RuntimeError, match="produced no school rows"):
        build_html.main()

    assert schools_path.read_text(encoding="utf-8") == '[{"city": "Dobruška"}]\n'
    assert rendered_rows == []
