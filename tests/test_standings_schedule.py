from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from orioles_bot.bot import (
    STANDINGS_VIEW_BOTH,
    STANDINGS_VIEW_DIVISION,
    STANDINGS_VIEW_WILD_CARD,
    _standings_embeds,
)
from orioles_bot.cache import AsyncTtlCache
from orioles_bot.dates import (
    MAX_SCHEDULE_WINDOW_DAYS,
    MIN_SCHEDULE_WINDOW_DAYS,
    schedule_window,
)
from orioles_bot.embeds import schedule_embeds, standings_embed, wild_card_embed
from orioles_bot.formatting import (
    PLAYOFF_LINE,
    format_games_back,
    format_next_game,
    format_orioles_standing,
    format_orioles_wild_card,
    format_run_differential,
    format_schedule_day,
    format_schedule_entry,
    format_schedule_window,
    format_standings,
    format_standings_row,
    format_streak,
    format_wild_card,
    format_wild_card_gap,
    format_wild_card_row,
)
from orioles_bot.mlb import (
    parse_next_games,
    parse_schedule,
    parse_standings,
    parse_wild_card_standings,
)
from orioles_bot.models import (
    AL_EAST_DIVISION_ID,
    DivisionStandings,
    GameInfo,
    NextGame,
    PitcherInfo,
    ScheduleWindow,
    TeamRecord,
    WildCardStandings,
)


EASTERN = ZoneInfo("America/New_York")


def _standings_payload() -> dict[str, Any]:
    return {
        "records": [
            {
                "division": {"id": 200, "nameShort": "AL Central"},
                "teamRecords": [
                    {"team": {"id": 116, "name": "Detroit Tigers"}, "wins": 1, "losses": 0}
                ],
            },
            {
                "division": {"id": AL_EAST_DIVISION_ID, "nameShort": "AL East"},
                "teamRecords": [
                    {
                        "team": {"id": 147, "name": "New York Yankees"},
                        "season": "2026",
                        "wins": 94,
                        "losses": 68,
                        "winningPercentage": ".580",
                        "divisionRank": "1",
                        "gamesBack": "-",
                        "wildCardGamesBack": "-",
                        "streak": {"streakCode": "W8"},
                        "runDifferential": 164,
                        "divisionLeader": True,
                        "clinchIndicator": "z",
                    },
                    {
                        "team": {"id": 110, "name": "Baltimore Orioles"},
                        "season": "2026",
                        "wins": 75,
                        "losses": 87,
                        "winningPercentage": ".463",
                        "divisionRank": "2",
                        "gamesBack": "19.0",
                        "wildCardGamesBack": "12.0",
                        "streak": {"streakCode": "L3"},
                        "runDifferential": -111,
                    },
                ],
            },
        ]
    }


def test_parse_standings_selects_the_requested_division() -> None:
    standings = parse_standings(_standings_payload())

    assert standings is not None
    assert standings.division_name == "AL East"
    assert standings.season == "2026"
    assert [record.team_name for record in standings.teams] == [
        "New York Yankees",
        "Baltimore Orioles",
    ]


def test_parse_standings_reads_record_details() -> None:
    standings = parse_standings(_standings_payload())
    assert standings is not None
    orioles = standings.teams[1]

    assert orioles.is_orioles
    assert (orioles.wins, orioles.losses) == (75, 87)
    assert orioles.games_back == "19.0"
    assert orioles.wild_card_games_back == "12.0"
    assert orioles.streak == "L3"
    assert orioles.run_differential == -111
    assert orioles.clinch_indicator is None


def test_parse_standings_returns_none_for_a_missing_division() -> None:
    assert parse_standings({"records": []}) is None
    assert parse_standings({}) is None
    assert parse_standings(_standings_payload(), division_id=999) is None


def test_parse_standings_ignores_malformed_entries() -> None:
    payload = {
        "records": [
            "nonsense",
            {
                "division": {"id": AL_EAST_DIVISION_ID, "name": "American League East"},
                "teamRecords": [
                    "nonsense",
                    {"team": {}},
                    {"team": {"id": 110, "name": "Baltimore Orioles"}, "wins": 3},
                ],
            },
        ]
    }

    standings = parse_standings(payload)

    assert standings is not None
    assert standings.division_name == "American League East"
    assert len(standings.teams) == 1
    assert standings.teams[0].losses == 0


