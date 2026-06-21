import pytest

from reality.refresh_real_estate_ads import validate_no_unmatched_raw_files


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
