from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from orioles_bot.embeds import no_live_game_embed, on_deck_embed
from orioles_bot.formatting import (
    format_at_bat_heading,
    format_at_bat_slots,
    format_count,
    format_half_inning,
    format_ordinal,
    format_runners,
)
from orioles_bot.mlb import parse_linescore
from orioles_bot.models import AtBatState, GameInfo, PlayerRef


def game(is_home: bool = False, opponent_team_id: int | None = 147) -> GameInfo:
    return GameInfo(
        game_pk=778001,
        game_date=datetime(2026, 8, 12, 23, 5, tzinfo=UTC),
        status="In Progress",
        venue="Yankee Stadium",
        home_team="New York Yankees" if not is_home else "Baltimore Orioles",
        home_team_id=147 if not is_home else 110,
        away_team="Baltimore Orioles" if not is_home else "New York Yankees",
        opponent="New York Yankees",
        opponent_team_id=opponent_team_id,
        is_home=is_home,
        orioles_score=2,
        opponent_score=1,
        pitcher=None,
        opponent_pitcher=None,
        lineup=(),
        opponent_lineup=(),
        abstract_status="Live",
    )


LINESCORE: dict[str, Any] = {
    "currentInning": 7,
    "inningState": "Top",
    "isTopInning": True,
    "balls": 2,
    "strikes": 1,
    "outs": 1,
    "offense": {
        "batter": {"id": 683002, "fullName": "Gunnar Henderson"},
        "onDeck": {"id": 668939, "fullName": "Adley Rutschman"},
        "inHole": {"id": 671218, "fullName": "Colton Cowser"},
        "first": {"id": 656775, "fullName": "Cedric Mullins"},
        "third": {"id": 663624, "fullName": "Jordan Westburg"},
    },
    "defense": {"pitcher": {"id": 543037, "fullName": "Gerrit Cole"}},
}


def test_a_linescore_becomes_the_current_at_bat() -> None:
    state = parse_linescore(LINESCORE, game())

    assert state.game_pk == 778001
    assert state.inning == 7
    assert state.is_top_inning
    assert state.batter == PlayerRef(683002, "Gunnar Henderson")
    assert state.on_deck == PlayerRef(668939, "Adley Rutschman")
    assert state.in_hole == PlayerRef(671218, "Colton Cowser")
    assert state.pitcher == PlayerRef(543037, "Gerrit Cole")
    assert (state.balls, state.strikes, state.outs) == (2, 1, 1)


def test_the_batting_team_follows_the_half_inning() -> None:
    # Orioles are the visitors, so the top of the inning is theirs.
    away = parse_linescore(LINESCORE, game(is_home=False))
    assert away.batting_team == "Baltimore Orioles"
    assert away.batting_team_id == 110
    assert away.orioles_batting

    # Same half inning with the Orioles at home means the visitors are hitting.
    home = parse_linescore(LINESCORE, game(is_home=True))
    assert home.batting_team == "New York Yankees"
    assert home.batting_team_id == 147
    assert not home.orioles_batting


def test_the_bottom_of_an_inning_belongs_to_the_home_team() -> None:
    payload = dict(LINESCORE, isTopInning=False, inningState="Bottom")

    state = parse_linescore(payload, game(is_home=True))

    assert state.batting_team == "Baltimore Orioles"
    assert not state.is_top_inning


def test_a_linescore_without_an_at_bat_reads_as_empty() -> None:
    state = parse_linescore({"currentInning": 1, "isTopInning": True}, game())

    assert state.is_empty
    assert state.batter is None
    assert state.runners == ()


def test_malformed_linescore_players_are_dropped() -> None:
    payload: dict[str, Any] = {
        "isTopInning": True,
        "offense": {
            "batter": {"id": 1, "fullName": "Real Batter"},
            "onDeck": {"fullName": "No Identifier"},
            "inHole": "not-a-dict",
            "second": {"id": 2, "fullName": ""},
        },
        "defense": "not-a-dict",
    }

    state = parse_linescore(payload, game())

    assert state.batter == PlayerRef(1, "Real Batter")
    assert state.on_deck is None
    assert state.in_hole is None
    assert state.runner_on_second is None
    assert state.pitcher is None


