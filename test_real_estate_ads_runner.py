from run_real_estate_ads_by_city import daily_refresh_city_completed_today


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
