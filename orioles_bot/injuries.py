from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta

from .mlb import MlbApiError, MlbClient
from .models import (
    INJURY_TRANSACTION_LOOKBACK_DAYS,
    InjuredPlayer,
    RehabAssignment,
    RosterEntry,
    TransactionInfo,
)


LOGGER = logging.getLogger(__name__)

# Rehab game logs are pulled one player at a time, so a long injury list would
# otherwise open a socket per player at once against a public API.
MAX_CONCURRENT_LOOKUPS = 5

_PLACEMENT_PATTERN = re.compile(r"\b(?:placed|transferred|moved)\b")
_ACTIVATION_PATTERN = re.compile(r"\b(?:activated|reinstated)\b")
_REHAB_END_PATTERN = re.compile(
    r"\b(?:ended|end|cancelled|canceled|completed|recalled|reinstated|activated)\b"
)
_RETROACTIVE_PATTERN = re.compile(
    r"retroactive to ([A-Z][a-z]+ \d{1,2})(?:, (\d{4}))?", re.IGNORECASE
)
_INJURY_NOTE_PATTERN = re.compile(
    r"injured list[^.]*?\bwith (?:a |an |the )?(?P<note>[^.]+)", re.IGNORECASE
)
_REHAB_TEAM_PATTERN = re.compile(
    r"rehab assignment (?:to|with) (?P<team>[^.]+)", re.IGNORECASE
)


class InjuryService:
    """Builds the current injured list and the story behind each name.

    The Stats API has no injury report: the roster says only that a player is
    on the list, so the day he went on, the injury itself, and any rehab
    assignment are read back out of the team's transaction feed, and rehab
    appearances are counted from his minor league game log.
    """

    def __init__(
        self,
        lookback_days: int = INJURY_TRANSACTION_LOOKBACK_DAYS,
        max_concurrent_lookups: int = MAX_CONCURRENT_LOOKUPS,
    ) -> None:
        self.lookback_days = lookback_days
        self.max_concurrent_lookups = max_concurrent_lookups

    async def injured_list(
        self, client: MlbClient, today: date
    ) -> tuple[InjuredPlayer, ...]:
        entries = await client.fetch_roster_entries()
        injured = [entry for entry in entries if entry.is_injured]
        if not injured:
            return ()

        try:
            transactions = await client.fetch_transactions_between(
                today - timedelta(days=self.lookback_days), today
            )
        except MlbApiError as exc:
            # The roster alone still names everyone on the list, which is the
            # bulk of the answer; only the dates and notes are lost.
            LOGGER.warning("Injury transaction history unavailable: %s", exc)
            transactions = []

        players = [
            build_injured_player(entry, transactions, today) for entry in injured
        ]
        players = await self._with_rehab_games(client, players, today)
        return tuple(sorted(players, key=_injury_sort_key))

    async def _with_rehab_games(
        self, client: MlbClient, players: Sequence[InjuredPlayer], today: date
    ) -> list[InjuredPlayer]:
        semaphore = asyncio.Semaphore(self.max_concurrent_lookups)

        async def counted(player: InjuredPlayer) -> InjuredPlayer:
            rehab = player.rehab
            if rehab is None:
                return player
            async with semaphore:
                try:
                    game_dates = await client.fetch_minor_league_game_dates(
                        player.player.player_id, rehab.started, today
                    )
                except MlbApiError as exc:
                    # One unreadable game log costs that player his rehab
                    # count, not the whole injury list.
                    LOGGER.warning(
                        "Rehab game log unavailable for %s: %s",
                        player.player.name,
                        exc,
                    )
                    return _with_rehab(player, _unknown_games(rehab))
            return _with_rehab(player, _with_games(rehab, game_dates))

        return list(await asyncio.gather(*(counted(player) for player in players)))


def build_injured_player(
    entry: RosterEntry,
    transactions: Sequence[TransactionInfo],
    today: date,
) -> InjuredPlayer:
    """Assemble one injured player from his roster spot and transaction trail."""
    history = sorted(
        (
            transaction
            for transaction in transactions
            if _names_player(transaction, entry.player.player_id)
        ),
        key=lambda transaction: transaction.date,
    )
    placement = _latest(history, is_injury_placement)
    since = placement.date if placement is not None else None
    rehab = _current_rehab(history, since)
    latest = history[-1] if history else None

    return InjuredPlayer(
        player=entry.player,
        status=entry.status_description or "Injured list",
        status_code=entry.status_code,
        placed_on=placement.date if placement is not None else None,
        retroactive_to=(
            retroactive_date(placement.description, placement.date)
            if placement is not None
            else None
        ),
        injury_note=(
            injury_note(placement.description) if placement is not None else None
        ),
        latest_update=latest.description if latest is not None else None,
        latest_update_date=latest.date if latest is not None else None,
        rehab=rehab,
    )


