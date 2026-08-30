from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from orioles_bot.bot import pitch_mix_game
from orioles_bot.embeds import (
    no_pitch_mix_game_embed,
    no_pitches_embed,
    pitch_mix_embed,
)
from orioles_bot.formatting import format_pitch_mix, format_pitch_usage
from orioles_bot.mlb import (
    MlbApiError,
    parse_pitch_arsenal,
    parse_thrown_pitches,
)
from orioles_bot.models import (
    GameInfo,
    OutingPitchMix,
    PitchArsenalEntry,
    PitchTypeUsage,
    PlayerRef,
    ThrownPitch,
    UNKNOWN_PITCH_CODE,
    whole_percent_shares,
)
from orioles_bot.pitch_mix import (
    PitchMixService,
    build_pitch_mix,
    select_pitcher,
)


ORIOLE = PlayerRef(669203, "Grayson Rodriguez", "P")
OPPONENT = PlayerRef(608331, "Max Fried", "P")


def game(is_home: bool = False, status: str = "In Progress") -> GameInfo:
    return GameInfo(
        game_pk=778001,
        game_date=datetime(2026, 8, 12, 23, 5, tzinfo=UTC),
        status=status,
        venue="Yankee Stadium",
        home_team="Baltimore Orioles" if is_home else "New York Yankees",
        home_team_id=110 if is_home else 147,
        away_team="New York Yankees" if is_home else "Baltimore Orioles",
        opponent="New York Yankees",
        opponent_team_id=147,
        is_home=is_home,
        orioles_score=2,
        opponent_score=1,
        pitcher=None,
        opponent_pitcher=None,
        lineup=(),
        opponent_lineup=(),
        abstract_status="Final" if status == "Final" else "Live",
    )


def pitch(
    pitcher: PlayerRef = ORIOLE,
    code: str | None = "FF",
    name: str | None = "Four-Seam Fastball",
    speed: float | None = 96.0,
    is_top_inning: bool | None = True,
) -> ThrownPitch:
    return ThrownPitch(
        pitcher=pitcher,
        code=code,
        name=name,
        speed=speed,
        is_top_inning=is_top_inning,
    )


PLAY_BY_PLAY: dict[str, Any] = {
    "allPlays": [
        {
            "about": {"halfInning": "bottom"},
            "matchup": {"pitcher": {"id": 669203, "fullName": "Grayson Rodriguez"}},
            "playEvents": [
                {
                    "isPitch": True,
                    "details": {
                        "type": {"code": "FF", "description": "Four-Seam Fastball"}
                    },
                    "pitchData": {"startSpeed": 96.4},
                },
                {
                    "isPitch": True,
                    "details": {"type": {"code": "SL", "description": "Slider"}},
                    "pitchData": {"startSpeed": 84.6},
                },
                {"isPitch": False, "details": {"description": "Pickoff Attempt"}},
                {
                    "isPitch": True,
                    "details": {"description": "Automatic Ball"},
                    "pitchData": {},
                },
            ],
        },
        {
            "about": {"halfInning": "top"},
            "matchup": {"pitcher": {"id": 608331, "fullName": "Max Fried"}},
            "playEvents": [
                {
                    "isPitch": True,
                    "details": {"type": {"code": "CU", "description": "Curveball"}},
                    "pitchData": {"startSpeed": 74.1},
                }
            ],
        },
    ]
}

ARSENAL: dict[str, Any] = {
    "stats": [
        {
            "type": {"displayName": "pitchArsenal"},
            "group": {"displayName": "pitching"},
            "splits": [
                {
                    "stat": {
                        "type": {"code": "FF", "description": "Four-Seam Fastball"},
                        "count": 900,
                        "averageSpeed": 95.5,
                    }
                },
                {
                    "stat": {
                        "type": {"code": "SL", "description": "Slider"},
                        "count": 400,
                        "averageSpeed": 85.1,
                    }
                },
            ],
        }
    ]
}


def test_pitches_are_parsed_from_a_play_by_play_feed() -> None:
    pitches = parse_thrown_pitches(PLAY_BY_PLAY)

    assert len(pitches) == 4
    assert [item.code for item in pitches] == ["FF", "SL", None, "CU"]
    assert pitches[0].pitcher == PlayerRef(669203, "Grayson Rodriguez")
    assert pitches[0].speed == 96.4
    assert pitches[0].is_top_inning is False
    assert pitches[-1].is_top_inning is True


def test_a_feed_without_plays_yields_no_pitches() -> None:
    assert parse_thrown_pitches({}) == ()
    assert parse_thrown_pitches({"allPlays": []}) == ()


def test_a_season_arsenal_is_keyed_by_pitch_code() -> None:
    arsenal = parse_pitch_arsenal(ARSENAL)

    assert set(arsenal) == {"FF", "SL"}
    assert arsenal["FF"] == PitchArsenalEntry(
        code="FF", name="Four-Seam Fastball", count=900, average_speed=95.5
    )


