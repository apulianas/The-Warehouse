from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import orioles_bot
from orioles_bot.formatting import (
    format_lineup,
    format_lineup_heading,
    format_moment,
    format_no_transactions,
    format_pitchers,
    format_transaction,
)
from orioles_bot.mlb import (
    MlbApiError,
    MlbClient,
    build_mlb_url,
    headshot_url,
    parse_game,
    parse_transaction,
    parse_transactions,
    savant_player_url,
    savant_preview_url,
    savant_team_matchup_url,
)
from orioles_bot.models import (
    GameInfo,
    LineupPlayer,
    PitcherInfo,
    Substitution,
    TransactionInfo,
    TransactionPlayer,
)


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
        "d_people:generic:headshot:67:current.png,w_180,q_auto:good"
        "/v1/people/12345/headshot/67/current"
    )


def test_headshot_url_accepts_a_larger_width() -> None:
    assert "w_426,q_auto:good" in headshot_url(12345, 426)


def test_headshot_url_always_requests_a_default_image() -> None:
    """Players with no photo must fall back to a silhouette, not a 404."""
    for width in (180, 426):
        assert "d_people:generic:headshot:67:current.png" in headshot_url(1, width)


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
    assert format_transaction(transaction) == (
        "**Recalled** — Baltimore Orioles recalled "
        "[Example Player](https://baseballsavant.mlb.com/savant-player/123) "
        "from Norfolk Tides."
    )


def test_no_transactions_message_mentions_date() -> None:
    assert "August" in format_no_transactions(date(2026, 8, 6))


@pytest.mark.parametrize(
    ("type_description", "description", "expected"),
    [
        ("Recalled", "Baltimore Orioles recalled RHP Cam Sanders from Norfolk.", True),
        (
            "Status Change",
            "Baltimore Orioles activated RHP Kyle Bradish from the 60-day IL.",
            True,
        ),
        (
            "Selected",
            "Baltimore Orioles selected the contract of RHP Roansy Contreras.",
            True,
        ),
        ("Optioned", "Baltimore Orioles optioned LHP Cade Povich to Norfolk.", False),
        (
            "Status Change",
            "Baltimore Orioles placed RHP Zach Eflin on the 15-day IL.",
            False,
        ),
        (
            "Designated for Assignment",
            "Baltimore Orioles designated 1B Coby Mayo for assignment.",
            False,
        ),
        (
            "Outright Assignment",
            "Baltimore Orioles sent RHP Matt Bowman outright to Norfolk Tides.",
            False,
        ),
        (
            "Assigned",
            "Baltimore Orioles sent LHP Trevor Rogers on a rehab assignment.",
            False,
        ),
    ],
)
def test_arrival_moves_are_told_apart_from_departures(
    type_description: str, description: str, expected: bool
) -> None:
    """A grouped roster move shows the incoming player, so this decides who."""
    transaction = TransactionInfo(
        transaction_id="1",
        date=date(2026, 8, 6),
        player_id=1,
        player_name="Some Player",
        type_description=type_description,
        description=description,
        headshot_url=None,
    )

    assert transaction.is_arrival is expected


def test_format_pitchers_labels_each_starter_by_throwing_hand() -> None:
    game = _game_with_pitchers(
        PitcherInfo(201, "Orioles Starter", status="Starting pitcher", throws="R"),
        PitcherInfo(401, "Opponent Starter", status="Probable pitcher", throws="L"),
    )

    assert format_pitchers(game) == (
        f"Baltimore Orioles starter: [Orioles Starter]({savant_player_url(201)})"
        " (RHP)\n"
        f"New York Yankees starter: [Opponent Starter]({savant_player_url(401)})"
        " (LHP, probable)"
    )


def test_format_pitchers_falls_back_to_status_without_a_known_hand() -> None:
    """Handedness comes from a separate lookup, which can come back empty."""
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


def test_format_pitchers_keeps_the_probable_caveat_on_a_default_status() -> None:
    game = _game_with_pitchers(PitcherInfo(201, "Orioles Starter", throws="R"), None)

    assert format_pitchers(game).startswith(
        f"Baltimore Orioles starter: [Orioles Starter]({savant_player_url(201)})"
        " (RHP, probable)"
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
        opponent_team_id=147,
        is_home=True,
        orioles_score=None,
        opponent_score=None,
        pitcher=pitcher,
        opponent_pitcher=opponent_pitcher,
        lineup=(),
        opponent_lineup=(),
    )


class _HandednessClient(MlbClient):
    """An MLB client whose only live call is the handedness lookup."""

    def __init__(self, hands: dict[int, tuple[str | None, str | None]]) -> None:
        super().__init__(session=None)  # type: ignore[arg-type]
        self._hands = hands
        self.requested: list[int] = []

    async def fetch_handedness(
        self, player_ids: Sequence[int]
    ) -> dict[int, tuple[str | None, str | None]]:
        self.requested.extend(player_ids)
        if self._hands is None:
            raise MlbApiError("handedness lookup failed")
        return {
            player_id: self._hands[player_id]
            for player_id in player_ids
            if player_id in self._hands
        }


