from __future__ import annotations

import asyncio
import csv
import io
import logging
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from datetime import date
from typing import Any

from .mlb import savant_hand_window_params, savant_matchup_params
from .models import GameInfo, MatchupAnnotation, MatchupHistory


LOGGER = logging.getLogger(__name__)

HOT_EMOJI = "🔥"
COLD_EMOJI = "🧊"
HOT_WOBA = 0.400
COLD_WOBA = 0.280
HOT_AVERAGE = 0.300
COLD_AVERAGE = 0.200
MATCHUP_FETCH_TIMEOUT_SECONDS = 45

HIT_EVENTS = {"single", "double", "triple", "home_run"}
WALK_EVENTS = {"walk", "intent_walk"}
STRIKEOUT_EVENTS = {"strikeout", "strikeout_double_play", "strikeout_triple_play"}
NON_AT_BAT_EVENTS = {
    "catcher_interf",
    "hit_by_pitch",
    "intent_walk",
    "sac_bunt",
    "sac_bunt_double_play",
    "sac_fly",
    "sac_fly_double_play",
    "walk",
}

FetchRecords = Callable[[int, int], Any]
FetchHandRecords = Callable[[int, str, date, date], Any]
# A batter, the pitching hand he is facing, and the inclusive days it covers.
HandWindow = tuple[int, str, date, date]
STATCAST_MATCHUP_CSV_URL = "https://baseballsavant.mlb.com/statcast_search/csv"


