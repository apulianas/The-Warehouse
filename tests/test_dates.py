from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from orioles_bot.dates import parse_user_date, today_in_zone


def test_today_in_zone_uses_configured_timezone() -> None:
    now = datetime(2026, 8, 6, 3, 30, tzinfo=UTC)

    assert today_in_zone(ZoneInfo("America/New_York"), now) .isoformat() == "2026-08-05"


def test_parse_user_date_defaults_to_today() -> None:
    now = datetime(2026, 8, 6, 15, 0, tzinfo=UTC)

    assert parse_user_date(None, ZoneInfo("America/New_York"), now).isoformat() == "2026-08-06"
    assert parse_user_date("today", ZoneInfo("America/New_York"), now).isoformat() == "2026-08-06"


def test_parse_user_date_accepts_iso_date() -> None:
    assert parse_user_date("2026-04-01", ZoneInfo("America/New_York")).isoformat() == "2026-04-01"


def test_parse_user_date_rejects_invalid_dates() -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_user_date("08/06/2026", ZoneInfo("America/New_York"))