def test_runners_read_from_third_base_down() -> None:
    state = parse_linescore(LINESCORE, game())

    assert [base for base, _ in state.runners] == ["3rd", "1st"]


def test_ordinals_handle_the_teens() -> None:
    assert [format_ordinal(value) for value in (1, 2, 3, 4, 11, 12, 13, 21)] == [
        "1st",
        "2nd",
        "3rd",
        "4th",
        "11th",
        "12th",
        "13th",
        "21st",
    ]


def test_the_half_inning_falls_back_to_the_top_flag() -> None:
    state = parse_linescore(dict(LINESCORE, inningState="", inningHalf=""), game())

    assert format_half_inning(state) == "Top 7th"


def test_an_unknown_inning_says_so() -> None:
    state = AtBatState(
        game_pk=1, batting_team="Baltimore Orioles", batting_team_id=110,
        is_top_inning=True,
    )

    assert format_half_inning(state) == "Inning unknown"


def test_the_heading_names_the_half_inning_and_the_batting_team() -> None:
    state = parse_linescore(LINESCORE, game())

    assert format_at_bat_heading(state) == "Top 7th — Baltimore Orioles batting"


def test_the_slots_are_labelled_and_linked() -> None:
    lines = format_at_bat_slots(parse_linescore(LINESCORE, game())).splitlines()

    assert lines[0].startswith("**At bat** — [Gunnar Henderson](")
    assert lines[1].startswith("**On deck** — [Adley Rutschman](")
    assert lines[2].startswith("**In the hole** — [Colton Cowser](")


def test_a_missing_slot_is_skipped_rather_than_left_blank() -> None:
    payload = dict(LINESCORE)
    payload["offense"] = {"batter": {"id": 1, "fullName": "Only Batter"}}

    assert format_at_bat_slots(parse_linescore(payload, game())).splitlines() == [
        "**At bat** — [Only Batter](https://baseballsavant.mlb.com/savant-player/1)"
    ]


def test_the_count_reads_balls_strikes_and_outs() -> None:
    assert format_count(parse_linescore(LINESCORE, game())) == "2-1 count, 1 out"


def test_the_count_pluralises_outs_and_tolerates_missing_pieces() -> None:
    payload = dict(LINESCORE, outs=2)
    assert format_count(parse_linescore(payload, game())) == "2-1 count, 2 outs"

    bare = {"isTopInning": True, "offense": LINESCORE["offense"]}
    assert format_count(parse_linescore(bare, game())) == ""


def test_empty_bases_say_so() -> None:
    payload = dict(LINESCORE)
    payload["offense"] = {"batter": {"id": 1, "fullName": "Only Batter"}}

    assert format_runners(parse_linescore(payload, game())) == "Bases empty"


def test_the_on_deck_embed_shows_the_at_bat() -> None:
    live = game()
    embed = on_deck_embed(parse_linescore(LINESCORE, live), live)

    assert embed.author.name == "Top 7th — Baltimore Orioles batting"
    assert "Gunnar Henderson" in (embed.description or "")
    assert "Adley Rutschman" in (embed.description or "")
    assert "Gerrit Cole" in (embed.description or "")
    assert "3rd [Jordan Westburg]" in (embed.description or "")
    assert "2-1 count, 1 out" in (embed.footer.text or "")


def test_the_on_deck_embed_says_so_when_mlb_has_posted_nothing() -> None:
    live = game()
    embed = on_deck_embed(parse_linescore({"isTopInning": True}, live), live)

    assert "has not posted the current at-bat" in (embed.description or "")


def test_the_no_live_game_embed_points_at_the_lineup_command() -> None:
    embed = no_live_game_embed(date(2026, 8, 12))

    assert "No Baltimore Orioles game is in progress" in (embed.description or "")
    assert "/lineup" in (embed.description or "")
