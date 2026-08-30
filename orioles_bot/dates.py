from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .models import ScheduleWindow, StatsWindow


DATE_FORMAT = "%Y-%m-%d"


def today_in_zone(time_zone: ZoneInfo, now: datetime | None = None) -> date:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(time_zone).date()


def is_today_request(value: str | None) -> bool:
    """Whether a date argument leaves the day to the bot rather than naming one."""
    return value is None or not value.strip() or value.strip().lower() == "today"


def parse_user_date(
    value: str | None, time_zone: ZoneInfo, now: datetime | None = None
) -> date:
    if is_today_request(value):
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
    return stats_window_ending(days, today_in_zone(time_zone, now))


def stats_window_ending(days: int, end: date) -> StatsWindow:
    """The inclusive day range of ``days`` days ending on ``end``.

    Anchored to a caller-supplied day rather than the clock, so a window built
    while working a given date stays on that date.
    """
    if days < MIN_STATS_WINDOW_DAYS or days > MAX_STATS_WINDOW_DAYS:
        raise ValueError(
            f"Days must be between {MIN_STATS_WINDOW_DAYS} and {MAX_STATS_WINDOW_DAYS}"
        )
    return StatsWindow(days=days, start=end - timedelta(days=days - 1), end=end)


MIN_SCHEDULE_WINDOW_DAYS = 1
MAX_SCHEDULE_WINDOW_DAYS = 30


def schedule_window(
    days: int, time_zone: ZoneInfo, now: datetime | None = None
) -> ScheduleWindow:
    """The inclusive day range covering the next ``days`` days, today included."""
    if days < MIN_SCHEDULE_WINDOW_DAYS or days > MAX_SCHEDULE_WINDOW_DAYS:
        raise ValueError(
            f"Days must be between {MIN_SCHEDULE_WINDOW_DAYS} and "
            f"{MAX_SCHEDULE_WINDOW_DAYS}"
        )
    start = today_in_zone(time_zone, now)
    return ScheduleWindow(days=days, start=start, end=start + timedelta(days=days - 1))