def test_an_empty_arsenal_response_reads_as_no_baseline() -> None:
    assert parse_pitch_arsenal({}) == {}
    assert parse_pitch_arsenal({"stats": [{"splits": []}]}) == {}


def test_a_mix_groups_pitches_by_type_most_thrown_first() -> None:
    mix = build_pitch_mix(
        pitcher=ORIOLE,
        game_pk=778001,
        pitches=[
            pitch(code="SL", name="Slider", speed=85.0),
            pitch(speed=96.0),
            pitch(speed=97.0),
            pitch(code="SL", name="Slider", speed=84.0),
            pitch(speed=95.0),
        ],
        arsenal=parse_pitch_arsenal(ARSENAL),
        season=2026,
    )

    assert mix.total_pitches == 5
    assert [usage.code for usage in mix.pitches] == ["FF", "SL"]
    assert mix.pitches[0].count == 3
    assert mix.pitches[0].average_speed == 96.0
    assert mix.pitches[0].season_average_speed == 95.5
    assert round(mix.pitches[0].velocity_delta or 0, 1) == 0.5
    assert mix.pitches[1].velocity_delta == 84.5 - 85.1
    assert mix.baseline_season == 2026
    assert mix.has_baseline


def test_untyped_pitches_are_bucketed_rather_than_dropped() -> None:
    mix = build_pitch_mix(
        pitcher=ORIOLE,
        game_pk=778001,
        pitches=[pitch(), pitch(code=None, name=None, speed=None)],
    )

    assert mix.total_pitches == 2
    assert [usage.code for usage in mix.pitches] == ["FF", UNKNOWN_PITCH_CODE]
    unknown = mix.pitches[-1]
    assert unknown.name == "Unknown"
    assert unknown.average_speed is None
    assert unknown.season_average_speed is None


def test_the_untyped_bucket_sorts_last_however_many_there_are() -> None:
    mix = build_pitch_mix(
        pitcher=ORIOLE,
        game_pk=778001,
        pitches=[pitch(code=None, name=None), pitch(code=None, name=None), pitch()],
    )

    assert [usage.code for usage in mix.pitches] == ["FF", UNKNOWN_PITCH_CODE]
    assert mix.pitches[-1].count == 2


def test_a_mix_without_a_baseline_says_so_rather_than_inventing_one() -> None:
    mix = build_pitch_mix(pitcher=ORIOLE, game_pk=778001, pitches=[pitch()])

    assert mix.baseline_season is None
    assert not mix.has_baseline
    assert mix.pitches[0].season_average_speed is None


def test_a_pitcher_who_has_thrown_nothing_has_an_empty_mix() -> None:
    mix = build_pitch_mix(pitcher=ORIOLE, game_pk=778001, pitches=[])

    assert mix.is_empty
    assert mix.total_pitches == 0
    assert mix.shares() == ()


def test_shares_are_whole_percentages_that_total_one_hundred() -> None:
    # Three even thirds round to 33 apiece, which would otherwise read as 99%.
    assert sum(whole_percent_shares((1, 1, 1))) == 100
    assert whole_percent_shares((1, 1, 1)) == (34, 33, 33)
    assert whole_percent_shares((7, 2, 1)) == (70, 20, 10)
    assert whole_percent_shares(()) == ()
    assert whole_percent_shares((0, 0)) == (0, 0)


def test_shares_follow_the_pitch_order_of_the_mix() -> None:
    mix = build_pitch_mix(
        pitcher=ORIOLE,
        game_pk=778001,
        pitches=[pitch(), pitch(), pitch(code="SL", name="Slider")],
    )

    assert mix.shares() == (67, 33)
    assert sum(mix.shares()) == 100


def test_a_usage_line_reads_count_share_speed_and_the_gap_to_the_season() -> None:
    usage = PitchTypeUsage(
        code="SL",
        name="Slider",
        count=21,
        average_speed=84.6,
        season_average_speed=83.7,
    )

    assert (
        format_pitch_usage(usage, 32)
        == "**Slider** — 21 (32%) · 84.6 mph (+0.9 vs season 83.7 mph)"
    )


def test_a_usage_line_without_a_baseline_prints_no_delta() -> None:
    usage = PitchTypeUsage(code="SL", name="Slider", count=4, average_speed=84.6)

    assert format_pitch_usage(usage, 10) == "**Slider** — 4 (10%) · 84.6 mph (no season average)"


def test_a_usage_line_without_a_speed_says_so() -> None:
    usage = PitchTypeUsage(code=UNKNOWN_PITCH_CODE, name="Unknown", count=1)

    assert format_pitch_usage(usage, 2) == "**Unknown** — 1 (2%) · speed not tracked"


def test_a_formatted_mix_is_one_line_per_pitch_type() -> None:
    mix = build_pitch_mix(
        pitcher=ORIOLE,
        game_pk=778001,
        pitches=[pitch(), pitch(), pitch(code="SL", name="Slider", speed=85.0)],
        arsenal=parse_pitch_arsenal(ARSENAL),
        season=2026,
    )

    lines = format_pitch_mix(mix).split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("**Four-Seam Fastball** — 2 (67%)")
    assert "vs season 95.5 mph" in lines[0]


