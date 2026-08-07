from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import urlencode

from .models import GameInfo, MatchupAnnotation


LOGGER = logging.getLogger(__name__)

HOT_EMOJI = "🔥"
COLD_EMOJI = "🧊"
HOT_WOBA = 0.400
COLD_WOBA = 0.280
HOT_AVERAGE = 0.300
COLD_AVERAGE = 0.200
MATCHUP_FETCH_TIMEOUT_SECONDS = 20

HIT_EVENTS = {"single", "double", "triple", "home_run"}
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
STATCAST_MATCHUP_CSV_PATH = "/statcast_search/csv"


class MatchupService:
    def __init__(self, min_pa: int = 5, fetcher: FetchRecords | None = None) -> None:
        self.min_pa = min_pa
        self._fetcher = fetcher or _fetch_statcast_batter_pitcher
        self._cache: dict[tuple[int, int], MatchupAnnotation | None] = {}
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
                for pair, annotation in zip(missing, fetched, strict=True):
                    self._cache.setdefault(pair, annotation)

        async with self._lock:
            return {
                pair: annotation
                for pair in unique_pairs
                if (annotation := self._cache.get(pair)) is not None
            }

    async def _fetch_pair(
        self, batter_id: int, pitcher_id: int
    ) -> MatchupAnnotation | None:
        try:
            data = await asyncio.wait_for(
                asyncio.to_thread(self._fetcher, batter_id, pitcher_id),
                timeout=MATCHUP_FETCH_TIMEOUT_SECONDS,
            )
            return calculate_matchup_annotation(_records_from_data(data), self.min_pa)
        except Exception as exc:  # noqa: BLE001 - any matchup failure should degrade gracefully.
            LOGGER.info(
                "Could not fetch matchup data for batter %s vs pitcher %s: %s",
                batter_id,
                pitcher_id,
                exc,
            )
            return None


def calculate_matchup_annotation(
    records: Iterable[Mapping[str, Any]], min_pa: int = 5
) -> MatchupAnnotation | None:
    plate_appearances = [
        record for record in records if _clean_event(record.get("events")) is not None
    ]
    pa_count = len(plate_appearances)
    if pa_count < min_pa:
        return None

    woba_numerator = 0.0
    woba_denominator = 0.0
    for record in plate_appearances:
        woba_value = _safe_float(record.get("woba_value"))
        woba_denom = _safe_float(record.get("woba_denom"))
        if woba_value is None or woba_denom is None or woba_denom <= 0:
            continue
        woba_numerator += woba_value
        woba_denominator += woba_denom

    if woba_denominator >= min_pa:
        woba = woba_numerator / woba_denominator
        return _classify_metric("wOBA", woba, int(round(woba_denominator)))

    hits = 0
    at_bats = 0
    for record in plate_appearances:
        event = _clean_event(record.get("events"))
        if event is None or event in NON_AT_BAT_EVENTS:
            continue
        at_bats += 1
        if event in HIT_EVENTS:
            hits += 1
    if at_bats == 0:
        return None
    average = hits / at_bats
    return _classify_metric("AVG", average, pa_count)


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
    import pybaseball

    statcast_batter_pitcher = getattr(pybaseball, "statcast_batter_pitcher", None)
    if statcast_batter_pitcher is not None:
        try:
            return statcast_batter_pitcher(batter_id, pitcher_id)
        except TypeError:
            LOGGER.debug("pybaseball.statcast_batter_pitcher signature did not accept IDs")

    from pybaseball.datasources.statcast import get_statcast_data_from_csv_url

    return get_statcast_data_from_csv_url(_statcast_matchup_csv_path(batter_id, pitcher_id))


def _statcast_matchup_csv_path(batter_id: int, pitcher_id: int) -> str:
    params = urlencode(
        {
            "all": "true",
            "hfBatters": f"{batter_id}|",
            "hfPitchers": f"{pitcher_id}|",
            "player_type": "batter",
            "type": "details",
        }
    )
    return f"{STATCAST_MATCHUP_CSV_PATH}?{params}"


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
