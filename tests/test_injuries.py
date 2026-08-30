from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest

from orioles_bot.embeds import injuries_embed
from orioles_bot.formatting import (
    format_injured_player,
    format_injury_placement,
    format_rehab,
)
from orioles_bot.injuries import (
    InjuryService,
    build_injured_player,
    injury_note,
    is_injury_placement,
    is_rehab_assignment,
    is_rehab_end,
    rehab_team,
    retroactive_date,
)
from orioles_bot.mlb import MlbApiError, parse_game_log_dates, parse_roster_entries
from orioles_bot.models import (
    InjuredPlayer,
    PlayerRef,
    RehabAssignment,
    RosterEntry,
    TransactionInfo,
    TransactionPlayer,
)


TODAY = date(2026, 8, 12)
GUNNAR = PlayerRef(683002, "Grayson Rodriguez", "P")


def transaction(
    day: date,
    description: str,
    player: PlayerRef = GUNNAR,
    type_description: str = "Status Change",
    transaction_id: str | None = None,
) -> TransactionInfo:
    return TransactionInfo(
        transaction_id=transaction_id or f"{day.isoformat()}-{description[:12]}",
        date=day,
        player_id=player.player_id,
        player_name=player.name,
        type_description=type_description,
        description=description,
        headshot_url=None,
        players=(TransactionPlayer(player.player_id, player.name),),
    )


def test_a_roster_entry_carries_its_injured_list_status() -> None:
    payload: dict[str, Any] = {
        "roster": [
            {
                "person": {"id": 1, "fullName": "Healthy Hitter"},
                "position": {"abbreviation": "SS"},
                "status": {"code": "A", "description": "Active"},
            },
            {
                "person": {"id": 2, "fullName": "Hurt Arm"},
                "position": {"abbreviation": "P"},
                "status": {"code": "D60", "description": "60-Day Injured List"},
            },
        ]
    }

    entries = parse_roster_entries(payload)

    assert [entry.player.name for entry in entries] == ["Healthy Hitter", "Hurt Arm"]
    assert not entries[0].is_injured
    assert entries[1].is_injured
    assert entries[1].status_description == "60-Day Injured List"


def test_an_unfamiliar_injured_list_code_is_still_read_from_its_description() -> None:
    entry = RosterEntry(
        player=GUNNAR, status_code="D99", status_description="99-Day Injured List"
    )

    assert entry.is_injured


def test_game_log_dates_are_deduplicated_across_stat_groups() -> None:
    payload: dict[str, Any] = {
        "stats": [
            {
                "group": {"displayName": "hitting"},
                "splits": [{"date": "2026-08-10"}, {"date": "2026-08-08"}],
            },
            {
                "group": {"displayName": "pitching"},
                "splits": [{"date": "2026-08-10"}, {"date": "nonsense"}],
            },
        ]
    }

    assert parse_game_log_dates(payload) == (date(2026, 8, 8), date(2026, 8, 10))


def test_a_placement_is_told_apart_from_an_activation() -> None:
    placed = transaction(
        date(2026, 7, 1),
        "Baltimore Orioles placed RHP Grayson Rodriguez on the 15-day injured list.",
    )
    activated = transaction(
        date(2026, 8, 1),
        "Baltimore Orioles activated RHP Grayson Rodriguez from the 15-day injured list.",
    )

    assert is_injury_placement(placed)
    assert not is_injury_placement(activated)
    assert is_rehab_end(activated)


def test_a_rehab_assignment_is_told_apart_from_the_end_of_one() -> None:
    sent = transaction(
        date(2026, 8, 5),
        "Baltimore Orioles sent RHP Grayson Rodriguez on a rehab assignment to Norfolk Tides.",
        type_description="Assigned",
    )
    ended = transaction(
        date(2026, 8, 9),
        "Baltimore Orioles ended the rehab assignment of RHP Grayson Rodriguez.",
    )

    assert is_rehab_assignment(sent)
    assert not is_rehab_assignment(ended)
    assert is_rehab_end(ended)
    assert rehab_team(sent.description) == "Norfolk Tides"


def test_a_retroactive_date_is_read_out_of_the_wording() -> None:
    assert retroactive_date(
        "placed on the 10-day injured list retroactive to June 12, 2026.",
        date(2026, 6, 15),
    ) == date(2026, 6, 12)
    # No year given: the announcement's year, unless that puts it in the future.
    assert retroactive_date(
        "placed on the 10-day injured list retroactive to June 12.", date(2026, 6, 15)
    ) == date(2026, 6, 12)
    assert retroactive_date(
        "placed on the 60-day injured list retroactive to December 28.",
        date(2027, 1, 3),
    ) == date(2026, 12, 28)
    assert retroactive_date("placed on the 10-day injured list.", TODAY) is None


