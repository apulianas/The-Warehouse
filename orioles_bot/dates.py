from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .models import StatsWindow


DATE_FORMAT = "%Y-%m-%d"


def today_in_zone(time_zone: ZoneInfo, now: datetime | None = None) -> date:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(time_zone).date()


def parse_user_date(
    value: str | None, time_zone: ZoneInfo, now: datetime | None = None
) -> date:
    if value is None or not value.strip() or value.strip().lower() == "today":
        return today_in_zone(time_zone, now)

    try:
        return datetime.strptime(value.strip(), DATE_FORMAT).date()
    except ValueError as exc:
        raise ValueError("Date must be today or YYYY-MM-DD") from exc


MIN_STATS_WINDOW_DAYS = 1
MAX_STATS_WINDOW_DAYS = 162


def stats_window(
    days: int, time_zone: ZoneInfo, now: datetime | None = None
) -> StatsWindow:
    """The inclusive day range covering the last ``days`` days, today included."""
    if days < MIN_STATS_WINDOW_DAYS or days > MAX_STATS_WINDOW_DAYS:
        raise ValueError(
            f"Days must be between {MIN_STATS_WINDOW_DAYS} and {MAX_STATS_WINDOW_DAYS}"
        )
    end = today_in_zone(time_zone, now)
    return StatsWindow(days=days, start=end - timedelta(days=days - 1), end=end)
