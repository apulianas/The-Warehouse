from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIME_ZONE = "America/New_York"
DEFAULT_POLL_INTERVAL_SECONDS = 300
DEFAULT_MATCHUP_MIN_PA = 5
STATE_FILE = "/data/state.json"


@dataclass(frozen=True)
class BotConfig:
    discord_token: str
    discord_channel_ids: tuple[int, ...]
    poll_interval_seconds: int
    matchup_min_pa: int
    time_zone: ZoneInfo
    state_file: str = STATE_FILE

    @property
    def discord_channel_id(self) -> int | None:
        return self.discord_channel_ids[0] if self.discord_channel_ids else None


def _optional_int(value: str | None, name: str) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _channel_ids(value: str | None, name: str) -> tuple[int, ...]:
    """Parse one channel id, or several separated by commas or whitespace."""
    if value is None or not value.strip():
        return ()

    ids: list[int] = []
    for raw in value.replace(",", " ").split():
        try:
            channel_id = int(raw)
        except ValueError as exc:
            raise ValueError(
                f"{name} must be a channel id, or several separated by commas"
            ) from exc
        if channel_id not in ids:
            ids.append(channel_id)
    return tuple(ids)


def load_config() -> BotConfig:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise ValueError("DISCORD_TOKEN is required")

    poll_interval = _optional_int(
        os.getenv("POLL_INTERVAL_SECONDS"), "POLL_INTERVAL_SECONDS"
    )
    if poll_interval is None:
        poll_interval = DEFAULT_POLL_INTERVAL_SECONDS
    if poll_interval < 30:
        raise ValueError("POLL_INTERVAL_SECONDS must be at least 30")

    matchup_min_pa = _optional_int(os.getenv("MATCHUP_MIN_PA"), "MATCHUP_MIN_PA")
    if matchup_min_pa is None:
        matchup_min_pa = DEFAULT_MATCHUP_MIN_PA
    if matchup_min_pa < 1:
        raise ValueError("MATCHUP_MIN_PA must be at least 1")

    time_zone_name = os.getenv("TIME_ZONE", DEFAULT_TIME_ZONE).strip() or DEFAULT_TIME_ZONE
    try:
        time_zone = ZoneInfo(time_zone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"TIME_ZONE is not valid: {time_zone_name}") from exc

    return BotConfig(
        discord_token=token,
        discord_channel_ids=_channel_ids(
            os.getenv("DISCORD_CHANNEL_ID"), "DISCORD_CHANNEL_ID"
        ),
        poll_interval_seconds=poll_interval,
        matchup_min_pa=matchup_min_pa,
        time_zone=time_zone,
    )