def test_fetching_games_attaches_each_starters_throwing_hand() -> None:
    client = _HandednessClient({201: ("R", "R"), 401: ("L", "L")})
    game = _game_with_pitchers(
        PitcherInfo(201, "Orioles Starter", status="Starting pitcher"),
        PitcherInfo(401, "Opponent Starter", status="Probable pitcher"),
    )

    resolved = asyncio.run(client._with_pitcher_handedness([game]))[0]

    assert resolved.pitcher is not None and resolved.pitcher.throws == "R"
    assert resolved.opponent_pitcher is not None
    assert resolved.opponent_pitcher.throws == "L"
    # One batched lookup, so a full slate does not mean a request per starter.
    assert client.requested == [201, 401]


def test_a_failed_handedness_lookup_leaves_the_starters_alone() -> None:
    """A missing hand costs a label, not the card."""
    client = _HandednessClient(None)  # type: ignore[arg-type]
    game = _game_with_pitchers(
        PitcherInfo(201, "Orioles Starter", status="Starting pitcher"), None
    )

    resolved = asyncio.run(client._with_pitcher_handedness([game]))[0]

    assert resolved.pitcher is not None and resolved.pitcher.throws is None
    assert format_pitchers(resolved).startswith(
        f"Baltimore Orioles starter: [Orioles Starter]({savant_player_url(201)})"
        " (Starting pitcher)"
    )


def test_handedness_reaches_the_pitcher_a_substitute_will_face() -> None:
    pitcher = PitcherInfo(401, "Opponent Reliever", status="Pitching")
    batter = LineupPlayer(
        player_id=101,
        name="Pinch Hitter",
        position="PH",
        batting_order=3,
        headshot_url=None,
        substitution_order=1,
    )
    game = replace(
        _game_with_pitchers(None, pitcher),
        current_opponent_pitcher=pitcher,
        substitutions=(
            Substitution(
                game_pk=1,
                slot=3,
                batter=batter,
                replaced=None,
                pitcher=pitcher,
                is_orioles=True,
                batting_team="Baltimore Orioles",
                batting_team_id=110,
            ),
        ),
    )

    resolved = asyncio.run(
        _HandednessClient({401: ("L", "L")})._with_pitcher_handedness([game])
    )[0]

    assert resolved.substitutions[0].pitcher is not None
    assert resolved.substitutions[0].pitcher.throws == "L"


def test_savant_preview_url_uses_game_pk() -> None:
    assert savant_preview_url(823937) == (
        "https://baseballsavant.mlb.com/preview?game_pk=823937"
    )


def test_savant_team_matchup_url_builds_team_versus_pitcher_page() -> None:
    assert savant_team_matchup_url(110, 119, 808967) == (
        "https://baseballsavant.mlb.com/player_matchup?"
        "type=batter&teamPitching=119&teamBatting=110&player_id=808967"
    )


def test_format_lineup_heading_links_team_matchup() -> None:
    heading = format_lineup_heading(
        "Baltimore Orioles", 110, 119, PitcherInfo(808967, "Yoshinobu Yamamoto")
    )

    assert heading == (
        "**Baltimore Orioles batting order** — "
        "[full matchup vs Yoshinobu Yamamoto]"
        f"({savant_team_matchup_url(110, 119, 808967)})"
    )


def test_format_lineup_heading_omits_link_without_opposing_pitcher() -> None:
    assert format_lineup_heading("Baltimore Orioles", 110, 119, None) == (
        "**Baltimore Orioles batting order**"
    )


def test_format_lineup_heading_omits_link_without_team_ids() -> None:
    pitcher = PitcherInfo(808967, "Yoshinobu Yamamoto")

    assert format_lineup_heading("Baltimore Orioles", 110, None, pitcher) == (
        "**Baltimore Orioles batting order**"
    )


_TRADE_DESCRIPTION = (
    "Baltimore Orioles traded 1B Ryan O'Hearn, RF Ramón Laureano and cash to "
    "San Diego Padres for LHP Boston Bateman and SS Cobb Hightower."
)


def _trade_rows() -> list[dict[str, object]]:
    people = [
        (669720, "Ryan O'Hearn"),
        (657656, "Ramón Laureano"),
        (695549, "Boston Bateman"),
        (702000, "Cobb Hightower"),
    ]
    rows: list[dict[str, object]] = [
        {
            "id": 860371,
            "effectiveDate": "2025-07-31",
            "typeDesc": "Trade",
            "description": _TRADE_DESCRIPTION,
        }
    ]
    rows.extend(
        {
            "id": 860371,
            "effectiveDate": "2025-07-31",
            "person": {"id": person_id, "fullName": name},
            "typeDesc": "Trade",
            "description": _TRADE_DESCRIPTION,
        }
        for person_id, name in people
    )
    return rows


