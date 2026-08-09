from __future__ import annotations

import asyncio
import csv
import io
import logging
import urllib.request
from collections.abc import Callable
from typing import Any


LOGGER = logging.getLogger(__name__)

SPRINT_SPEED_URL = "https://baseballsavant.mlb.com/leaderboard/sprint_speed"
SPRINT_SPEED_FETCH_TIMEOUT_SECONDS = 45
# Statcast rates anyone with at least this many tracked competitive runs. The
# leaderboard is a few hundred rows, so one fetch covers every runner in a game.
SPRINT_SPEED_MIN_RUNS = 1

FetchRows = Callable[[int], Any]


class SprintSpeedService:
    """Statcast sprint speed, fetched once per season and kept in memory.

    Speed barely moves over a season and the leaderboard is the same document
    for every player, so refetching per pinch runner would be wasteful.
    """

    def __init__(self, fetcher: FetchRows | None = None) -> None:
        self._fetcher = fetcher or _fetch_sprint_speed_leaderboard
        self._cache: dict[int, dict[int, dict[str, float | int | None]]] = {}
        self._lock = asyncio.Lock()

    async def for_player(
        self, player_id: int, season: int
    ) -> dict[str, float | int | None] | None:
        """This player's speed line, or None if Statcast does not rate him.

        A callup with only a handful of tracked runs is genuinely absent from
        the leaderboard, which is different from the fetch having failed. Both
        return None here, and the card degrades to the stolen base record.
        """
        board = await self._leaderboard(season)
        if board is None:
            return None
        return board.get(player_id)

    async def _leaderboard(
        self, season: int
    ) -> dict[int, dict[str, float | int | None]] | None:
        async with self._lock:
            cached = self._cache.get(season)
        if cached is not None:
            return cached

        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(self._fetcher, season),
                timeout=SPRINT_SPEED_FETCH_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - speed is a nice-to-have.
            LOGGER.info("Could not fetch sprint speed for %s: %s", season, exc)
            return None

        board = _index_by_player(rows)
        if not board:
            # A 200 with no usable rows happens early in a season before
            # Statcast publishes, so caching it would kill speed all year.
            LOGGER.info("Sprint speed leaderboard for %s was empty", season)
            return None
        async with self._lock:
            self._cache.setdefault(season, board)
        return board


def _index_by_player(rows: Any) -> dict[int, dict[str, float | int | None]]:
    indexed: dict[int, dict[str, float | int | None]] = {}
    if not rows:
        return indexed
    for row in rows:
        if not isinstance(row, dict):
            continue
        player_id = _safe_int(row.get("player_id"))
        if player_id is None:
            continue
        indexed[player_id] = {
            "sprint_speed": _safe_float(row.get("sprint_speed")),
            "bolts": _safe_int(row.get("bolts")),
            "home_to_first": _safe_float(row.get("hp_to_1b")),
        }
    return indexed


def _fetch_sprint_speed_leaderboard(season: int) -> Any:
    request = urllib.request.Request(
        sprint_speed_csv_url(season),
        headers={"User-Agent": "orioles-discord-bot"},
    )
    with urllib.request.urlopen(
        request, timeout=SPRINT_SPEED_FETCH_TIMEOUT_SECONDS
    ) as response:
        payload = response.read().decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(payload)))


def sprint_speed_csv_url(season: int) -> str:
    return (
        f"{SPRINT_SPEED_URL}?year={season}&position=&team="
        f"&min={SPRINT_SPEED_MIN_RUNS}&csv=true"
    )


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