def test_the_injury_itself_is_read_out_of_a_placement() -> None:
    assert (
        injury_note(
            "Baltimore Orioles placed RHP Grayson Rodriguez on the 15-day injured "
            "list with a right elbow strain."
        )
        == "right elbow strain"
    )
    assert (
        injury_note(
            "Baltimore Orioles placed RHP Grayson Rodriguez on the 60-day injured "
            "list retroactive to March 26. Right lat strain."
        )
        == "Right lat strain"
    )
    assert (
        injury_note(
            "Baltimore Orioles placed RHP Grayson Rodriguez on the 10-day injured list."
        )
        is None
    )


def test_an_injured_player_is_assembled_from_his_transaction_trail() -> None:
    entry = RosterEntry(GUNNAR, "D15", "15-Day Injured List")
    history = [
        transaction(
            date(2026, 6, 20),
            "Baltimore Orioles placed RHP Grayson Rodriguez on the 15-day injured "
            "list retroactive to June 18, 2026. Right elbow inflammation.",
        ),
        transaction(
            date(2026, 8, 5),
            "Baltimore Orioles sent RHP Grayson Rodriguez on a rehab assignment to "
            "Norfolk Tides.",
            type_description="Assigned",
        ),
    ]

    player = build_injured_player(entry, history, TODAY)

    assert player.status == "15-Day Injured List"
    assert player.placed_on == date(2026, 6, 20)
    assert player.retroactive_to == date(2026, 6, 18)
    assert player.effective_date == date(2026, 6, 18)
    assert player.days_out(TODAY) == 55
    assert player.injury_note == "Right elbow inflammation"
    assert player.rehab is not None
    assert player.rehab.started == date(2026, 8, 5)
    assert player.rehab.team_name == "Norfolk Tides"
    assert player.latest_update_date == date(2026, 8, 5)


def test_a_finished_rehab_assignment_is_not_reported_as_current() -> None:
    entry = RosterEntry(GUNNAR, "D15", "15-Day Injured List")
    history = [
        transaction(
            date(2026, 6, 20),
            "Baltimore Orioles placed RHP Grayson Rodriguez on the 15-day injured list.",
        ),
        transaction(
            date(2026, 7, 20),
            "Baltimore Orioles sent RHP Grayson Rodriguez on a rehab assignment to "
            "Norfolk Tides.",
            type_description="Assigned",
        ),
        transaction(
            date(2026, 7, 28),
            "Baltimore Orioles ended the rehab assignment of RHP Grayson Rodriguez.",
        ),
    ]

    player = build_injured_player(entry, history, TODAY)

    assert player.rehab is None
    assert player.placed_on == date(2026, 6, 20)


def test_a_transaction_naming_someone_else_is_ignored() -> None:
    entry = RosterEntry(GUNNAR, "D10", "10-Day Injured List")
    other = PlayerRef(999, "Somebody Else", "SS")
    history = [
        transaction(
            date(2026, 7, 1),
            "Baltimore Orioles placed SS Somebody Else on the 10-day injured list.",
            player=other,
        )
    ]

    player = build_injured_player(entry, history, TODAY)

    assert player.placed_on is None
    assert player.effective_date is None
    assert player.days_out(TODAY) is None


class StubClient:
    def __init__(
        self,
        entries: tuple[RosterEntry, ...],
        transactions: Any,
        game_dates: dict[int, Any] | None = None,
    ) -> None:
        self.entries = entries
        self.transactions = transactions
        self.game_dates = game_dates or {}
        self.requested: list[int] = []

    async def fetch_roster_entries(
        self, roster_type: str = "fullSeason"
    ) -> tuple[RosterEntry, ...]:
        return self.entries

    async def fetch_transactions_between(
        self, start: date, end: date
    ) -> list[TransactionInfo]:
        if isinstance(self.transactions, Exception):
            raise self.transactions
        return list(self.transactions)

    async def fetch_minor_league_game_dates(
        self, player_id: int, start: date, end: date
    ) -> tuple[date, ...]:
        self.requested.append(player_id)
        result = self.game_dates.get(player_id, ())
        if isinstance(result, Exception):
            raise result
        return tuple(result)


def test_the_service_counts_rehab_games_and_leaves_healthy_players_out() -> None:
    entries = (
        RosterEntry(PlayerRef(1, "Healthy Hitter", "SS"), "A", "Active"),
        RosterEntry(GUNNAR, "D15", "15-Day Injured List"),
    )
    transactions = [
        transaction(
            date(2026, 6, 20),
            "Baltimore Orioles placed RHP Grayson Rodriguez on the 15-day injured list.",
        ),
        transaction(
            date(2026, 8, 5),
            "Baltimore Orioles sent RHP Grayson Rodriguez on a rehab assignment to "
            "Norfolk Tides.",
            type_description="Assigned",
        ),
    ]
    client = StubClient(
        entries,
        transactions,
        {GUNNAR.player_id: [date(2026, 8, 6), date(2026, 8, 8), date(2026, 8, 11)]},
    )

    injured = asyncio.run(InjuryService().injured_list(client, TODAY))

    assert [player.player.name for player in injured] == [GUNNAR.name]
    assert client.requested == [GUNNAR.player_id]
    rehab = injured[0].rehab
    assert rehab is not None
    assert rehab.games == 3
    assert rehab.last_game == date(2026, 8, 11)