class MatchupService:
    def __init__(
        self,
        min_pa: int = 5,
        fetcher: FetchRecords | None = None,
        hand_fetcher: FetchHandRecords | None = None,
    ) -> None:
        self.min_pa = min_pa
        self._fetcher = fetcher or _fetch_statcast_batter_pitcher
        self._hand_fetcher = hand_fetcher or _fetch_statcast_batter_hand
        # The full line is cached rather than the hot/cold verdict, so a
        # substitution card can show a matchup too small or too ordinary to
        # earn an emoji without refetching it.
        self._cache: dict[tuple[int, int], MatchupHistory | None] = {}
        self._hand_cache: dict[HandWindow, MatchupHistory] = {}
        self._lock = asyncio.Lock()

    async def fetch_for_games(
        self, games: Iterable[GameInfo]
    ) -> dict[tuple[int, int], MatchupAnnotation]:
        pairs: set[tuple[int, int]] = set()
        for game in games:
            if game.opponent_pitcher and game.opponent_pitcher.player_id is not None:
                pairs.update(
                    (player.player_id, game.opponent_pitcher.player_id)
                    for player in game.lineup
                )
            if game.pitcher and game.pitcher.player_id is not None:
                pairs.update(
                    (player.player_id, game.pitcher.player_id)
                    for player in game.opponent_lineup
                )
        return await self.fetch_many(pairs)

    async def fetch_many(
        self, pairs: Iterable[tuple[int, int]]
    ) -> dict[tuple[int, int], MatchupAnnotation]:
        histories = await self.history_many(pairs)
        return {
            pair: annotation
            for pair, history in histories.items()
            if (annotation := annotation_from_history(history, self.min_pa)) is not None
        }

    async def history(
        self, batter_id: int, pitcher_id: int
    ) -> MatchupHistory | None:
        histories = await self.history_many([(batter_id, pitcher_id)])
        return histories.get((batter_id, pitcher_id))

    async def history_many(
        self, pairs: Iterable[tuple[int, int]]
    ) -> dict[tuple[int, int], MatchupHistory]:
        unique_pairs = set(pairs)
        if not unique_pairs:
            return {}

        async with self._lock:
            missing = [pair for pair in unique_pairs if pair not in self._cache]

        if missing:
            fetched = await asyncio.gather(
                *(self._fetch_pair(batter_id, pitcher_id) for batter_id, pitcher_id in missing)
            )
            async with self._lock:
                for pair, history in zip(missing, fetched, strict=True):
                    # A failed fetch is deliberately not cached, so a transient
                    # Statcast outage does not poison the pair for the life of
                    # the process. A genuinely empty matchup caches fine, since
                    # it arrives as a zeroed history rather than None.
                    if history is not None:
                        self._cache.setdefault(pair, history)

        async with self._lock:
            return {
                pair: history
                for pair in unique_pairs
                if (history := self._cache.get(pair)) is not None
            }

    async def _fetch_pair(
        self, batter_id: int, pitcher_id: int
    ) -> MatchupHistory | None:
        try:
            data = await asyncio.wait_for(
                asyncio.to_thread(self._fetcher, batter_id, pitcher_id),
                timeout=MATCHUP_FETCH_TIMEOUT_SECONDS,
            )
            return calculate_matchup_history(_records_from_data(data))
        except Exception as exc:  # noqa: BLE001 - any matchup failure should degrade gracefully.
            LOGGER.info(
                "Could not fetch matchup data for batter %s vs pitcher %s: %s",
                batter_id,
                pitcher_id,
                exc,
            )
            return None

    async def hand_history(
        self, batter_id: int, throws: str, start: date, end: date
    ) -> MatchupHistory | None:
        histories = await self.hand_history_many([(batter_id, throws, start, end)])
        return histories.get((batter_id, throws, start, end))

    async def hand_history_many(
        self, windows: Iterable[HandWindow]
    ) -> dict[HandWindow, MatchupHistory]:
        """Recent form against one pitching hand, totalled from Statcast.

        The window itself is part of the cache key, so a card posted tomorrow
        refetches instead of reusing a range that has since rolled forward.
        """
        unique_windows = set(windows)
        if not unique_windows:
            return {}

        async with self._lock:
            missing = [
                window for window in unique_windows if window not in self._hand_cache
            ]

        if missing:
            fetched = await asyncio.gather(
                *(self._fetch_hand_window(*window) for window in missing)
            )
            async with self._lock:
                for window, history in zip(missing, fetched, strict=True):
                    # As with a pair, a failed fetch is left uncached so a
                    # transient Statcast outage does not stick to the window.
                    if history is not None:
                        self._hand_cache.setdefault(window, history)

        async with self._lock:
            return {
                window: history
                for window in unique_windows
                if (history := self._hand_cache.get(window)) is not None
            }

    async def _fetch_hand_window(
        self, batter_id: int, throws: str, start: date, end: date
    ) -> MatchupHistory | None:
        try:
            data = await asyncio.wait_for(
                asyncio.to_thread(
                    self._hand_fetcher, batter_id, throws, start, end
                ),
                timeout=MATCHUP_FETCH_TIMEOUT_SECONDS,
            )
            return calculate_matchup_history(_records_from_data(data))
        except Exception as exc:  # noqa: BLE001 - recent form is a nicety, not a reason to drop the card.
            LOGGER.info(
                "Could not fetch %s-handed form for batter %s from %s to %s: %s",
                throws,
                batter_id,
                start,
                end,
                exc,
            )
            return None


def calculate_matchup_history(records: Iterable[Mapping[str, Any]]) -> MatchupHistory:
    """Total a batter's plate appearances against one pitcher.

    Statcast reports one row per pitch, so only rows carrying an ``events``
    value close out a plate appearance and count here.
    """
    plate_appearances = [
        record for record in records if _clean_event(record.get("events")) is not None
    ]

    woba_numerator = 0.0
    woba_denominator = 0.0
    at_bats = 0
    hits = 0
    doubles = 0
    triples = 0
    home_runs = 0
    walks = 0
    strikeouts = 0
    total_bases = 0

    for record in plate_appearances:
        woba_value = _safe_float(record.get("woba_value"))
        woba_denom = _safe_float(record.get("woba_denom"))
        if woba_value is not None and woba_denom is not None and woba_denom > 0:
            woba_numerator += woba_value
            woba_denominator += woba_denom

        event = _clean_event(record.get("events"))
        if event is None:
            continue
        if event in WALK_EVENTS:
            walks += 1
        if event in STRIKEOUT_EVENTS:
            strikeouts += 1
        if event in NON_AT_BAT_EVENTS:
            continue

        at_bats += 1
        if event in HIT_EVENTS:
            hits += 1
            if event == "double":
                doubles += 1
                total_bases += 2
            elif event == "triple":
                triples += 1
                total_bases += 3
            elif event == "home_run":
                home_runs += 1
                total_bases += 4
            else:
                total_bases += 1

    return MatchupHistory(
        plate_appearances=len(plate_appearances),
        at_bats=at_bats,
        hits=hits,
        doubles=doubles,
        triples=triples,
        home_runs=home_runs,
        walks=walks,
        strikeouts=strikeouts,
        average=hits / at_bats if at_bats else None,
        slugging_percentage=total_bases / at_bats if at_bats else None,
        woba=woba_numerator / woba_denominator if woba_denominator > 0 else None,
        woba_denominator=woba_denominator,
    )