def test_parse_standings_falls_back_to_the_league_record() -> None:
    payload = {
        "records": [
            {
                "division": {"id": AL_EAST_DIVISION_ID, "nameShort": "AL East"},
                "teamRecords": [
                    {
                        "team": {"id": 110, "name": "Baltimore Orioles"},
                        "leagueRecord": {"wins": 12, "losses": 9, "pct": ".571"},
                    }
                ],
            }
        ]
    }

    standings = parse_standings(payload)

    assert standings is not None
    assert (standings.teams[0].wins, standings.teams[0].losses) == (12, 9)
    assert standings.teams[0].winning_percentage == ".571"


def test_parse_standings_orders_unranked_teams_by_wins() -> None:
    payload = {
        "records": [
            {
                "division": {"id": AL_EAST_DIVISION_ID, "nameShort": "AL East"},
                "teamRecords": [
                    {"team": {"id": 110, "name": "Baltimore Orioles"}, "wins": 2},
                    {"team": {"id": 147, "name": "New York Yankees"}, "wins": 5},
                ],
            }
        ]
    }

    standings = parse_standings(payload)

    assert standings is not None
    assert [record.team_id for record in standings.teams] == [147, 110]


def _schedule_game(
    game_pk: int,
    game_date: str | None,
    home_id: int,
    away_id: int,
    state: str = "Scheduled",
) -> dict[str, Any]:
    return {
        "gamePk": game_pk,
        "gameDate": game_date,
        "status": {
            "detailedState": state,
            "abstractGameState": "Final" if state == "Final" else "Preview",
        },
        "venue": {"name": "Oriole Park"},
        "teams": {
            "home": {"team": {"id": home_id, "name": f"Team {home_id}", "abbreviation": f"H{home_id}"}},
            "away": {"team": {"id": away_id, "name": f"Team {away_id}", "abbreviation": f"A{away_id}"}},
        },
    }


def test_parse_next_games_picks_the_earliest_unplayed_game() -> None:
    schedule = {
        "dates": [
            {
                "games": [
                    _schedule_game(1, "2026-04-02T23:05:00Z", 110, 147, state="Final"),
                    _schedule_game(2, "2026-04-03T23:05:00Z", 110, 147),
                ]
            },
            {"games": [_schedule_game(3, "2026-04-04T23:05:00Z", 110, 147)]},
        ]
    }

    result = parse_next_games(schedule, [110, 147])

    assert set(result) == {110, 147}
    assert result[110].opponent_abbreviation == "A147"
    assert result[110].is_home is True
    assert result[147].is_home is False
    assert result[110].game_date == datetime(2026, 4, 3, 23, 5, tzinfo=UTC)


def test_parse_next_games_skips_postponed_games() -> None:
    schedule = {
        "dates": [
            {
                "games": [
                    _schedule_game(1, "2026-04-02T23:05:00Z", 110, 147, state="Postponed"),
                    _schedule_game(2, "2026-04-05T23:05:00Z", 110, 141),
                ]
            }
        ]
    }

    result = parse_next_games(schedule, [110])

    assert result[110].opponent_abbreviation == "A141"


def test_parse_next_games_keeps_in_progress_games() -> None:
    schedule = {
        "dates": [
            {"games": [_schedule_game(1, "2026-04-02T23:05:00Z", 110, 147, state="In Progress")]}
        ]
    }

    result = parse_next_games(schedule, [110])

    assert result[110].status == "In Progress"


def test_parse_next_games_ignores_teams_that_were_not_requested() -> None:
    schedule = {"dates": [{"games": [_schedule_game(1, "2026-04-02T23:05:00Z", 110, 147)]}]}

    assert set(parse_next_games(schedule, [110])) == {110}
    assert parse_next_games(schedule, []) == {}
    assert parse_next_games({}, [110]) == {}


def test_parse_next_games_tolerates_an_unparsable_start_time() -> None:
    schedule = {
        "dates": [
            {
                "games": [
                    _schedule_game(1, "not-a-date", 110, 147),
                    _schedule_game(2, "2026-04-06T23:05:00Z", 110, 141),
                ]
            }
        ]
    }

    result = parse_next_games(schedule, [110])

    assert result[110].game_date == datetime(2026, 4, 6, 23, 5, tzinfo=UTC)


