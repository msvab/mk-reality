from build_real_estate_ads_by_city import build_aggregate_output


def test_previous_ads_missing_from_latest_snapshot_are_hidden(tmp_path):
    schools_input = tmp_path / "schools.json"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    schools_input.write_text('[{"city": "Dobruška"}]\n', encoding="utf-8")
    raw_dir.joinpath("dobruska.json").write_text(
        """
{
  "city": "Dobruška",
  "query": {
    "municipality": "Dobruška",
    "location_scope": "municipality_only",
    "country": "Czech Republic",
    "property_types": ["house", "chalupa", "land"],
    "price_min": null,
    "price_max": null,
    "house_size_min_m2": null,
    "house_size_max_m2": null,
    "land_size_min_m2": 1000,
    "land_size_max_m2": null,
    "must_have": ["sale listings"],
    "exclude": ["chata"]
  },
  "assumptions": [],
  "coverage": {
    "workers_launched": 4,
    "workers_with_results": 1,
    "candidates_gathered": 1,
    "rows_retained": 1,
    "zero_result_portals": [],
    "blocked_portals": []
  },
  "gaps": [],
  "listings": [
    {
      "portal": ["reality.idnes.cz"],
      "title": "New house",
      "location": "Dobruška",
      "property_type": "house",
      "price": "5 000 000 Kč",
      "house_area_m2": "120",
      "land_area_m2": "1200",
      "urls": ["https://reality.idnes.cz/detail/new"],
      "notes": []
    }
  ]
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    previous_aggregate = {
        "cities": {
            "Dobruška": {
                "count": 1,
                "ads": [
                    {
                        "portal": ["reality.idnes.cz"],
                        "title": "Old house",
                        "location": "Dobruška",
                        "property_type": "house",
                        "price": "4 000 000 Kč",
                        "price_czk": 4000000,
                        "house_area_m2": 100,
                        "land_area_m2": 1100,
                        "urls": ["https://reality.idnes.cz/detail/old"],
                        "notes": [],
                        "status": "active",
                        "first_seen_at": "2026-06-01T00:00:00+0200",
                        "last_seen_at": "2026-06-01T00:00:00+0200",
                    }
                ],
                "hidden_ads": [],
            }
        }
    }

    output = build_aggregate_output(schools_input, raw_dir, previous_aggregate=previous_aggregate)
    bundle = output["cities"]["Dobruška"]

    assert bundle["count"] == 1
    assert [ad["title"] for ad in bundle["ads"]] == ["New house"]
    assert bundle["ads"][0]["status"] == "active"
    assert [ad["title"] for ad in bundle["hidden_ads"]] == ["Old house"]
    assert bundle["hidden_ads"][0]["status"] == "hidden"
    assert output["coverage"]["hidden_ads"] == 1