def test_an_unreadable_rehab_log_costs_only_the_game_count() -> None:
    entries = (RosterEntry(GUNNAR, "D15", "15-Day Injured List"),)
    transactions = [
        transaction(
            date(2026, 8, 5),
            "Baltimore Orioles sent RHP Grayson Rodriguez on a rehab assignment to "
            "Norfolk Tides.",
            type_description="Assigned",
        )
    ]
    client = StubClient(
        entries, transactions, {GUNNAR.player_id: MlbApiError("log down")}
    )

    injured = asyncio.run(InjuryService().injured_list(client, TODAY))

    rehab = injured[0].rehab
    assert rehab is not None
    assert not rehab.games_known
    assert "unavailable" in format_rehab(rehab)


def test_an_unreadable_transaction_feed_still_names_the_injured() -> None:
    entries = (RosterEntry(GUNNAR, "D60", "60-Day Injured List"),)
    client = StubClient(entries, MlbApiError("transactions down"))

    injured = asyncio.run(InjuryService().injured_list(client, TODAY))

    assert [player.player.name for player in injured] == [GUNNAR.name]
    assert injured[0].placed_on is None
    assert "date unavailable" in format_injury_placement(injured[0], TODAY)


def test_a_roster_failure_surfaces_to_the_caller() -> None:
    class FailingClient(StubClient):
        async def fetch_roster_entries(
            self, roster_type: str = "fullSeason"
        ) -> tuple[RosterEntry, ...]:
            raise MlbApiError("roster down")

    with pytest.raises(MlbApiError):
        asyncio.run(InjuryService().injured_list(FailingClient((), []), TODAY))


def test_rehabbing_players_are_listed_before_the_rest() -> None:
    other = PlayerRef(2, "Old Injury", "1B")
    entries = (
        RosterEntry(other, "D10", "10-Day Injured List"),
        RosterEntry(GUNNAR, "D15", "15-Day Injured List"),
    )
    transactions = [
        transaction(
            date(2026, 8, 1),
            "Baltimore Orioles placed 1B Old Injury on the 10-day injured list.",
            player=other,
        ),
        transaction(
            date(2026, 6, 1),
            "Baltimore Orioles placed RHP Grayson Rodriguez on the 15-day injured list.",
        ),
        transaction(
            date(2026, 8, 5),
            "Baltimore Orioles sent RHP Grayson Rodriguez on a rehab assignment to "
            "Norfolk Tides.",
            type_description="Assigned",
        ),
    ]
    client = StubClient(entries, transactions, {GUNNAR.player_id: [date(2026, 8, 6)]})

    injured = asyncio.run(InjuryService().injured_list(client, TODAY))

    assert [player.player.name for player in injured] == [GUNNAR.name, "Old Injury"]


def test_an_injury_line_reads_the_whole_story() -> None:
    player = InjuredPlayer(
        player=GUNNAR,
        status="15-Day Injured List",
        status_code="D15",
        placed_on=date(2026, 6, 20),
        retroactive_to=date(2026, 6, 18),
        injury_note="Right elbow inflammation",
        latest_update="Baltimore Orioles sent RHP Grayson Rodriguez on a rehab "
        "assignment to Norfolk Tides.",
        latest_update_date=date(2026, 8, 5),
        rehab=RehabAssignment(
            started=date(2026, 8, 5),
            team_name="Norfolk Tides",
            game_dates=(date(2026, 8, 6), date(2026, 8, 11)),
        ),
    )

    line = format_injured_player(player, TODAY)

    assert "Grayson Rodriguez" in line
    assert "15-Day Injured List" in line
    assert "Jun 18, 2026" in line
    assert "55 days" in line
    assert "Right elbow inflammation" in line
    assert "2 rehab games" in line
    assert "Norfolk Tides" in line
    assert "Latest:" in line


def test_the_embed_groups_by_list_and_says_when_nobody_is_hurt() -> None:
    short = InjuredPlayer(
        player=GUNNAR, status="15-Day Injured List", placed_on=date(2026, 8, 1)
    )
    long = InjuredPlayer(
        player=PlayerRef(3, "Long Injury", "OF"),
        status="60-Day Injured List",
        placed_on=date(2026, 4, 1),
    )

    embed = injuries_embed((short, long), TODAY)

    assert [field.name for field in embed.fields] == [
        "15-Day Injured List (1)",
        "60-Day Injured List (1)",
    ]

    empty = injuries_embed((), TODAY)
    assert empty.fields == []
    assert empty.description is not None
    assert "No Orioles are on the injured list." in empty.description