def test_parse_schedule_sorts_games_and_tolerates_bad_dates() -> None:
    schedule = {
        "dates": [
            {
                "games": [
                    _schedule_game(3, None, 110, 147),
                    _schedule_game(2, "2026-04-03T23:05:00Z", 110, 147),
                ]
            },
            {"games": [_schedule_game(1, "2026-04-01T23:05:00Z", 110, 147)]},
        ]
    }

    games = parse_schedule(schedule)

    assert [game.game_pk for game in games] == [1, 2, 3]


def test_parse_schedule_handles_an_empty_payload() -> None:
    assert parse_schedule({}) == []
    assert parse_schedule({"dates": "nonsense"}) == []


def test_schedule_window_looks_forward() -> None:
    now = datetime(2026, 4, 1, 15, 0, tzinfo=UTC)

    window = schedule_window(7, EASTERN, now)

    assert window.days == 7
    assert window.start == date(2026, 4, 1)
    assert window.end == date(2026, 4, 7)


def test_schedule_window_of_one_day_covers_today_only() -> None:
    now = datetime(2026, 4, 1, 15, 0, tzinfo=UTC)

    window = schedule_window(1, EASTERN, now)

    assert window.start == window.end == date(2026, 4, 1)


@pytest.mark.parametrize(
    "days", [MIN_SCHEDULE_WINDOW_DAYS - 1, MAX_SCHEDULE_WINDOW_DAYS + 1, 0, -3]
)
def test_schedule_window_rejects_out_of_range_days(days: int) -> None:
    with pytest.raises(ValueError, match="Days must be between"):
        schedule_window(days, EASTERN)


def test_format_games_back_renders_a_leader_as_even() -> None:
    assert format_games_back("-") == "—"
    assert format_games_back("+0.0") == "—"
    assert format_games_back("5.0") == "5.0"
    assert format_games_back(None) == "—"


def test_format_streak_and_run_differential() -> None:
    assert format_streak("W4") == "W4"
    assert format_streak(None) == "—"
    assert format_run_differential(77) == "+77"
    assert format_run_differential(-111) == "-111"
    assert format_run_differential(0) == "0"
    assert format_run_differential(None) == "—"


def _record(**overrides: Any) -> TeamRecord:
    base: dict[str, Any] = {
        "team_id": 110,
        "team_name": "Baltimore Orioles",
        "wins": 75,
        "losses": 87,
        "winning_percentage": ".463",
        "division_rank": "5",
        "games_back": "19.0",
        "wild_card_games_back": "12.0",
        "streak": "L3",
        "run_differential": -111,
    }
    base.update(overrides)
    return TeamRecord(**base)


def test_format_standings_row_highlights_the_orioles() -> None:
    row = format_standings_row(_record())

    assert row == "5. **Baltimore Orioles** — 75-87 (.463), GB 19.0, L3"


def test_format_standings_row_shows_a_clinch_indicator() -> None:
    row = format_standings_row(
        _record(team_id=147, team_name="New York Yankees", clinch_indicator="z")
    )

    assert "New York Yankees (z)" in row
    assert "**" not in row


def test_format_standings_row_appends_the_next_opponent() -> None:
    next_games = {
        110: NextGame(
            team_id=110,
            opponent="Tampa Bay Rays",
            opponent_abbreviation="TB",
            opponent_team_id=139,
            is_home=True,
            game_date=datetime(2026, 4, 3, 23, 5, tzinfo=UTC),
            status="Scheduled",
        )
    }

    row = format_standings_row(_record(), next_games, EASTERN)

    assert row.endswith("↳ Next: vs TB — Fri Apr 3, 7:05 PM EDT")


def test_format_standings_row_omits_the_next_game_when_unknown() -> None:
    assert "Next:" not in format_standings_row(_record(), {}, EASTERN)


def test_format_next_game_marks_an_away_game_and_a_live_status() -> None:
    next_game = NextGame(
        team_id=110,
        opponent="Boston Red Sox",
        opponent_abbreviation="BOS",
        opponent_team_id=111,
        is_home=False,
        game_date=datetime(2026, 4, 3, 23, 5, tzinfo=UTC),
        status="In Progress",
    )

    assert format_next_game(next_game, EASTERN) == (
        "↳ Next: @ BOS — Fri Apr 3, 7:05 PM EDT (In Progress)"
    )


