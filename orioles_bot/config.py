from __future__ import annotations

import os
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIME_ZONE = "America/New_York"
DEFAULT_POLL_INTERVAL_SECONDS = 300
# Cadences for adaptive polling. During a game substitutions land constantly,
# and in the hours before first pitch the lineup card can drop at any moment,
# so both are polled harder than the idle baseline above.
DEFAULT_LIVE_POLL_INTERVAL_SECONDS = 60
DEFAULT_PREGAME_POLL_INTERVAL_SECONDS = 120
# How long before first pitch the pre-game cadence kicks in. Lineups are
# usually posted around three hours out, so four hours gives some margin.
DEFAULT_PREGAME_LEAD_MINUTES = 240
DEFAULT_MATCHUP_MIN_PA = 5
STATE_FILE = "/data/state.json"
# Mirrors discord.py's own webhook URL parser so a malformed URL is rejected at
# startup instead of silently failing on every post.
WEBHOOK_URL_RE = re.compile(
    r"https://(?:\w+\.)?discord(?:app)?\.com/api/webhooks/"
    r"(?P<id>[0-9]{17,20})/(?P<token>[A-Za-z0-9.\-_]{60,})$"
)


def webhook_id(url: str) -> str:
    """The webhook's numeric id, which is safe to persist and log.

    A webhook URL ends in a secret token, so only the id is ever written to the
    state file or the logs.
    """
    match = WEBHOOK_URL_RE.match(url)
    return match.group("id") if match else "unknown"


@dataclass(frozen=True)
class BotConfig:
    discord_token: str
    discord_channel_ids: tuple[int, ...]
    discord_webhook_urls: tuple[str, ...]
    poll_interval_seconds: int
    matchup_min_pa: int
    time_zone: ZoneInfo
    live_poll_interval_seconds: int = DEFAULT_LIVE_POLL_INTERVAL_SECONDS
    pregame_poll_interval_seconds: int = DEFAULT_PREGAME_POLL_INTERVAL_SECONDS
    pregame_lead_minutes: int = DEFAULT_PREGAME_LEAD_MINUTES
    state_file: str = STATE_FILE

    @property
    def discord_channel_id(self) -> int | None:
        return self.discord_channel_ids[0] if self.discord_channel_ids else None

    @property
    def has_announcement_targets(self) -> bool:
        return bool(self.discord_channel_ids or self.discord_webhook_urls)


def _optional_int(value: str | None, name: str) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _interval(name: str, default: int) -> int:
    """Read a poll interval, enforcing the same 30 second floor as the baseline.

    Anything faster risks MLB rate limiting for no practical gain, since the
    Stats API does not update more often than that.
    """
    value = _optional_int(os.getenv(name), name)
    if value is None:
        return default
    if value < 30:
        raise ValueError(f"{name} must be at least 30")
    return value


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


def _webhook_urls(value: str | None, name: str) -> tuple[str, ...]:
    """Parse one webhook URL, or several separated by commas or whitespace."""
    if value is None or not value.strip():
        return ()

    urls: list[str] = []
    for raw in value.replace(",", " ").split():
        if not WEBHOOK_URL_RE.match(raw):
            raise ValueError(
                f"{name} must contain full Discord webhook URLs that look like "
                "https://discord.com/api/webhooks/<id>/<token> "
                "(copy the whole URL from Discord's Integrations settings)"
            )
        if raw not in urls:
            urls.append(raw)
    return tuple(urls)


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

    live_poll_interval = _interval(
        "LIVE_POLL_INTERVAL_SECONDS", DEFAULT_LIVE_POLL_INTERVAL_SECONDS
    )
    pregame_poll_interval = _interval(
        "PREGAME_POLL_INTERVAL_SECONDS", DEFAULT_PREGAME_POLL_INTERVAL_SECONDS
    )

    pregame_lead = _optional_int(
        os.getenv("PREGAME_LEAD_MINUTES"), "PREGAME_LEAD_MINUTES"
    )
    if pregame_lead is None:
        pregame_lead = DEFAULT_PREGAME_LEAD_MINUTES
    if pregame_lead < 0:
        raise ValueError("PREGAME_LEAD_MINUTES must not be negative")

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
        discord_webhook_urls=_webhook_urls(
            os.getenv("DISCORD_WEBHOOK_URL"), "DISCORD_WEBHOOK_URL"
        ),
        poll_interval_seconds=poll_interval,
        live_poll_interval_seconds=live_poll_interval,
        pregame_poll_interval_seconds=pregame_poll_interval,
        pregame_lead_minutes=pregame_lead,
        matchup_min_pa=matchup_min_pa,
        time_zone=time_zone,
    )
