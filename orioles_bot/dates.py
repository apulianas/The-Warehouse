from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


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