def _current_rehab(
    history: Sequence[TransactionInfo], since: date | None
) -> RehabAssignment | None:
    """The rehab assignment in force now, if the player is on one.

    A rehab stint can be cut short and restarted, so only the newest assignment
    counts, and it counts only when nothing later ended it.
    """
    relevant = [
        transaction
        for transaction in history
        if since is None or transaction.date >= since
    ]
    start = _latest(relevant, is_rehab_assignment)
    if start is None:
        return None
    ended = _latest(
        [
            transaction
            for transaction in relevant
            if transaction.date >= start.date and transaction is not start
        ],
        is_rehab_end,
    )
    if ended is not None:
        return None
    return RehabAssignment(
        started=start.date,
        team_name=rehab_team(start.description),
        description=start.description,
    )


def _latest(
    transactions: Sequence[TransactionInfo],
    predicate: Callable[[TransactionInfo], bool],
) -> TransactionInfo | None:
    matches = [transaction for transaction in transactions if predicate(transaction)]
    return matches[-1] if matches else None


def _names_player(transaction: TransactionInfo, player_id: int) -> bool:
    if transaction.player_id == player_id:
        return True
    return any(player.player_id == player_id for player in transaction.players)


def _text(transaction: TransactionInfo) -> str:
    return f"{transaction.type_description} {transaction.description}".casefold()


def is_injury_placement(transaction: TransactionInfo) -> bool:
    """Whether a move put the player on the injured list."""
    text = _text(transaction)
    if "injured list" not in text:
        return False
    if _ACTIVATION_PATTERN.search(text):
        return False
    return _PLACEMENT_PATTERN.search(text) is not None


def is_activation(transaction: TransactionInfo) -> bool:
    text = _text(transaction)
    return "injured list" in text and _ACTIVATION_PATTERN.search(text) is not None


def is_rehab_assignment(transaction: TransactionInfo) -> bool:
    text = _text(transaction)
    if "rehab assignment" not in text:
        return False
    return _REHAB_END_PATTERN.search(text) is None


def is_rehab_end(transaction: TransactionInfo) -> bool:
    """Whether a move closed out a rehab assignment.

    An activation off the injured list ends a rehab stint too, even when the
    wording never mentions rehab.
    """
    text = _text(transaction)
    if is_activation(transaction):
        return True
    return "rehab assignment" in text and _REHAB_END_PATTERN.search(text) is not None


def retroactive_date(description: str, announced: date) -> date | None:
    """The date a placement was backdated to, when the wording carries one.

    Eligibility to return counts from this day rather than the day the move was
    announced, so it is the date worth showing.
    """
    match = _RETROACTIVE_PATTERN.search(description or "")
    if match is None:
        return None
    day = match.group(1)
    year = match.group(2)
    if year:
        return _parse_month_day(day, int(year))

    parsed = _parse_month_day(day, announced.year)
    if parsed is None:
        return None
    # A move announced in January backdated to December belongs to the year
    # before, since a placement cannot be backdated into the future.
    if parsed > announced:
        return _parse_month_day(day, announced.year - 1)
    return parsed


def _parse_month_day(text: str, year: int) -> date | None:
    for fmt in ("%B %d", "%b %d"):
        try:
            parsed = datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
        try:
            return date(year, parsed.month, parsed.day)
        except ValueError:
            return None
    return None


def injury_note(description: str) -> str | None:
    """The injury itself, pulled out of a placement description.

    MLB writes it either inline — "on the 10-day injured list with a left
    hamstring strain" — or as a sentence of its own after the move.
    """
    text = (description or "").strip()
    if not text:
        return None
    match = _INJURY_NOTE_PATTERN.search(text)
    if match is not None:
        return _clean_note(match.group("note"))

    sentences = [part.strip() for part in text.split(". ") if part.strip()]
    if len(sentences) < 2:
        return None
    tail = sentences[-1]
    lowered = tail.casefold()
    if "injured list" in lowered or _PLACEMENT_PATTERN.search(lowered):
        return None
    return _clean_note(tail)


def rehab_team(description: str) -> str | None:
    match = _REHAB_TEAM_PATTERN.search(description or "")
    return _clean_note(match.group("team")) if match is not None else None


def _clean_note(text: str) -> str | None:
    cleaned = text.strip().strip(".").strip()
    if not cleaned:
        return None
    # "retroactive to ..." rides along on the same clause often enough to be
    # worth trimming, since the date is already shown on its own line.
    cleaned = re.split(r",? retroactive to ", cleaned, flags=re.IGNORECASE)[0]
    return cleaned.strip().strip(".") or None


def _with_rehab(player: InjuredPlayer, rehab: RehabAssignment) -> InjuredPlayer:
    return replace(player, rehab=rehab)


def _with_games(rehab: RehabAssignment, game_dates: Sequence[date]) -> RehabAssignment:
    return replace(rehab, game_dates=tuple(sorted(game_dates)), games_known=True)


def _unknown_games(rehab: RehabAssignment) -> RehabAssignment:
    return replace(rehab, game_dates=(), games_known=False)


def _injury_sort_key(player: InjuredPlayer) -> tuple[int, float, str]:
    """Rehabbing players first, then the newest placements.

    A player already in games is the closest thing the list has to news, and a
    fresh injury is the next most likely reason someone is asking.
    """
    effective = player.effective_date
    return (
        0 if player.rehab is not None else 1,
        -effective.toordinal() if effective is not None else 0.0,
        player.player.name,
    )