def test_format_next_game_handles_a_missing_start_time() -> None:
    next_game = NextGame(
        team_id=110,
        opponent="Boston Red Sox",
        opponent_abbreviation=None,
        opponent_team_id=111,
        is_home=True,
        game_date=None,
        status="Scheduled",
    )

    assert format_next_game(next_game, EASTERN) == (
        "↳ Next: vs Boston Red Sox — time TBD"
    )


def _standings(*records: TeamRecord) -> DivisionStandings:
    return DivisionStandings(
        division_id=AL_EAST_DIVISION_ID,
        division_name="AL East",
        teams=records,
        season="2026",
    )


def test_format_standings_renders_one_row_per_team() -> None:
    text = format_standings(
        _standings(_record(team_id=147, team_name="New York Yankees", division_rank="1"), _record())
    )

    assert text.count("\n") == 1


def test_format_standings_handles_an_empty_division() -> None:
    assert format_standings(_standings()) == "Standings are not available yet."


def test_format_orioles_standing_summarizes_the_orioles_row() -> None:
    summary = format_orioles_standing(_standings(_record()))

    assert summary == (
        "Baltimore Orioles: 75-87 • 5th in AL East • wild card 12.0 • run diff -111"
    )


def test_format_orioles_standing_returns_none_without_the_orioles() -> None:
    other = _record(team_id=147, team_name="New York Yankees")

    assert format_orioles_standing(_standings(other)) is None


def _wild_card_payload() -> dict[str, Any]:
    def entry(
        rank: str, team_id: int, name: str, wins: int, losses: int, gap: str, leader: bool
    ) -> dict[str, Any]:
        return {
            "team": {"id": team_id, "name": name},
            "season": "2026",
            "wins": wins,
            "losses": losses,
            "winningPercentage": ".500",
            "wildCardRank": rank,
            "wildCardGamesBack": gap,
            "wildCardLeader": leader,
            "streak": {"streakCode": "W1"},
        }

    return {
        "records": [
            {
                "standingsType": "wildCard",
                "teamRecords": [
                    entry("3", 140, "Texas Rangers", 57, 58, "-", True),
                    entry("1", 147, "New York Yankees", 64, 51, "+7.0", True),
                    entry("2", 111, "Boston Red Sox", 63, 51, "+6.5", True),
                    entry("6", 110, "Baltimore Orioles", 56, 59, "1.0", False),
                    entry("4", 142, "Minnesota Twins", 57, 59, "0.5", False),
                ],
            }
        ]
    }


def test_parse_wild_card_standings_orders_by_wild_card_rank() -> None:
    standings = parse_wild_card_standings(_wild_card_payload())

    assert standings is not None
    assert standings.league_name == "American League"
    assert standings.season == "2026"
    assert [record.wild_card_rank for record in standings.teams] == [
        "1",
        "2",
        "3",
        "4",
        "6",
    ]


def test_parse_wild_card_standings_reads_leader_flags() -> None:
    standings = parse_wild_card_standings(_wild_card_payload())
    assert standings is not None

    assert [record.wild_card_leader for record in standings.teams] == [
        True,
        True,
        True,
        False,
        False,
    ]


def test_parse_wild_card_standings_handles_empty_payloads() -> None:
    assert parse_wild_card_standings({}) is None
    assert parse_wild_card_standings({"records": []}) is None
    assert parse_wild_card_standings({"records": [{"teamRecords": []}]}) is None


def test_parse_wild_card_standings_merges_multiple_record_groups() -> None:
    payload = {
        "records": [
            {
                "teamRecords": [
                    {
                        "team": {"id": 110, "name": "Baltimore Orioles"},
                        "wildCardRank": "2",
                        "wins": 56,
                    }
                ]
            },
            {
                "teamRecords": [
                    {
                        "team": {"id": 147, "name": "New York Yankees"},
                        "wildCardRank": "1",
                        "wins": 64,
                    }
                ]
            },
        ]
    }

    standings = parse_wild_card_standings(payload)

    assert standings is not None
    assert [record.team_id for record in standings.teams] == [147, 110]