def test_the_card_shows_the_pitch_count_and_every_pitch_type() -> None:
    mix = build_pitch_mix(
        pitcher=ORIOLE,
        game_pk=778001,
        pitches=[pitch(), pitch(code="SL", name="Slider", speed=85.0)],
        arsenal=parse_pitch_arsenal(ARSENAL),
        season=2026,
    )

    embed = pitch_mix_embed(mix, game())
    body = embed.description or ""

    assert "Grayson Rodriguez" in body
    assert "2 pitches" in body
    assert "2026 season averages" in body
    assert embed.fields[0].name == "Pitch mix"
    assert "Four-Seam Fastball" in str(embed.fields[0].value)
    assert "Slider" in str(embed.fields[0].value)


def test_the_card_says_when_there_is_no_season_baseline() -> None:
    mix = build_pitch_mix(pitcher=ORIOLE, game_pk=778001, pitches=[pitch()])

    embed = pitch_mix_embed(mix, game())

    assert "No season averages available" in (embed.description or "")


def test_the_empty_states_name_the_game_and_the_pitcher() -> None:
    no_pitches = no_pitches_embed(game())
    assert "No pitches have been thrown yet" in (no_pitches.description or "")

    missing = no_pitches_embed(game(), ORIOLE)
    assert "Grayson Rodriguez has not pitched" in (missing.description or "")

    no_game = no_pitch_mix_game_embed(game().game_date.date())
    assert "No Orioles game has been played yet" in (no_game.description or "")


def test_the_default_pitcher_is_the_orioles_arm_most_recently_on_the_mound() -> None:
    away = game()
    pitches = [
        pitch(is_top_inning=False),
        pitch(pitcher=OPPONENT, is_top_inning=True),
    ]

    assert select_pitcher(pitches, away) == ORIOLE


def test_the_default_pitcher_falls_back_to_the_last_pitch_of_either_side() -> None:
    pitches = [pitch(pitcher=OPPONENT, is_top_inning=None)]

    assert select_pitcher(pitches, game()) == OPPONENT
    assert select_pitcher([], game()) is None


def test_a_named_pitcher_who_never_appeared_selects_nobody() -> None:
    pitches = [pitch(is_top_inning=False)]

    assert select_pitcher(pitches, game(), OPPONENT.player_id) is None
    assert select_pitcher(pitches, game(), ORIOLE.player_id) == ORIOLE


class FakeClient:
    def __init__(self, arsenal_error: bool = False) -> None:
        self.play_by_play_calls = 0
        self.arsenal_calls = 0
        self.arsenal_error = arsenal_error

    async def fetch_play_by_play(self, game_pk: int) -> tuple[ThrownPitch, ...]:
        self.play_by_play_calls += 1
        return parse_thrown_pitches(PLAY_BY_PLAY)

    async def fetch_pitch_arsenal(
        self, player_id: int, season: int
    ) -> dict[str, PitchArsenalEntry]:
        self.arsenal_calls += 1
        if self.arsenal_error:
            raise MlbApiError("boom")
        return parse_pitch_arsenal(ARSENAL)


def test_the_service_reads_one_pitchers_outing_and_caches_both_lookups() -> None:
    async def scenario() -> tuple[OutingPitchMix | None, FakeClient]:
        client = FakeClient()
        service = PitchMixService()
        first = await service.outing(client, game(), 2026)  # type: ignore[arg-type]
        await service.outing(client, game(), 2026)  # type: ignore[arg-type]
        return first, client

    mix, client = asyncio.run(scenario())

    assert mix is not None
    # The away Orioles pitch the bottom half, so Fried's curveball is excluded.
    assert mix.pitcher == PlayerRef(669203, "Grayson Rodriguez")
    assert mix.total_pitches == 3
    assert [usage.code for usage in mix.pitches] == ["FF", "SL", UNKNOWN_PITCH_CODE]
    assert client.play_by_play_calls == 1
    assert client.arsenal_calls == 1


def test_a_missing_arsenal_costs_the_comparison_not_the_card() -> None:
    async def scenario() -> OutingPitchMix | None:
        service = PitchMixService()
        return await service.outing(FakeClient(arsenal_error=True), game(), 2026)  # type: ignore[arg-type]

    mix = asyncio.run(scenario())

    assert mix is not None
    assert mix.total_pitches == 3
    assert not mix.has_baseline
    assert mix.baseline_season is None


def test_the_service_returns_nothing_for_a_pitcher_who_did_not_appear() -> None:
    async def scenario() -> OutingPitchMix | None:
        service = PitchMixService()
        return await service.outing(FakeClient(), game(), 2026, 111111)  # type: ignore[arg-type]

    assert asyncio.run(scenario()) is None


def test_a_live_game_is_preferred_over_a_finished_one() -> None:
    finished = game(status="Final")
    live = game()

    assert pitch_mix_game([finished, live]) is live
    assert pitch_mix_game([finished]) is finished
    assert pitch_mix_game([]) is None
