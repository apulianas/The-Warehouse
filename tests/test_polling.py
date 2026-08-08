from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from orioles_bot.bot import poll_interval_for
from orioles_bot.config import BotConfig
from orioles_bot.formatting import format_matchup_history, format_platoon_split
from orioles_bot.matchups import MatchupService, calculate_matchup_history
from orioles_bot.models import GameInfo, HittingSplit, PitcherInfo


NOW = datetime(2026, 8, 7, 18, 0, tzinfo=timezone.utc)

CONFIG = BotConfig(
    discord_token="token",
    discord_channel_ids=(123,),
    discord_webhook_urls=(),
    poll_interval_seconds=300,
    matchup_min_pa=5,
    time_zone=ZoneInfo("UTC"),
    live_poll_interval_seconds=60,
    pregame_poll_interval_seconds=120,
    pregame_lead_minutes=240,
    state_file="state.json",
)


def _game(
    status: str,
    *,
    abstract: str = "",
    coded: str = "",
    starts_in: timedelta | None = None,
    game_pk: int = 1,
) -> GameInfo:
    return GameInfo(
        game_pk=game_pk,
        game_date=None if starts_in is None else NOW + starts_in,
        status=status,
        venue="Oriole Park",
        home_team="Baltimore Orioles",
        home_team_id=110,
        away_team="Tampa Bay Rays",
        opponent="Tampa Bay Rays",
        opponent_team_id=139,
        is_home=True,
        orioles_score=None,
        opponent_score=None,
        pitcher=None,
        opponent_pitcher=None,
        lineup=(),
        opponent_lineup=(),
        abstract_status=abstract,
        coded_status=coded,
    )


def test_idle_when_no_games() -> None:
    assert poll_interval_for([], NOW, CONFIG)[0] == 300


def test_live_game_polls_fastest() -> None:
    game = _game("In Progress", abstract="Live", coded="I", starts_in=timedelta(hours=-1))

    assert poll_interval_for([game], NOW, CONFIG)[0] == 60


def test_pregame_window_polls_faster_than_idle() -> None:
    game = _game("Scheduled", abstract="Preview", coded="S", starts_in=timedelta(hours=3))

    assert poll_interval_for([game], NOW, CONFIG)[0] == 120


def test_game_beyond_the_window_stays_idle() -> None:
    game = _game("Scheduled", abstract="Preview", coded="S", starts_in=timedelta(hours=9))

    assert poll_interval_for([game], NOW, CONFIG)[0] == 300


def test_finished_game_returns_to_idle() -> None:
    game = _game("Final", abstract="Final", coded="F", starts_in=timedelta(hours=-4))

    assert poll_interval_for([game], NOW, CONFIG)[0] == 300


def test_missing_start_time_is_treated_as_imminent() -> None:
    game = _game("Scheduled", abstract="Preview", coded="S")

    assert poll_interval_for([game], NOW, CONFIG)[0] == 120


@pytest.mark.parametrize(
    ("status", "coded"),
    [
        ("Postponed", "D"),
        ("Cancelled", "C"),
        ("Suspended", "U"),
        # MLB has two suspended code families and reports both as "Live".
        ("Suspended: Rain", "U"),
        ("Suspended: Rain", "T"),
        ("Suspended: Appeal Upheld", "T"),
    ],
)
def test_called_off_games_do_not_hold_the_fast_cadence(status: str, coded: str) -> None:
    """A postponed game keeps today's start time, so it must not count.

    Without this the bot would poll every two minutes until midnight for a game
    that is never played. Suspended games arrive as abstract "Live", so relying
    on the abstract state alone would pin the bot to the live cadence instead.
    """
    game = _game(status, abstract="Final", coded=coded, starts_in=timedelta(hours=1))

    assert poll_interval_for([game], NOW, CONFIG)[0] == 300


def test_suspended_game_reported_as_live_is_not_in_progress() -> None:
    """`codedGameState` "T" and "U" are suspended, but abstract says "Live"."""
    for coded in ("T", "U"):
        game = _game(
            "Suspended: Rain",
            abstract="Live",
            coded=coded,
            starts_in=timedelta(hours=-1),
        )

        assert game.is_unplayed
        assert not game.is_in_progress
        assert poll_interval_for([game], NOW, CONFIG)[0] == 300


def test_covid_scheduled_shares_the_suspended_code_but_is_pregame() -> None:
    """`codedGameState` "T" also covers "Scheduled: COVID-19", which is Preview.

    This is why the detailed state decides rather than the coded one.
    """
    game = _game(
        "Scheduled: COVID-19",
        abstract="Preview",
        coded="T",
        starts_in=timedelta(hours=2),
    )

    assert not game.is_unplayed
    assert not game.has_started
    assert poll_interval_for([game], NOW, CONFIG)[0] == 120


def test_warmup_is_reported_live_but_has_not_started() -> None:
    """MLB gives warmup abstract "Live" even though there is no first pitch yet.

    `_announce_substitutions` is gated on `has_started`, so a batting order
    change here must still count as a lineup correction.
    """
    game = _game("Warmup", abstract="Live", coded="P", starts_in=timedelta(minutes=20))

    assert not game.has_started
    assert not game.is_in_progress
    assert poll_interval_for([game], NOW, CONFIG)[0] == 120