def test_parse_wild_card_standings_sorts_unranked_teams_by_wins() -> None:
    payload = {
        "records": [
            {
                "teamRecords": [
                    {"team": {"id": 110, "name": "Baltimore Orioles"}, "wins": 40},
                    {"team": {"id": 147, "name": "New York Yankees"}, "wins": 60},
                ]
            }
        ]
    }

    standings = parse_wild_card_standings(payload)

    assert standings is not None
    assert [record.team_id for record in standings.teams] == [147, 110]


def _wc_record(**overrides: Any) -> TeamRecord:
    base: dict[str, Any] = {
        "team_id": 110,
        "team_name": "Baltimore Orioles",
        "wins": 56,
        "losses": 59,
        "winning_percentage": ".487",
        "wild_card_rank": "6",
        "wild_card_games_back": "1.0",
        "streak": "L1",
    }
    base.update(overrides)
    return TeamRecord(**base)


def test_format_wild_card_gap_distinguishes_leaders_from_chasers() -> None:
    assert format_wild_card_gap(_wc_record(wild_card_games_back="+7.0")) == "+7.0 up"
    assert format_wild_card_gap(_wc_record(wild_card_games_back="1.0")) == "1.0 GB"


def test_format_wild_card_gap_marks_the_team_holding_the_last_berth() -> None:
    holder = _wc_record(wild_card_games_back="-", wild_card_leader=True)

    assert format_wild_card_gap(holder) == "even with the line"


def test_format_wild_card_row_bolds_the_orioles() -> None:
    row = format_wild_card_row(_wc_record())

    assert row == "6. **Baltimore Orioles** — 56-59 (.487), 1.0 GB, L1"


def _wild_card(*records: TeamRecord) -> WildCardStandings:
    return WildCardStandings(
        league_id=103,
        league_name="American League",
        teams=records,
        season="2026",
    )


def test_format_wild_card_draws_the_playoff_line_after_the_third_spot() -> None:
    standings = _wild_card(
        _wc_record(team_id=147, team_name="Yankees", wild_card_rank="1", wild_card_leader=True),
        _wc_record(team_id=111, team_name="Red Sox", wild_card_rank="2", wild_card_leader=True),
        _wc_record(team_id=140, team_name="Rangers", wild_card_rank="3", wild_card_leader=True),
        _wc_record(team_id=142, team_name="Twins", wild_card_rank="4"),
        _wc_record(wild_card_rank="5"),
    )

    lines = format_wild_card(standings).split("\n")

    assert lines[3] == PLAYOFF_LINE
    assert lines.count(PLAYOFF_LINE) == 1
    assert "Rangers" in lines[2]
    assert "Twins" in lines[4]


def test_format_wild_card_draws_no_line_when_every_team_is_in() -> None:
    standings = _wild_card(
        _wc_record(team_id=147, wild_card_rank="1", wild_card_leader=True),
        _wc_record(team_id=111, wild_card_rank="2", wild_card_leader=True),
    )

    assert PLAYOFF_LINE not in format_wild_card(standings)


def test_format_wild_card_falls_back_to_position_without_ranks() -> None:
    standings = _wild_card(
        *[_wc_record(team_id=index, wild_card_rank=None) for index in range(5)]
    )

    lines = format_wild_card(standings).split("\n")

    assert lines[3] == PLAYOFF_LINE


def test_format_wild_card_handles_an_empty_race() -> None:
    assert format_wild_card(_wild_card()) == (
        "Wild card standings are not available yet."
    )


def test_format_orioles_wild_card_summarizes_the_race_position() -> None:
    summary = format_orioles_wild_card(_wild_card(_wc_record()))

    assert summary == "Baltimore Orioles: 56-59 • 6th in the AL wild card • 1.0 GB"


def test_format_orioles_wild_card_returns_none_when_absent() -> None:
    other = _wc_record(team_id=147, team_name="New York Yankees")

    assert format_orioles_wild_card(_wild_card(other)) is None


def test_wild_card_embed_reports_missing_standings() -> None:
    embed = wild_card_embed(None)

    assert embed.title == "AL wild card"
    assert "unavailable" in (embed.description or "")


