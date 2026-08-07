from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from orioles_bot.dates import stats_window
from orioles_bot.formatting import (
    format_era,
    format_hitting_split,
    format_innings,
    format_pitching_split,
    format_pitching_game,
    format_player_heading,
    format_player_not_found,
    format_rate,
    format_stats_window,
)
from orioles_bot.mlb import (
    MlbApiError,
    parse_people,
    parse_pitching_game_logs,
    parse_player_stats,
    parse_roster,
)
from orioles_bot.models import HittingSplit, PitchingGame, PitchingSplit, PlayerRef
from orioles_bot.player_stats import PlayerStatsService


EASTERN = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 7, 18, 0, tzinfo=UTC)

ROSTER_PAYLOAD: dict[str, Any] = {
    "roster": [
        {
            "person": {"id": 668939, "fullName": "Adley Rutschman"},
            "position": {"abbreviation": "C"},
        },
        {
            "person": {"id": 683002, "fullName": "Gunnar Henderson"},
            "position": {"abbreviation": "SS"},
        },
        {"person": {"id": 668939, "fullName": "Adley Rutschman"}},
        {"person": {"fullName": "No Identifier"}},
        "not-a-dict",
    ]
}

STATS_PAYLOAD: dict[str, Any] = {
    "stats": [
        {
            "group": {"displayName": "hitting"},
            "splits": [
                {
                    "stat": {
                        "gamesPlayed": 6,
                        "plateAppearances": 27,
                        "atBats": 24,
                        "runs": 5,
                        "hits": 8,
                        "doubles": 2,
                        "triples": 0,
                        "homeRuns": 3,
                        "rbi": 7,
                        "baseOnBalls": 3,
                        "strikeOuts": 5,
                        "stolenBases": 1,
                        "avg": ".333",
                        "obp": ".407",
                        "slg": ".708",
                        "ops": "1.115",
                    }
                }
            ],
        },
        {
            "group": {"displayName": "pitching"},
            "splits": [
                {
                    "stat": {
                        "gamesPlayed": 2,
                        "gamesStarted": 2,
                        "wins": 1,
                        "losses": 0,
                        "saves": 0,
                        "inningsPitched": "12.1",
                        "hits": 9,
                        "runs": 4,
                        "earnedRuns": 3,
                        "homeRuns": 1,
                        "baseOnBalls": 2,
                        "strikeOuts": 15,
                        "era": "2.19",
                        "whip": "0.89",
                    }
                }
            ],
        },
    ]
}


class FakeClient:
    """Stands in for MlbClient, counting calls so caching can be asserted."""

    def __init__(
        self,
        roster: tuple[PlayerRef, ...] = (),
        search: tuple[PlayerRef, ...] = (),
        person: PlayerRef | None = None,
        roster_error: Exception | None = None,
    ) -> None:
        self._roster = roster
        self._search = search
        self._person = person
        self._roster_error = roster_error
        self.roster_calls = 0
        self.search_calls: list[str] = []
        self.person_calls: list[int] = []
        self.stats_calls: list[tuple[int, date, date]] = []

    async def fetch_roster(self) -> tuple[PlayerRef, ...]:
        self.roster_calls += 1
        if self._roster_error is not None:
            raise self._roster_error
        return self._roster

    async def search_players(self, query: str) -> tuple[PlayerRef, ...]:
        self.search_calls.append(query)
        return self._search

    async def fetch_player(self, player_id: int) -> PlayerRef | None:
        self.person_calls.append(player_id)
        return self._person

    async def fetch_player_stats(
        self, player_id: int, window: Any
    ) -> tuple[HittingSplit | None, PitchingSplit | None]:
        self.stats_calls.append((player_id, window.start, window.end))
        return HittingSplit(games=1), None


ADLEY = PlayerRef(668939, "Adley Rutschman", "C")
GUNNAR = PlayerRef(683002, "Gunnar Henderson", "SS")


def test_stats_window_covers_today_and_the_preceding_days() -> None:
    window = stats_window(7, EASTERN, NOW)

    assert window.days == 7
    assert window.end == date(2026, 8, 7)
    assert window.start == date(2026, 8, 1)


def test_stats_window_of_one_day_is_today_only() -> None:
    window = stats_window(1, EASTERN, NOW)

    assert window.start == window.end == date(2026, 8, 7)


