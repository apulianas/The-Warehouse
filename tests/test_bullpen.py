from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest

from orioles_bot.bullpen import (
    BullpenService,
    assess_reliever,
    is_pitcher,
    is_starter,
)
from orioles_bot.embeds import bullpen_embed
from orioles_bot.formatting import format_reliever, format_reliever_outing
from orioles_bot.mlb import MlbApiError, parse_pitching_game_logs
from orioles_bot.models import (
    PitchingGame,
    PitchingSplit,
    PlayerRef,
    RELIEVER_AVAILABLE,
    RELIEVER_CAUTION,
    RELIEVER_UNAVAILABLE,
    RELIEVER_UNKNOWN,
)


TODAY = date(2026, 8, 12)
RELIEVER = PlayerRef(621111, "Yennier Cano", "P")


def outing(
    day: date,
    innings: float | None = 1.0,
    pitches: int = 15,
    games_started: int = 0,
    batters_faced: int = 4,
) -> PitchingGame:
    return PitchingGame(
        game_date=day,
        opponent="New York Yankees",
        is_home=True,
        result="W",
        stat=PitchingSplit(
            games=1,
            games_started=games_started,
            innings_pitched=innings,
            pitches=pitches,
            batters_faced=batters_faced,
        ),
    )


def test_pitch_count_and_batters_faced_are_parsed_from_a_game_log() -> None:
    payload: dict[str, Any] = {
        "stats": [
            {
                "group": {"displayName": "pitching"},
                "splits": [
                    {
                        "date": "2026-08-11",
                        "opponent": {"name": "New York Yankees"},
                        "isHome": True,
                        "stat": {
                            "gamesPlayed": 1,
                            "inningsPitched": "1.0",
                            "numberOfPitches": 18,
                            "battersFaced": 4,
                        },
                    }
                ],
            }
        ]
    }

    games = parse_pitching_game_logs(payload)

    assert games[0].stat.pitches == 18
    assert games[0].stat.batters_faced == 4


def test_pitches_thrown_is_used_when_number_of_pitches_is_absent() -> None:
    payload: dict[str, Any] = {
        "stats": [
            {
                "group": {"displayName": "pitching"},
                "splits": [
                    {
                        "date": "2026-08-11",
                        "opponent": {"name": "New York Yankees"},
                        "stat": {"inningsPitched": "0.2", "pitchesThrown": 11},
                    }
                ],
            }
        ]
    }

    assert parse_pitching_game_logs(payload)[0].stat.pitches == 11


def test_a_reliever_with_no_recent_work_is_available() -> None:
    status = assess_reliever(RELIEVER, [outing(date(2026, 8, 5))], TODAY)

    assert status.availability == RELIEVER_AVAILABLE
    assert status.outings == ()
    assert status.days_rest is None


def test_pitching_today_makes_a_reliever_unavailable() -> None:
    status = assess_reliever(RELIEVER, [outing(TODAY, innings=1.0, pitches=12)], TODAY)

    assert status.availability == RELIEVER_UNAVAILABLE
    assert status.days_rest == 0
    assert "today" in status.reason


def test_back_to_back_outings_make_a_reliever_unavailable() -> None:
    status = assess_reliever(
        RELIEVER,
        [outing(date(2026, 8, 11), pitches=12), outing(date(2026, 8, 10), pitches=14)],
        TODAY,
    )

    assert status.availability == RELIEVER_UNAVAILABLE
    assert status.reason == "Pitched on back-to-back days"
    assert status.days_rest == 1


def test_heavy_work_yesterday_is_a_caution() -> None:
    status = assess_reliever(
        RELIEVER, [outing(date(2026, 8, 11), innings=2.0, pitches=34)], TODAY
    )

    assert status.availability == RELIEVER_CAUTION
    assert "34 P" in status.reason


def test_light_work_yesterday_still_reads_as_available() -> None:
    status = assess_reliever(
        RELIEVER, [outing(date(2026, 8, 11), innings=0.1, pitches=6)], TODAY
    )

    assert status.availability == RELIEVER_AVAILABLE
    assert status.days_rest == 1


def test_two_days_of_rest_reads_as_available() -> None:
    status = assess_reliever(RELIEVER, [outing(date(2026, 8, 10), pitches=25)], TODAY)

    assert status.availability == RELIEVER_AVAILABLE
    assert status.reason == "2 days of rest"


def test_a_missing_game_log_reads_as_unknown() -> None:
    status = assess_reliever(RELIEVER, None, TODAY)

    assert status.availability == RELIEVER_UNKNOWN
    assert status.outings == ()