def test_parse_transactions_merges_rows_into_one_entry() -> None:
    merged = parse_transactions(_trade_rows(), date(2025, 7, 31))

    assert len(merged) == 1
    assert [player.name for player in merged[0].players] == [
        "Ryan O'Hearn",
        "Ramón Laureano",
        "Boston Bateman",
        "Cobb Hightower",
    ]
    assert merged[0].player_id == 669720
    assert merged[0].headshot_url == headshot_url(669720)


def test_format_transaction_links_every_player_in_a_trade() -> None:
    body = format_transaction(parse_transactions(_trade_rows(), date(2025, 7, 31))[0])

    for player_id, name in (
        (669720, "Ryan O'Hearn"),
        (657656, "Ramón Laureano"),
        (695549, "Boston Bateman"),
        (702000, "Cobb Hightower"),
    ):
        assert f"[{name}]({savant_player_url(player_id)})" in body
    assert "San Diego Padres" in body
    assert body.count("savant-player") == 4


def test_format_transaction_does_not_nest_links_for_overlapping_names() -> None:
    transaction = TransactionInfo(
        transaction_id="1",
        date=date(2026, 8, 6),
        player_id=1,
        player_name="Bobby Witt",
        type_description="Trade",
        description="Orioles traded Bobby Witt Jr. and Bobby Witt for cash.",
        headshot_url=None,
        players=(
            TransactionPlayer(1, "Bobby Witt"),
            TransactionPlayer(2, "Bobby Witt Jr."),
        ),
    )

    body = format_transaction(transaction)

    assert "[Bobby Witt Jr.](https://baseballsavant.mlb.com/savant-player/2)" in body
    assert body.count("](") == 2
    assert "savant-player/1)" in body


def test_format_transaction_falls_back_when_description_omits_the_player() -> None:
    transaction = TransactionInfo(
        transaction_id="2",
        date=date(2026, 8, 6),
        player_id=7,
        player_name="Hidden Player",
        type_description="Status Change",
        description="Baltimore Orioles activated a player from the injured list.",
        headshot_url=None,
        players=(TransactionPlayer(7, "Hidden Player"),),
    )

    assert format_transaction(transaction) == (
        "**Status Change** — "
        f"[Hidden Player]({savant_player_url(7)}): "
        "Baltimore Orioles activated a player from the injured list."
    )


# `%-d` and `%-I` are a glibc extension. They render "Aug 6" and "7:05 PM"
# instead of "Aug 06" and "07:05 PM" on Linux and macOS, and raise ValueError
# on Windows. This bot is written on a Mac, maintained from a PC and deployed
# in a Linux container, so every date goes through `format_moment`.
PACKAGE_SOURCES = sorted(Path(orioles_bot.__file__).parent.glob("*.py"))
INLINE_DATE_FORMAT_SPEC = re.compile(r"\{[^{}]*:%")


def test_format_moment_strips_the_leading_zero_from_a_day() -> None:
    assert format_moment(date(2026, 8, 6), "%b %-d, %Y") == "Aug 6, 2026"
    assert format_moment(date(2026, 8, 16), "%b %-d, %Y") == "Aug 16, 2026"


def test_format_moment_strips_the_leading_zero_from_an_hour() -> None:
    morning = datetime(2026, 8, 6, 7, 5, tzinfo=timezone.utc)
    evening = datetime(2026, 8, 6, 19, 5, tzinfo=timezone.utc)

    assert format_moment(morning, "%-I:%M %p") == "7:05 AM"
    assert format_moment(evening, "%-I:%M %p") == "7:05 PM"


def test_format_moment_renders_midnight_as_twelve() -> None:
    """`hour % 12` is 0 at midnight, which is not an hour anyone writes."""
    midnight = datetime(2026, 8, 29, 0, 22, tzinfo=timezone.utc)

    assert format_moment(midnight, "%-I:%M %p") == "12:22 AM"
    assert format_moment(midnight.replace(hour=12), "%-I:%M %p") == "12:22 PM"


def test_format_moment_leaves_an_hour_alone_for_a_plain_date() -> None:
    """A `date` has no hour, so an hour directive must not be substituted."""
    assert format_moment(date(2026, 8, 6), "%b %-d") == "Aug 6"


def test_dates_only_reach_strftime_through_the_portable_helper() -> None:
    """One `strftime` call in the package, inside `format_moment`."""
    calls = {
        path.name: path.read_text(encoding="utf-8").count(".strftime(")
        for path in PACKAGE_SOURCES
    }

    assert {name: n for name, n in calls.items() if n} == {"formatting.py": 1}


def test_no_date_is_rendered_with_an_inline_format_spec() -> None:
    """`f"{when:%b %-d}"` goes straight to `strftime` and breaks on Windows."""
    offenders = [
        f"{path.name}:{number}: {line.strip()}"
        for path in PACKAGE_SOURCES
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if INLINE_DATE_FORMAT_SPEC.search(line)
    ]

    assert offenders == []