def test_wild_card_embed_titles_and_footers_the_race() -> None:
    embed = wild_card_embed(_wild_card(_wc_record()), {}, EASTERN)

    assert embed.title == "American League wild card — 2026"
    assert "Top 3 make the playoffs" in (embed.footer.text or "")
    assert "6th in the AL wild card" in (embed.footer.text or "")


def _payload() -> tuple[Any, Any, dict[int, NextGame]]:
    division = _standings(_record())
    wild_card = _wild_card(_wc_record())
    return division, wild_card, {}


def test_standings_embeds_show_the_wild_card_first_for_both() -> None:
    embeds = _standings_embeds(_payload(), STANDINGS_VIEW_BOTH, EASTERN)

    assert len(embeds) == 2
    assert "wild card" in (embeds[0].title or "")
    assert "AL East" in (embeds[1].title or "")


def test_standings_embeds_honour_a_single_view() -> None:
    wild_card_only = _standings_embeds(_payload(), STANDINGS_VIEW_WILD_CARD, EASTERN)
    division_only = _standings_embeds(_payload(), STANDINGS_VIEW_DIVISION, EASTERN)

    assert len(wild_card_only) == 1
    assert "wild card" in (wild_card_only[0].title or "")
    assert len(division_only) == 1
    assert "AL East" in (division_only[0].title or "")


def _game(**overrides: Any) -> GameInfo:
    base: dict[str, Any] = {
        "game_pk": 777001,
        "game_date": datetime(2026, 4, 3, 23, 5, tzinfo=UTC),
        "status": "Scheduled",
        "venue": "Oriole Park at Camden Yards",
        "home_team": "Baltimore Orioles",
        "home_team_id": 110,
        "away_team": "Tampa Bay Rays",
        "opponent": "Tampa Bay Rays",
        "opponent_team_id": 139,
        "is_home": True,
        "orioles_score": None,
        "opponent_score": None,
        "pitcher": PitcherInfo(player_id=1, name="Grayson Rodriguez"),
        "opponent_pitcher": PitcherInfo(player_id=2, name="Shane McClanahan"),
        "lineup": (),
        "opponent_lineup": (),
    }
    base.update(overrides)
    return GameInfo(**base)


def test_format_schedule_entry_lists_probable_starters() -> None:
    entry = format_schedule_entry(_game(), EASTERN)

    assert entry.startswith("**vs Tampa Bay Rays** — Fri, Apr 3 at 7:05 PM EDT")
    assert "Grayson Rodriguez" in entry
    assert "Shane McClanahan" in entry


def test_format_schedule_entry_marks_an_away_game() -> None:
    entry = format_schedule_entry(_game(is_home=False), EASTERN)

    assert entry.startswith("**@ Tampa Bay Rays**")


def test_format_schedule_entry_shows_a_final_score_without_probables() -> None:
    entry = format_schedule_entry(
        _game(status="Final", orioles_score=5, opponent_score=2), EASTERN
    )

    assert "Final: Orioles 5, Tampa Bay Rays 2" in entry
    assert "Probable pitcher" not in entry


def test_format_schedule_entry_surfaces_a_non_default_status() -> None:
    entry = format_schedule_entry(_game(status="Postponed"), EASTERN)

    assert "Status: Postponed" in entry
    assert "Grayson Rodriguez" in entry


def test_format_schedule_day_and_window() -> None:
    window = ScheduleWindow(days=3, start=date(2026, 4, 1), end=date(2026, 4, 3))

    assert format_schedule_window(window) == "Next 3 days (Apr 1 – Apr 3, 2026)"
    assert format_schedule_day(_game(), EASTERN) == "Fri, Apr 3"
    assert format_schedule_day(_game(game_date=None), EASTERN) == "Date TBD"


def test_format_schedule_window_uses_the_singular_for_one_day() -> None:
    window = ScheduleWindow(days=1, start=date(2026, 4, 1), end=date(2026, 4, 1))

    assert format_schedule_window(window).startswith("Next 1 day (")


def test_standings_embed_reports_when_standings_are_missing() -> None:
    embed = standings_embed(None)

    assert embed.title == "AL East standings"
    assert "unavailable" in (embed.description or "")


