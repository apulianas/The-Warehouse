from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable

from .mlb import MlbApiError, MlbClient
from .models import HittingSplit, PitchingGame, PitchingSplit, PlayerRef, StatsWindow


ROSTER_TTL_SECONDS = 900
STATS_CACHE_MAX_ENTRIES = 512
AUTOCOMPLETE_LIMIT = 25

StatsPair = tuple[HittingSplit | None, PitchingSplit | None]


class PlayerStatsService:
    """Name resolution and rolling-window stats, cached to spare the API.

    A window's totals only change when a game finishes, so repeated calls in a
    busy channel are served from memory. The roster is cached on a short clock
    instead of a key so call-ups appear without a restart.
    """

    def __init__(
        self,
        roster_ttl_seconds: int = ROSTER_TTL_SECONDS,
        max_entries: int = STATS_CACHE_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.roster_ttl_seconds = roster_ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._stats: dict[tuple[int, str, str], StatsPair] = {}
        self._pitching_games: dict[tuple[int, str, str], tuple[PitchingGame, ...]] = {}
        self._roster: tuple[PlayerRef, ...] = ()
        self._roster_fetched_at: float | None = None
        self._lock = asyncio.Lock()
        # Autocomplete fires on every keystroke, so a cold roster would other-
        # wise send one request per waiting coroutine. A dedicated lock keeps
        # that fetch single-flighted without serializing unrelated stat calls.
        self._roster_lock = asyncio.Lock()

    async def roster(self, client: MlbClient) -> tuple[PlayerRef, ...]:
        async with self._roster_lock:
            if self._fresh_roster() is not None:
                return self._roster

            roster = await client.fetch_roster()
            self._roster = roster
            self._roster_fetched_at = self._now()
            return self._roster

    def _fresh_roster(self) -> tuple[PlayerRef, ...] | None:
        if self._roster_fetched_at is None:
            return None
        if self._now() - self._roster_fetched_at >= self.roster_ttl_seconds:
            return None
        return self._roster

    async def autocomplete(
        self, client: MlbClient, query: str
    ) -> tuple[PlayerRef, ...]:
        """Roster suggestions for the typed prefix, degrading to none on failure.

        Autocomplete fires on every keystroke, so an API hiccup must leave the
        command usable rather than surfacing an error; free text still resolves.
        """
        try:
            roster = await self.roster(client)
        except MlbApiError:
            return ()
        return _match_players(roster, query)[:AUTOCOMPLETE_LIMIT]

    async def resolve(self, client: MlbClient, query: str) -> PlayerRef | None:
        """Turn an autocomplete value or a typed name into one player."""
        cleaned = query.strip()
        if not cleaned:
            return None

        if cleaned.isdigit():
            player_id = int(cleaned)
            roster = await self._roster_or_empty(client)
            for player in roster:
                if player.player_id == player_id:
                    return player
            return await client.fetch_player(player_id)

        roster = await self._roster_or_empty(client)
        matches = _match_players(roster, cleaned)
        if matches:
            return matches[0]

        found = await client.search_players(cleaned)
        return found[0] if found else None

    async def stats(
        self, client: MlbClient, player_id: int, window: StatsWindow
    ) -> StatsPair:
        key = (player_id, window.start.isoformat(), window.end.isoformat())
        async with self._lock:
            cached = self._stats.get(key)
            if cached is not None:
                return cached

        splits = await client.fetch_player_stats(player_id, window)

        async with self._lock:
            if len(self._stats) >= self.max_entries:
                self._stats.clear()
            self._stats.setdefault(key, splits)
            return self._stats[key]

    async def pitching_games(
        self, client: MlbClient, player_id: int, window: StatsWindow
    ) -> tuple[PitchingGame, ...]:
        key = (player_id, window.start.isoformat(), window.end.isoformat())
        async with self._lock:
            cached = self._pitching_games.get(key)
            if cached is not None:
                return cached

        games = await client.fetch_player_pitching_games(player_id, window)

        async with self._lock:
            if len(self._pitching_games) >= self.max_entries:
                self._pitching_games.clear()
            self._pitching_games.setdefault(key, games)
            return self._pitching_games[key]

    async def _roster_or_empty(self, client: MlbClient) -> tuple[PlayerRef, ...]:
        try:
            return await self.roster(client)
        except MlbApiError:
            return ()

    def _now(self) -> float:
        return float(self._clock())


def _match_players(
    players: Iterable[PlayerRef], query: str
) -> tuple[PlayerRef, ...]:
    """Rank roster matches, preferring names that start with what was typed."""
    needle = query.strip().casefold()
    if not needle:
        return tuple(players)

    starts: list[PlayerRef] = []
    contains: list[PlayerRef] = []
    for player in players:
        name = player.name.casefold()
        if name.startswith(needle) or any(
            part.startswith(needle) for part in name.split()
        ):
            starts.append(player)
        elif needle in name:
            contains.append(player)
    return tuple(starts + contains)