def calculate_matchup_annotation(
    records: Iterable[Mapping[str, Any]], min_pa: int = 5
) -> MatchupAnnotation | None:
    return annotation_from_history(calculate_matchup_history(records), min_pa)


def annotation_from_history(
    history: MatchupHistory, min_pa: int = 5
) -> MatchupAnnotation | None:
    """Flag a matchup hot or cold, preferring wOBA and falling back to average.

    A strikeout-heavy sample can leave wOBA thin even when the plate appearance
    count clears the bar, so average covers that case.
    """
    if history.plate_appearances < min_pa:
        return None

    if history.woba is not None and history.woba_denominator >= min_pa:
        return _classify_metric(
            "wOBA", history.woba, int(round(history.woba_denominator))
        )

    if history.average is None:
        return None
    return _classify_metric("AVG", history.average, history.plate_appearances)


def _classify_metric(
    metric_name: str, metric_value: float, plate_appearances: int
) -> MatchupAnnotation | None:
    if metric_name == "wOBA":
        if metric_value >= HOT_WOBA:
            return MatchupAnnotation(HOT_EMOJI, metric_name, metric_value, plate_appearances)
        if metric_value <= COLD_WOBA:
            return MatchupAnnotation(COLD_EMOJI, metric_name, metric_value, plate_appearances)
        return None

    if metric_value >= HOT_AVERAGE:
        return MatchupAnnotation(HOT_EMOJI, metric_name, metric_value, plate_appearances)
    if metric_value <= COLD_AVERAGE:
        return MatchupAnnotation(COLD_EMOJI, metric_name, metric_value, plate_appearances)
    return None


def _records_from_data(data: Any) -> list[Mapping[str, Any]]:
    if data is None:
        return []
    if hasattr(data, "empty") and data.empty:
        return []
    if hasattr(data, "to_dict"):
        records = data.to_dict("records")
        return records if isinstance(records, list) else []
    if isinstance(data, list):
        return [record for record in data if isinstance(record, Mapping)]
    return []


def _fetch_statcast_batter_pitcher(batter_id: int, pitcher_id: int) -> Any:
    return _fetch_statcast_csv(statcast_matchup_csv_url(batter_id, pitcher_id))


def _fetch_statcast_batter_hand(
    batter_id: int, throws: str, start: date, end: date
) -> Any:
    return _fetch_statcast_csv(
        statcast_hand_window_csv_url(batter_id, throws, start, end)
    )


def _fetch_statcast_csv(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "orioles-discord-bot"},
    )
    with urllib.request.urlopen(request, timeout=MATCHUP_FETCH_TIMEOUT_SECONDS) as response:
        payload = response.read().decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(payload)))


def statcast_matchup_csv_url(batter_id: int | str, pitcher_id: int | str) -> str:
    return f"{STATCAST_MATCHUP_CSV_URL}?{savant_matchup_params(batter_id, pitcher_id)}"


def statcast_hand_window_csv_url(
    batter_id: int | str, throws: str, start: date, end: date
) -> str:
    params = savant_hand_window_params(batter_id, throws, start, end)
    return f"{STATCAST_MATCHUP_CSV_URL}?{params}"


def _clean_event(value: Any) -> str | None:
    if value is None:
        return None
    event = str(value).strip()
    if not event or event.lower() in {"nan", "none"}:
        return None
    return event.lower()


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number
