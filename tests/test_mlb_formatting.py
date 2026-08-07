from __future__ import annotations

from datetime import date

from orioles_bot.formatting import (
    format_lineup,
    format_no_transactions,
    format_pitchers,
    format_transaction,
)
from orioles_bot.mlb import (
    build_mlb_url,
    headshot_url,
    parse_game,
    parse_transaction,
    savant_player_url,
    savant_preview_url,
)
from orioles_bot.models import GameInfo, LineupPlayer, PitcherInfo


def test_build_mlb_url_sorts_and_encodes_query_params() -> None:
    url = build_mlb_url(
        "/schedule",
        {"teamId": 110, "hydrate": "probablePitcher,team", "date": "2026-08-06"},
    )

    assert url == (
        "https://statsapi.mlb.com/api/v1/schedule?"
        "date=2026-08-06&hydrate=probablePitcher%2Cteam&teamId=110"
    )


def test_headshot_url_uses_mlb_static_template() -> None:
    assert headshot_url(12345) == (
        "https://img.mlbstatic.com/mlb-photos/image/upload/"
        "w_180,q_auto:good/v1/people/12345/headshot/67/current"
    )


def test_parse_game_extracts_orioles_lineup_from_live_feed() -> None:
    raw_game = {
        "gamePk": 1,
        "gameDate": "2026-08-06T23:05:00Z",
        "status": {"detailedState": "Pre-Game"},
        "venue": {"name": "Oriole Park at Camden Yards"},
        "teams": {
            "home": {
                "team": {"id": 110, "name": "Baltimore Orioles"},
                "probablePitcher": {"id": 99, "fullName": "Probable Pitcher"},
            },
            "away": {
                "team": {"id": 147, "name": "New York Yankees"},
                "probablePitcher": {"id": 199, "fullName": "Opponent Starter"},
            },
        },
    }
    feed = {
        "liveData": {
            "boxscore": {
                "teams": {
                    "home": {
                        "battingOrder": ["101", "102"],
                        "pitchers": [201],
                        "players": {
                            "ID101": {
                                "person": {"fullName": "Leadoff Hitter"},
                                "position": {"abbreviation": "CF"},
                            },
                            "ID102": {
                                "person": {"fullName": "Second Hitter"},
                                "position": {"abbreviation": "SS"},
                            },
                            "ID201": {
                                "person": {"fullName": "Confirmed Starter"},
                            },
                        },
                    }
                }
            }
        }
    }

    game = parse_game(raw_game, feed)

    assert game.opponent == "New York Yankees"
    assert game.is_home is True
    assert [player.name for player in game.lineup] == ["Leadoff Hitter", "Second Hitter"]
    assert game.opponent_lineup == ()
    assert game.pitcher is not None
    assert game.pitcher.name == "Confirmed Starter"
    assert game.pitcher.status == "Starting pitcher"
    assert game.opponent_pitcher is not None
    assert game.opponent_pitcher.name == "Opponent Starter"


def test_savant_player_url_uses_player_id() -> None:
    assert savant_player_url(12345) == (
        "https://baseballsavant.mlb.com/savant-player/12345"
    )


def test_format_lineup_links_savant_player_page_without_pitcher() -> None:
    player = LineupPlayer(
        player_id=101,
        name="Leadoff Hitter",
        position="CF",
        batting_order=1,
        headshot_url=headshot_url(101),
    )

    assert format_lineup((player,)) == (
        "1. CF [Leadoff Hitter](https://baseballsavant.mlb.com/savant-player/101)"
    )


def test_parse_and_format_transaction() -> None:
    transaction = parse_transaction(
        {
            "id": 55,
            "effectiveDate": "2026-08-06",
            "person": {"id": 123, "fullName": "Example Player"},
            "typeDesc": "Recalled",
            "description": "Baltimore Orioles recalled Example Player from Norfolk Tides.",
        },
        date(2026, 8, 6),
    )

    assert transaction.transaction_id == "55"
    assert transaction.headshot_url == headshot_url(123)
    assert format_transaction(transaction).startswith(
        "**Recalled** — [Example Player](https://baseballsavant.mlb.com/savant-player/123)"
    )


def test_no_transactions_message_mentions_date() -> None:
    assert "August" in format_no_transactions(date(2026, 8, 6))


def test_format_pitchers_renders_both_starters() -> None:
    game = _game_with_pitchers(
        PitcherInfo(201, "Orioles Starter", status="Starting pitcher"),
        PitcherInfo(401, "Opponent Starter", status="Probable pitcher"),
    )

    assert format_pitchers(game) == (
        f"Baltimore Orioles starter: [Orioles Starter]({savant_player_url(201)})"
        " (Starting pitcher)\n"
        f"New York Yankees starter: [Opponent Starter]({savant_player_url(401)})"
        " (Probable pitcher)"
    )


def test_format_pitchers_handles_unannounced_opponent() -> None:
    game = _game_with_pitchers(PitcherInfo(201, "Orioles Starter"), None)

    assert format_pitchers(game).endswith("New York Yankees starter: not announced")


def _game_with_pitchers(
    pitcher: PitcherInfo | None, opponent_pitcher: PitcherInfo | None
) -> GameInfo:
    return GameInfo(
        game_pk=1,
        game_date=None,
        status="Pre-Game",
        venue="Oriole Park at Camden Yards",
        home_team="Baltimore Orioles",
        home_team_id=110,
        away_team="New York Yankees",
        opponent="New York Yankees",
        is_home=True,
        orioles_score=None,
        opponent_score=None,
        pitcher=pitcher,
        opponent_pitcher=opponent_pitcher,
        lineup=(),
        opponent_lineup=(),
    )


def test_savant_preview_url_uses_game_pk() -> None:
    assert savant_preview_url(823937) == (
        "https://baseballsavant.mlb.com/preview?game_pk=823937"
    )