def test_standings_embed_titles_with_the_division_and_season() -> None:
    embed = standings_embed(_standings(_record()), {}, EASTERN)

    assert embed.title == "AL East standings — 2026"
    assert "Baltimore Orioles" in (embed.description or "")
    assert "run diff -111" in (embed.footer.text or "")


def test_schedule_embeds_render_one_field_per_game() -> None:
    window = ScheduleWindow(days=3, start=date(2026, 4, 1), end=date(2026, 4, 3))

    embeds = schedule_embeds([_game(), _game(game_pk=777002)], window, EASTERN)

    assert len(embeds) == 1
    assert len(embeds[0].fields) == 2
    assert embeds[0].description == format_schedule_window(window)


def test_schedule_embeds_report_an_empty_window() -> None:
    window = ScheduleWindow(days=3, start=date(2026, 4, 1), end=date(2026, 4, 3))

    embeds = schedule_embeds([], window, EASTERN)

    assert "No Baltimore Orioles games are scheduled" in (embeds[0].description or "")


def test_schedule_embeds_cap_fields_at_the_discord_limit() -> None:
    window = ScheduleWindow(days=30, start=date(2026, 4, 1), end=date(2026, 4, 30))
    games = [_game(game_pk=index) for index in range(30)]

    embeds = schedule_embeds(games, window, EASTERN)

    assert len(embeds[0].fields) == 25
    assert "Showing 25 of 30 games." == embeds[0].footer.text


def test_ttl_cache_serves_a_second_call_from_memory() -> None:
    async def scenario() -> tuple[str, str, int]:
        calls = 0

        async def factory() -> str:
            nonlocal calls
            calls += 1
            return f"value-{calls}"

        cache: AsyncTtlCache[str, str] = AsyncTtlCache(60, clock=lambda: 0.0)
        first = await cache.get_or_fetch("key", factory)
        second = await cache.get_or_fetch("key", factory)
        return first, second, calls

    first, second, calls = asyncio.run(scenario())

    assert (first, second, calls) == ("value-1", "value-1", 1)


def test_ttl_cache_refetches_after_the_ttl_expires() -> None:
    async def scenario() -> tuple[str, int]:
        calls = 0
        now = 0.0

        async def factory() -> str:
            nonlocal calls
            calls += 1
            return f"value-{calls}"

        cache: AsyncTtlCache[str, str] = AsyncTtlCache(60, clock=lambda: now)
        await cache.get_or_fetch("key", factory)
        now = 61.0
        latest = await cache.get_or_fetch("key", factory)
        return latest, calls

    latest, calls = asyncio.run(scenario())

    assert (latest, calls) == ("value-2", 2)


def test_ttl_cache_collapses_concurrent_misses_into_one_fetch() -> None:
    async def scenario() -> tuple[list[str], int]:
        calls = 0
        released = asyncio.Event()

        async def factory() -> str:
            nonlocal calls
            calls += 1
            await released.wait()
            return "value"

        cache: AsyncTtlCache[str, str] = AsyncTtlCache(60, clock=lambda: 0.0)
        waiters = [
            asyncio.create_task(cache.get_or_fetch("key", factory)) for _ in range(5)
        ]
        await asyncio.sleep(0)
        released.set()
        return await asyncio.gather(*waiters), calls

    values, calls = asyncio.run(scenario())

    assert values == ["value"] * 5
    assert calls == 1


def test_ttl_cache_keeps_distinct_keys_apart() -> None:
    async def scenario() -> tuple[str, str]:
        async def factory(value: str) -> str:
            return value

        cache: AsyncTtlCache[str, str] = AsyncTtlCache(60, clock=lambda: 0.0)
        first = await cache.get_or_fetch("a", lambda: factory("a"))
        second = await cache.get_or_fetch("b", lambda: factory("b"))
        return first, second

    assert asyncio.run(scenario()) == ("a", "b")


def test_ttl_cache_invalidate_forces_a_refetch() -> None:
    async def scenario() -> int:
        calls = 0

        async def factory() -> str:
            nonlocal calls
            calls += 1
            return "value"

        cache: AsyncTtlCache[str, str] = AsyncTtlCache(60, clock=lambda: 0.0)
        await cache.get_or_fetch("key", factory)
        cache.invalidate("key")
        await cache.get_or_fetch("key", factory)
        return calls

    assert asyncio.run(scenario()) == 2
