from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence
from datetime import date, timedelta

from .dates import stats_window_ending
from .formatting import format_innings
from .mlb import MlbApiError, MlbClient
from .models import (
    BULLPEN_HEAVY_INNINGS,
    BULLPEN_HEAVY_PITCHES,
    BULLPEN_LOG_DAYS,
    BULLPEN_WORKLOAD_DAYS,
    PitchingGame,
    PlayerRef,
    RELIEVER_AVAILABLE,
    RELIEVER_CAUTION,
    RELIEVER_UNAVAILABLE,
    RELIEVER_UNKNOWN,
    RelieverStatus,
)


LOGGER = logging.getLogger(__name__)

PITCHER_POSITIONS = {"P", "RHP", "LHP", "SP", "RP"}
# Game logs are pulled one pitcher at a time, so a staff would otherwise open a
# dozen sockets at once against a public API.
MAX_CONCURRENT_LOOKUPS = 5


class BullpenService:
    """Reads the bullpen's recent workload and grades who can pitch today.

    MLB publishes no availability feed, so the read is inferred from each
    reliever's game log: who threw today, who is on a back-to-back, and who is
    rested. The judgement is deliberately conservative and always shows the
    outings behind it, so a manager's actual decision can be second-guessed.
    """

    def __init__(
        self,
        log_days: int = BULLPEN_LOG_DAYS,
        workload_days: int = BULLPEN_WORKLOAD_DAYS,
        max_concurrent_lookups: int = MAX_CONCURRENT_LOOKUPS,
    ) -> None:
        self.log_days = log_days
        self.workload_days = workload_days
        self.max_concurrent_lookups = max_concurrent_lookups

    async def relievers(
        self, client: MlbClient, today: date
    ) -> tuple[RelieverStatus, ...]:
        roster = await client.fetch_roster()
        pitchers = [player for player in roster if is_pitcher(player)]
        if not pitchers:
            return ()

        window = stats_window_ending(self.log_days, today)
        semaphore = asyncio.Semaphore(self.max_concurrent_lookups)

        async def log_for(player: PlayerRef) -> tuple[PitchingGame, ...] | None:
            async with semaphore:
                try:
                    return await client.fetch_pitching_game_log(
                        player.player_id, window
                    )
                except MlbApiError as exc:
                    # One unreachable game log should cost that pitcher his
                    # workload detail, not the whole bullpen card.
                    LOGGER.warning(
                        "Bullpen workload unavailable for %s: %s", player.name, exc
                    )
                    return None

        logs = await asyncio.gather(*(log_for(player) for player in pitchers))

        statuses = [
            assess_reliever(player, log, today, self.workload_days)
            for player, log in zip(pitchers, logs)
            if log is None or not is_starter(log)
        ]
        return tuple(sorted(statuses, key=_status_sort_key))


def is_pitcher(player: PlayerRef) -> bool:
    position = (player.position or "").strip().upper()
    return position in PITCHER_POSITIONS


def is_starter(outings: Sequence[PitchingGame]) -> bool:
    """Whether a game log reads as a rotation arm rather than a bullpen one.

    A reliever makes the odd opener start, and a starter occasionally works in
    relief, so the call is made on the balance of a month rather than on any
    single appearance.
    """
    if not outings:
        return False
    starts = sum(1 for outing in outings if outing.stat.games_started)
    return starts > 0 and starts * 2 >= len(outings)


def assess_reliever(
    player: PlayerRef,
    outings: Iterable[PitchingGame] | None,
    today: date,
    workload_days: int = BULLPEN_WORKLOAD_DAYS,
) -> RelieverStatus:
    """Grade one reliever from his recent outings, newest first."""
    if outings is None:
        return RelieverStatus(
            player=player,
            availability=RELIEVER_UNKNOWN,
            reason="Recent workload unavailable",
        )

    recent = tuple(
        sorted(
            (
                outing
                for outing in outings
                if 0 <= (today - outing.game_date).days < workload_days
            ),
            key=lambda outing: outing.game_date,
            reverse=True,
        )
    )
    if not recent:
        return RelieverStatus(
            player=player,
            availability=RELIEVER_AVAILABLE,
            reason=f"No outings in the last {workload_days} days",
        )

    last = recent[0]
    days_rest = (today - last.game_date).days
    if days_rest == 0:
        return RelieverStatus(
            player=player,
            availability=RELIEVER_UNAVAILABLE,
            reason=f"Pitched today ({_workload_text(last)})",
            outings=recent,
            days_rest=days_rest,
        )

    if days_rest == 1 and any(
        outing.game_date == last.game_date - timedelta(days=1) for outing in recent[1:]
    ):
        return RelieverStatus(
            player=player,
            availability=RELIEVER_UNAVAILABLE,
            reason="Pitched on back-to-back days",
            outings=recent,
            days_rest=days_rest,
        )

    if days_rest == 1 and is_heavy(last):
        return RelieverStatus(
            player=player,
            availability=RELIEVER_CAUTION,
            reason=f"Heavy work yesterday ({_workload_text(last)})",
            outings=recent,
            days_rest=days_rest,
        )

    if days_rest == 1:
        return RelieverStatus(
            player=player,
            availability=RELIEVER_AVAILABLE,
            reason=f"Light work yesterday ({_workload_text(last)})",
            outings=recent,
            days_rest=days_rest,
        )

    return RelieverStatus(
        player=player,
        availability=RELIEVER_AVAILABLE,
        reason=f"{days_rest} days of rest",
        outings=recent,
        days_rest=days_rest,
    )


def is_heavy(outing: PitchingGame) -> bool:
    """Whether an outing was long enough to cost a reliever the next day."""
    if outing.stat.pitches >= BULLPEN_HEAVY_PITCHES:
        return True
    innings = outing.stat.innings_pitched
    return innings is not None and innings >= BULLPEN_HEAVY_INNINGS


def _workload_text(outing: PitchingGame) -> str:
    innings = outing.stat.innings_pitched
    parts = [
        f"{format_innings(innings)} IP" if innings is not None else "IP unknown"
    ]
    if outing.stat.pitches:
        parts.append(f"{outing.stat.pitches} P")
    return ", ".join(parts)


AVAILABILITY_ORDER = {
    RELIEVER_AVAILABLE: 0,
    RELIEVER_CAUTION: 1,
    RELIEVER_UNAVAILABLE: 2,
    RELIEVER_UNKNOWN: 3,
}


def _status_sort_key(status: RelieverStatus) -> tuple[int, int, str]:
    """Freshest arms first, so the usable half of the pen reads off the top."""
    rest = status.days_rest
    return (
        AVAILABILITY_ORDER.get(status.availability, len(AVAILABILITY_ORDER)),
        -(rest if rest is not None else BULLPEN_WORKLOAD_DAYS),
        status.player.name,
    )
