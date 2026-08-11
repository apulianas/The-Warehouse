from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
import pytest

from dataclasses import replace

from orioles_bot.bot import OriolesBot, poll_interval_for
from orioles_bot.config import BotConfig
from orioles_bot.formatting import format_matchup_history, format_platoon_split
from orioles_bot.matchups import MatchupService, calculate_matchup_history
from orioles_bot.mlb import headshot_url
from orioles_bot.models import (
    GameInfo,
    HittingSplit,
    LineupPlayer,
    PitcherInfo,
    TransactionInfo,
    TransactionPlayer,
)


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


class _StubMlb:
    def __init__(self, games: list[GameInfo]) -> None:
        self._games = games

    async def fetch_games(self, target_date: object) -> list[GameInfo]:
        return self._games

    async def fetch_transactions(self, target_date: object) -> list[object]:
        return []


def _lineup_game() -> GameInfo:
    player = LineupPlayer(
        player_id=1,
        name="Player",
        position="CF",
        batting_order=1,
        headshot_url=None,
    )
    return replace(
        _game("In Progress", abstract="Live", coded="I"),
        lineup=(player,),
        opponent_lineup=(player,),
    )


def _run_poll(config: BotConfig) -> tuple[list[str], list[str]]:
    """Poll once with the network stubbed out, returning where each card went."""
    bot = OriolesBot(config)
    bot.mlb = _StubMlb([_lineup_game()])
    lineup_targets: list[str] = []
    substitution_targets: list[str] = []

    async def fake_targets(
        channel_ids: tuple[int, ...], webhook_urls: tuple[str, ...]
    ) -> list[str]:
        return [str(channel_id) for channel_id in channel_ids]

    async def fake_lineup(game, targets, target_date):  # type: ignore[no-untyped-def]
        lineup_targets.extend(targets)

    async def fake_substitutions(game, targets, target_date):  # type: ignore[no-untyped-def]
        substitution_targets.extend(targets)

    bot._announcement_targets = fake_targets  # type: ignore[method-assign]
    bot._announce_lineup = fake_lineup  # type: ignore[method-assign]
    bot._announce_substitutions = fake_substitutions  # type: ignore[method-assign]

    async def run() -> None:
        await OriolesBot.poll_updates.coro(bot)

    asyncio.run(run())
    return lineup_targets, substitution_targets


def test_substitutions_follow_the_lineup_channel_by_default() -> None:
    lineup, substitutions = _run_poll(CONFIG)

    assert lineup == ["123"]
    assert substitutions == ["123"]


def test_substitutions_go_to_their_own_channel_when_set() -> None:
    """The lineup card stays put while the in-game spam moves elsewhere."""
    config = replace(CONFIG, substitution_channel_ids=(456,))

    lineup, substitutions = _run_poll(config)

    assert lineup == ["123"]
    assert substitutions == ["456"]


POVICH_ID = 683551
SANDERS_ID = 681857
MAYO_ID = 683002
CONTRERAS_ID = 665795
MORTON_ID = 450203


def _transaction(
    transaction_id: str,
    type_description: str,
    description: str,
    player_id: int,
    name: str,
) -> TransactionInfo:
    return TransactionInfo(
        transaction_id=transaction_id,
        date=date(2026, 8, 7),
        player_id=player_id,
        player_name=name,
        type_description=type_description,
        description=description,
        headshot_url=headshot_url(player_id),
        players=(TransactionPlayer(player_id, name),),
    )


OPTIONED = _transaction(
    "1",
    "Optioned",
    "Baltimore Orioles optioned LHP Cade Povich to Norfolk Tides.",
    POVICH_ID,
    "Cade Povich",
)
RECALLED = _transaction(
    "2",
    "Recalled",
    "Baltimore Orioles recalled RHP Cam Sanders from Norfolk Tides.",
    SANDERS_ID,
    "Cam Sanders",
)
DESIGNATED = _transaction(
    "3",
    "Designated for Assignment",
    "Baltimore Orioles designated 1B Coby Mayo for assignment.",
    MAYO_ID,
    "Coby Mayo",
)
SELECTED = _transaction(
    "4",
    "Selected",
    "Baltimore Orioles selected the contract of RHP Roansy Contreras from Norfolk Tides.",
    CONTRERAS_ID,
    "Roansy Contreras",
)
TRADE = _transaction(
    "5",
    "Trade",
    "Baltimore Orioles traded RHP Charlie Morton to Detroit Tigers for cash.",
    MORTON_ID,
    "Charlie Morton",
)


class _RecordingDestination:
    def __init__(self) -> None:
        self.sent: list[list[object]] = []

    async def send(self, *, embeds: list[object]) -> None:
        self.sent.append(embeds)


class _TransactionMlb:
    def __init__(self, transactions: list[TransactionInfo]) -> None:
        self.transactions = transactions

    async def fetch_games(self, target_date: object) -> list[GameInfo]:
        return []

    async def fetch_transactions(self, target_date: object) -> list[TransactionInfo]:
        return self.transactions


def _transaction_bot(
    tmp_path, transactions: list[TransactionInfo]
) -> tuple[OriolesBot, _RecordingDestination, _TransactionMlb]:
    from orioles_bot.bot import _AnnouncementTarget

    bot = OriolesBot(replace(CONFIG, state_file=str(tmp_path / "state.json")))
    mlb = _TransactionMlb(transactions)
    bot.mlb = mlb  # type: ignore[assignment]
    destination = _RecordingDestination()
    target = _AnnouncementTarget("123", "channel 123", destination)  # type: ignore[arg-type]

    async def fake_targets(
        channel_ids: tuple[int, ...], webhook_urls: tuple[str, ...]
    ) -> list[object]:
        return [target]

    bot._announcement_targets = fake_targets  # type: ignore[method-assign]
    return bot, destination, mlb