@pytest.mark.parametrize("days", [0, -3, 163])
def test_stats_window_rejects_days_outside_the_supported_range(days: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 162"):
        stats_window(days, EASTERN, NOW)


def test_parse_roster_keeps_positions_and_drops_duplicates_and_junk() -> None:
    assert parse_roster(ROSTER_PAYLOAD) == (ADLEY, GUNNAR)


def test_parse_roster_tolerates_a_missing_roster_key() -> None:
    assert parse_roster({}) == ()


def test_parse_people_reads_the_primary_position() -> None:
    payload = {
        "people": [
            {
                "id": 12345,
                "fullName": "Aaron Judge",
                "primaryPosition": {"abbreviation": "RF"},
            }
        ]
    }

    assert parse_people(payload) == (PlayerRef(12345, "Aaron Judge", "RF"),)


def test_parse_player_stats_reads_both_groups() -> None:
    hitting, pitching = parse_player_stats(STATS_PAYLOAD)

    assert hitting is not None
    assert hitting.plate_appearances == 27
    assert hitting.home_runs == 3
    assert hitting.average == pytest.approx(0.333)
    assert hitting.ops == pytest.approx(1.115)
    assert pitching is not None
    assert pitching.innings_pitched == pytest.approx(12.1)
    assert pitching.era == pytest.approx(2.19)
    assert pitching.strikeouts == 15


def test_parse_player_stats_returns_nothing_when_the_window_is_empty() -> None:
    payload = {"stats": [{"group": {"displayName": "hitting"}, "splits": []}]}

    assert parse_player_stats(payload) == (None, None)


def test_parse_player_stats_treats_placeholder_rates_as_missing() -> None:
    payload = {
        "stats": [
            {
                "group": {"displayName": "hitting"},
                "splits": [{"stat": {"atBats": 0, "avg": ".---", "ops": "-.--"}}],
            }
        ]
    }

    hitting, _ = parse_player_stats(payload)

    assert hitting is not None
    assert hitting.average is None
    assert hitting.ops is None


def test_parse_player_stats_ignores_an_unexpected_payload() -> None:
    assert parse_player_stats({"stats": "nope"}) == (None, None)


def test_parse_pitching_game_logs_reads_team_result_and_game_line() -> None:
    games = parse_pitching_game_logs(
        {
            "stats": [
                {
                    "group": {"displayName": "pitching"},
                    "splits": [
                        {
                            "date": "2026-08-06",
                            "opponent": {"name": "New York Yankees"},
                            "isHome": True,
                            "isWin": True,
                            "stat": {
                                "gamesPlayed": 1,
                                "gamesStarted": 1,
                                "inningsPitched": "6.2",
                                "hits": 4,
                                "runs": 1,
                                "earnedRuns": 1,
                                "baseOnBalls": 2,
                                "strikeOuts": 8,
                            },
                        }
                    ],
                }
            ]
        }
    )

    assert len(games) == 1
    assert games[0].opponent == "New York Yankees"
    assert games[0].is_home is True
    assert games[0].result == "W"
    assert games[0].stat.innings_pitched == pytest.approx(6.2)
    assert games[0].stat.strikeouts == 8


def test_format_rate_drops_the_leading_zero() -> None:
    assert format_rate(0.2845) == ".284"
    assert format_rate(1.115) == "1.115"
    assert format_rate(None) == "—"


def test_format_era_uses_two_decimals_and_keeps_the_leading_zero() -> None:
    assert format_era(2.1) == "2.10"
    assert format_era(0.89) == "0.89"
    assert format_era(None) == "—"


def test_format_innings_uses_thirds_notation() -> None:
    assert format_innings(12.1) == "12.1"
    assert format_innings(7.0) == "7.0"
    assert format_innings(None) == "—"


def test_format_stats_window_reads_as_a_date_range() -> None:
    assert format_stats_window(stats_window(7, EASTERN, NOW)) == (
        "Last 7 days (Aug 1 – Aug 7, 2026)"
    )


def test_format_stats_window_uses_the_singular_for_one_day() -> None:
    assert format_stats_window(stats_window(1, EASTERN, NOW)).startswith("Last 1 day (")


def test_format_player_heading_links_to_baseball_savant() -> None:
    assert format_player_heading(ADLEY) == (
        "[Adley Rutschman](https://baseballsavant.mlb.com/savant-player/668939) (C)"
    )


def test_format_player_heading_omits_an_unknown_position() -> None:
    assert format_player_heading(PlayerRef(1, "Someone")) == (
        "[Someone](https://baseballsavant.mlb.com/savant-player/1)"
    )


def test_format_hitting_split_renders_the_slash_line() -> None:
    hitting, _ = parse_player_stats(STATS_PAYLOAD)
    assert hitting is not None

    text = format_hitting_split(hitting)

    assert "6 G, 27 PA" in text
    assert ".333/.407/.708 (OPS 1.115)" in text
    assert "3 HR, 7 RBI" in text


def test_format_pitching_split_renders_the_record_and_rates() -> None:
    _, pitching = parse_player_stats(STATS_PAYLOAD)
    assert pitching is not None

    text = format_pitching_split(pitching)

    assert "2 G (2 GS), 12.1 IP" in text
    assert "1-0, 2.19 ERA, 0.89 WHIP" in text
    assert "15 K" in text


def test_format_pitching_split_shows_saves_when_there_are_any() -> None:
    text = format_pitching_split(PitchingSplit(games=3, saves=2, era=0.0))

    assert "0-0, 2 SV, 0.00 ERA" in text


def test_format_pitching_game_shows_result_location_and_line() -> None:
    text = format_pitching_game(
        PitchingGame(
            game_date=date(2026, 8, 6),
            opponent="New York Yankees",
            is_home=True,
            result="W",
            stat=PitchingSplit(
                innings_pitched=6.2,
                hits=4,
                runs=1,
                earned_runs=1,
                walks=2,
                strikeouts=8,
            ),
        )
    )

    assert text == "6.2 IP, 4 H, 1 R, 1 ER, 2 BB, 8 K"


def test_format_player_not_found_quotes_the_query() -> None:
    assert "“Adly Rutchman”" in format_player_not_found("  Adly Rutchman ")


def test_service_caches_the_roster_until_the_ttl_expires() -> None:
    client = FakeClient(roster=(ADLEY, GUNNAR))
    clock = _FakeClock()
    service = PlayerStatsService(roster_ttl_seconds=900, clock=clock)

    async def scenario() -> None:
        assert await service.roster(client) == (ADLEY, GUNNAR)
        await service.roster(client)
        assert client.roster_calls == 1
        clock.advance(901)
        await service.roster(client)
        assert client.roster_calls == 2

    asyncio.run(scenario())


def test_service_caches_stats_per_player_and_window() -> None:
    client = FakeClient()
    service = PlayerStatsService()
    window = stats_window(7, EASTERN, NOW)
    other_window = stats_window(30, EASTERN, NOW)

    async def scenario() -> None:
        await service.stats(client, 668939, window)
        await service.stats(client, 668939, window)
        assert len(client.stats_calls) == 1
        await service.stats(client, 668939, other_window)
        await service.stats(client, 683002, window)
        assert len(client.stats_calls) == 3

    asyncio.run(scenario())


def test_autocomplete_prefers_names_starting_with_the_query() -> None:
    client = FakeClient(roster=(ADLEY, GUNNAR, PlayerRef(3, "Henderson Gun", "1B")))
    service = PlayerStatsService()

    suggestions = asyncio.run(service.autocomplete(client, "hend"))

    assert [player.name for player in suggestions] == [
        "Gunnar Henderson",
        "Henderson Gun",
    ]


def test_autocomplete_matches_a_last_name_typed_alone() -> None:
    client = FakeClient(roster=(ADLEY, GUNNAR))
    service = PlayerStatsService()

    suggestions = asyncio.run(service.autocomplete(client, "rutsch"))

    assert suggestions == (ADLEY,)


def test_autocomplete_returns_nothing_when_the_roster_is_unavailable() -> None:
    client = FakeClient(roster_error=MlbApiError("down"))
    service = PlayerStatsService()

    assert asyncio.run(service.autocomplete(client, "gunnar")) == ()


def test_resolve_accepts_an_autocomplete_player_id() -> None:
    client = FakeClient(roster=(ADLEY, GUNNAR))
    service = PlayerStatsService()

    assert asyncio.run(service.resolve(client, "683002")) == GUNNAR
    assert client.search_calls == []


def test_resolve_falls_back_to_the_people_endpoint_for_an_offroster_id() -> None:
    stranger = PlayerRef(99, "Someone Else", "1B")
    client = FakeClient(roster=(ADLEY,), person=stranger)
    service = PlayerStatsService()

    assert asyncio.run(service.resolve(client, "99")) == stranger
    assert client.person_calls == [99]


def test_resolve_searches_the_league_when_the_name_is_not_on_the_roster() -> None:
    judge = PlayerRef(592450, "Aaron Judge", "RF")
    client = FakeClient(roster=(ADLEY,), search=(judge,))
    service = PlayerStatsService()

    assert asyncio.run(service.resolve(client, "Aaron Judge")) == judge
    assert client.search_calls == ["Aaron Judge"]


def test_resolve_returns_nothing_for_an_unknown_name() -> None:
    client = FakeClient(roster=(ADLEY,))
    service = PlayerStatsService()

    assert asyncio.run(service.resolve(client, "Nobody At All")) is None


def test_resolve_ignores_a_blank_query() -> None:
    client = FakeClient(roster=(ADLEY,))
    service = PlayerStatsService()

    assert asyncio.run(service.resolve(client, "   ")) is None
    assert client.roster_calls == 0


class _FakeClock:
    def __init__(self) -> None:
        self._now = 0.0

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def __call__(self) -> float:
        return self._now


def test_service_fetches_the_roster_once_for_concurrent_callers() -> None:
    """Autocomplete keystrokes race, so a cold roster must be fetched once."""

    class SlowClient(FakeClient):
        async def fetch_roster(self) -> tuple[PlayerRef, ...]:
            self.roster_calls += 1
            await asyncio.sleep(0)
            return (ADLEY, GUNNAR)

    client = SlowClient()
    service = PlayerStatsService()

    async def scenario() -> None:
        results = await asyncio.gather(
            *(service.roster(client) for _ in range(5))
        )
        assert all(result == (ADLEY, GUNNAR) for result in results)
        assert client.roster_calls == 1

    asyncio.run(scenario())