def test_delayed_start_keeps_the_pregame_cadence() -> None:
    """First pitch is overdue, so the lineup is out and the game is imminent."""
    game = _game(
        "Delayed Start: Rain",
        abstract="Preview",
        coded="P",
        starts_in=timedelta(hours=-2),
    )

    assert poll_interval_for([game], NOW, CONFIG)[0] == 120


def test_rain_delay_after_first_pitch_stays_live() -> None:
    """MLB keeps a delayed game "Live"; play can resume at any moment."""
    game = _game(
        "Delayed: Rain", abstract="Live", coded="I", starts_in=timedelta(hours=-1)
    )

    assert poll_interval_for([game], NOW, CONFIG)[0] == 60


def test_doubleheader_uses_the_more_urgent_game() -> None:
    postponed = _game(
        "Postponed", abstract="Final", coded="D", starts_in=timedelta(hours=1), game_pk=1
    )
    live = _game(
        "In Progress",
        abstract="Live",
        coded="I",
        starts_in=timedelta(hours=-1),
        game_pk=2,
    )

    assert poll_interval_for([postponed, live], NOW, CONFIG)[0] == 60


def test_finished_opener_does_not_hide_the_nightcap() -> None:
    opener = _game(
        "Final", abstract="Final", coded="F", starts_in=timedelta(hours=-5), game_pk=1
    )
    nightcap = _game(
        "Scheduled", abstract="Preview", coded="S", starts_in=timedelta(hours=2), game_pk=2
    )

    assert poll_interval_for([opener, nightcap], NOW, CONFIG)[0] == 120


class TestUnavailableDataIsNotAClaim:
    """A failed fetch must not be published as "no plate appearances"."""

    def test_missing_history_reads_as_unavailable(self) -> None:
        pitcher = PitcherInfo(
            player_id=1,
            name="Ace Reliever",
            headshot_url="",
            status="Pitching",
        )

        assert "unavailable" in format_matchup_history(None, pitcher)

    def test_empty_history_still_reads_as_no_appearances(self) -> None:
        pitcher = PitcherInfo(
            player_id=1,
            name="Ace Reliever",
            headshot_url="",
            status="Pitching",
        )
        empty = calculate_matchup_history([])

        text = format_matchup_history(empty, pitcher)
        assert "No prior plate appearances" in text
        assert "unavailable" not in text

    def test_missing_split_reads_as_unavailable(self) -> None:
        assert "unavailable" in format_platoon_split(None, "L")

    def test_empty_split_still_reads_as_no_appearances(self) -> None:
        text = format_platoon_split(HittingSplit(), "L")

        assert "No plate appearances" in text
        assert "unavailable" not in text


class TestFailedFetchesAreNotCached:
    def test_a_failed_matchup_is_retried_on_the_next_call(self) -> None:
        """A transient Statcast outage must not poison the pair for the process."""
        calls: list[tuple[int, int]] = []
        rows = [{"events": "single", "woba_value": "0.9", "woba_denom": "1"}]

        def fetcher(batter_id: int, pitcher_id: int) -> object:
            calls.append((batter_id, pitcher_id))
            if len(calls) == 1:
                raise RuntimeError("Statcast is down (HTTP 503)")
            return rows

        service = MatchupService(min_pa=1, fetcher=fetcher)

        first = asyncio.run(service.history_many([(1, 2)]))
        assert first == {}

        second = asyncio.run(service.history_many([(1, 2)]))
        assert (1, 2) in second
        assert second[(1, 2)].hits == 1
        assert len(calls) == 2

    def test_an_empty_matchup_is_cached(self) -> None:
        calls: list[tuple[int, int]] = []

        def fetcher(batter_id: int, pitcher_id: int) -> object:
            calls.append((batter_id, pitcher_id))
            return []

        service = MatchupService(min_pa=1, fetcher=fetcher)

        asyncio.run(service.history_many([(1, 2)]))
        asyncio.run(service.history_many([(1, 2)]))

        assert len(calls) == 1



    """Callers without the abstract state fall back to the display status."""

    def test_reason_suffix_does_not_break_matching(self) -> None:
        assert _game("Postponed: Rain").is_unplayed
        assert not _game("Postponed: Rain").has_started

    def test_delayed_start_is_still_pregame(self) -> None:
        game = _game("Delayed Start: Rain")

        assert not game.has_started
        assert not game.is_final

    def test_delay_after_first_pitch_is_in_progress(self) -> None:
        game = _game("Delayed: Rain")

        assert game.has_started
        assert game.is_in_progress

    def test_final_is_not_in_progress(self) -> None:
        game = _game("Game Over")

        assert game.has_started
        assert game.is_final
        assert not game.is_in_progress

    def test_postponed_is_never_in_progress(self) -> None:
        game = _game("Postponed")

        assert game.is_unplayed
        assert game.is_final
        assert not game.is_in_progress