def test_is_starter_only_flags_a_rotation_arm() -> None:
    starts = [outing(date(2026, 8, 6), games_started=1, innings=6.0)]
    relief = [outing(date(2026, 8, 6)), outing(date(2026, 8, 3))]
    opener = [outing(date(2026, 8, 6), games_started=1), *relief]

    assert is_starter(starts)
    assert not is_starter(relief)
    assert not is_starter(opener)
    assert not is_starter([])


def test_is_pitcher_accepts_the_position_codes_mlb_uses() -> None:
    assert is_pitcher(PlayerRef(1, "Arm", "P"))
    assert is_pitcher(PlayerRef(2, "Arm", "rhp"))
    assert not is_pitcher(PlayerRef(3, "Bat", "SS"))
    assert not is_pitcher(PlayerRef(4, "Bat", None))


class StubClient:
    def __init__(
        self,
        roster: tuple[PlayerRef, ...],
        logs: dict[int, Any],
    ) -> None:
        self.roster = roster
        self.logs = logs
        self.requested: list[int] = []

    async def fetch_roster(self) -> tuple[PlayerRef, ...]:
        return self.roster

    async def fetch_pitching_game_log(
        self, player_id: int, window: Any
    ) -> tuple[PitchingGame, ...]:
        self.requested.append(player_id)
        result = self.logs.get(player_id, ())
        if isinstance(result, Exception):
            raise result
        return tuple(result)


def test_the_service_grades_the_pen_and_leaves_out_starters() -> None:
    roster = (
        PlayerRef(1, "Fresh Arm", "P"),
        PlayerRef(2, "Tired Arm", "P"),
        PlayerRef(3, "Rotation Arm", "P"),
        PlayerRef(4, "Shortstop", "SS"),
        PlayerRef(5, "Silent Arm", "P"),
    )
    logs: dict[int, Any] = {
        1: [outing(date(2026, 8, 4))],
        2: [outing(TODAY)],
        3: [outing(date(2026, 8, 7), games_started=1, innings=6.0)],
        5: MlbApiError("boom"),
    }
    client = StubClient(roster, logs)

    relievers = asyncio.run(BullpenService().relievers(client, TODAY))

    by_name = {status.player.name: status for status in relievers}
    assert set(by_name) == {"Fresh Arm", "Tired Arm", "Silent Arm"}
    assert by_name["Fresh Arm"].availability == RELIEVER_AVAILABLE
    assert by_name["Tired Arm"].availability == RELIEVER_UNAVAILABLE
    assert by_name["Silent Arm"].availability == RELIEVER_UNKNOWN
    assert 4 not in client.requested
    # Available first, unknown last, so the usable arms read off the top.
    assert [status.player.name for status in relievers] == [
        "Fresh Arm",
        "Tired Arm",
        "Silent Arm",
    ]


def test_the_service_returns_nothing_when_the_roster_has_no_pitchers() -> None:
    client = StubClient((PlayerRef(4, "Shortstop", "SS"),), {})

    assert asyncio.run(BullpenService().relievers(client, TODAY)) == ()


def test_a_roster_failure_surfaces_to_the_caller() -> None:
    class FailingClient(StubClient):
        async def fetch_roster(self) -> tuple[PlayerRef, ...]:
            raise MlbApiError("roster down")

    with pytest.raises(MlbApiError):
        asyncio.run(BullpenService().relievers(FailingClient((), {}), TODAY))


def test_a_reliever_line_shows_the_usage_behind_the_read() -> None:
    status = assess_reliever(
        RELIEVER, [outing(date(2026, 8, 11), innings=1.0, pitches=18)], TODAY
    )

    line = format_reliever(status)

    assert "Yennier Cano" in line
    assert "Recent: Aug 11 vs New York Yankees (1.0 IP, 18 P, 4 BF)" in line


def test_an_away_outing_reads_as_at_the_opponent() -> None:
    away = PitchingGame(
        game_date=date(2026, 8, 11),
        opponent="Boston Red Sox",
        is_home=False,
        result=None,
        stat=PitchingSplit(games=1, innings_pitched=0.2, pitches=9),
    )

    assert format_reliever_outing(away) == "Aug 11 at Boston Red Sox (0.2 IP, 9 P)"


def test_the_bullpen_embed_groups_relievers_by_availability() -> None:
    relievers = (
        assess_reliever(PlayerRef(1, "Fresh Arm", "P"), [], TODAY),
        assess_reliever(PlayerRef(2, "Tired Arm", "P"), [outing(TODAY)], TODAY),
    )

    embed = bullpen_embed(relievers)

    names = [field.name for field in embed.fields]
    assert names == ["Available (1)", "Unavailable (1)"]
    assert "Fresh Arm" in embed.fields[0].value
    assert "Tired Arm" in embed.fields[1].value


def test_the_bullpen_embed_says_so_when_no_relievers_are_found() -> None:
    embed = bullpen_embed(())

    assert embed.fields == []
    assert "No relievers" in (embed.description or "")