def _poll(bot: OriolesBot) -> None:
    asyncio.run(OriolesBot.poll_updates.coro(bot))


def test_related_roster_moves_post_as_one_card(tmp_path) -> None:
    """An option out and the recall it pays for are one piece of news."""
    bot, destination, _ = _transaction_bot(tmp_path, [OPTIONED, RECALLED])

    _poll(bot)

    assert len(destination.sent) == 1
    embeds = destination.sent[0]
    assert len(embeds) == 1
    fields = embeds[0].to_dict()["fields"]  # type: ignore[attr-defined]
    assert [field["name"] for field in fields] == [
        "Joining the roster",
        "Leaving the roster",
    ]
    assert "Cam Sanders" in fields[0]["value"]
    assert "Cade Povich" in fields[1]["value"]


def test_a_card_carries_no_per_move_date(tmp_path) -> None:
    """The title already dates the card, so a date per row was only noise."""
    bot, destination, _ = _transaction_bot(tmp_path, [OPTIONED, RECALLED])

    _poll(bot)

    payload = destination.sent[0][0].to_dict()  # type: ignore[attr-defined]
    assert payload["title"].startswith("Orioles transactions — ")
    assert not any(
        re.search(r"\d{4}-\d{2}-\d{2}", field["name"]) for field in payload["fields"]
    )


def test_two_arrivals_each_get_their_own_thumbnail(tmp_path) -> None:
    """One embed carries one face, so a second call-up gets a card of his own."""
    bot, destination, _ = _transaction_bot(
        tmp_path, [OPTIONED, DESIGNATED, RECALLED, SELECTED]
    )

    _poll(bot)

    assert len(destination.sent) == 1
    embeds = [embed.to_dict() for embed in destination.sent[0]]  # type: ignore[attr-defined]
    assert len(embeds) == 3
    assert embeds[0]["thumbnail"]["url"] == headshot_url(SANDERS_ID)
    assert embeds[1]["thumbnail"]["url"] == headshot_url(CONTRERAS_ID)
    # Thumbnails only: a column of full-width photos fills a phone screen.
    assert not any("image" in embed for embed in embeds)
    # The heading is not repeated on the second player's card.
    assert embeds[0]["fields"][0]["name"] == "Joining the roster"
    assert embeds[1]["fields"][0]["name"] == "\u200b"
    # Everyone leaving shares the closing card.
    assert embeds[2]["fields"][0]["name"] == "Leaving the roster"
    assert "Cade Povich" in embeds[2]["fields"][0]["value"]
    assert "Coby Mayo" in embeds[2]["fields"][0]["value"]


def test_a_trade_is_not_forced_onto_either_side(tmp_path) -> None:
    """A trade names both directions, so claiming it for one would misread it."""
    bot, destination, _ = _transaction_bot(tmp_path, [RECALLED, TRADE])

    _poll(bot)

    fields = destination.sent[0][0].to_dict()["fields"]  # type: ignore[attr-defined]
    assert [field["name"] for field in fields] == [
        "Joining the roster",
        "Other moves",
    ]
    assert "Charlie Morton" in fields[1]["value"]


def test_a_grouped_card_shows_the_arriving_player(tmp_path) -> None:
    """The recall is the news, even though the option is listed first."""
    bot, destination, _ = _transaction_bot(tmp_path, [OPTIONED, RECALLED])

    _poll(bot)

    payload = destination.sent[0][0].to_dict()  # type: ignore[attr-defined]
    assert "image" not in payload
    assert payload["thumbnail"]["url"] == headshot_url(SANDERS_ID)


def test_a_lone_move_keeps_a_thumbnail(tmp_path) -> None:
    """No post should fill a phone screen, however few moves it covers."""
    bot, destination, _ = _transaction_bot(tmp_path, [RECALLED])

    _poll(bot)

    embeds = destination.sent[0]
    payload = embeds[0].to_dict()  # type: ignore[attr-defined]
    assert "image" not in payload
    assert payload["thumbnail"]["url"] == headshot_url(SANDERS_ID)


def test_a_later_move_does_not_repeat_the_ones_already_posted(tmp_path) -> None:
    bot, destination, mlb = _transaction_bot(tmp_path, [OPTIONED, RECALLED])
    _poll(bot)

    mlb.transactions = [OPTIONED, RECALLED, DESIGNATED]
    _poll(bot)

    assert len(destination.sent) == 2
    embeds = destination.sent[1]
    fields = embeds[0].to_dict()["fields"]  # type: ignore[attr-defined]
    assert len(fields) == 1
    assert "Coby Mayo" in fields[0]["value"]


def test_nothing_is_posted_when_every_move_has_been_seen(tmp_path) -> None:
    bot, destination, _ = _transaction_bot(tmp_path, [OPTIONED, RECALLED])
    _poll(bot)

    _poll(bot)

    assert len(destination.sent) == 1


def test_a_failed_post_leaves_the_whole_card_unannounced(tmp_path) -> None:
    """A partial mark would repost only some of a grouped card next time."""
    bot, destination, _ = _transaction_bot(tmp_path, [OPTIONED, RECALLED])

    async def refuse(*args: object, **kwargs: object) -> None:
        raise discord.DiscordException("channel is gone")

    destination.send = refuse  # type: ignore[method-assign]
    _poll(bot)

    destination.send = _RecordingDestination.send.__get__(destination)  # type: ignore[method-assign]
    _poll(bot)

    assert len(destination.sent) == 1
    assert len(destination.sent[0][0].to_dict()["fields"]) == 2  # type: ignore[attr-defined]
