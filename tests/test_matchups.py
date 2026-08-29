from __future__ import annotations

import asyncio
import csv
import io
from datetime import date
from zoneinfo import ZoneInfo

from orioles_bot.embeds import lineup_embeds
from orioles_bot.formatting import format_lineup
from orioles_bot.matchups import (
    MatchupService,
    annotation_from_history,
    calculate_matchup_annotation,
    calculate_matchup_history,
    statcast_matchup_csv_url,
)
from orioles_bot.mlb import (
    headshot_url,
    savant_player_url,
    savant_preview_url,
    team_logo_url,
)
from orioles_bot.models import GameInfo, LineupPlayer, MatchupAnnotation, PitcherInfo


def _records(values: list[float]) -> list[dict[str, object]]:
    return [
        {"events": "single", "woba_value": value, "woba_denom": 1}
        for value in values
    ]


def test_calculate_matchup_annotation_hot() -> None:
    annotation = calculate_matchup_annotation(_records([0.7, 0.5, 0.4, 0.4, 0.2]), 5)

    assert annotation is not None
    assert annotation.emoji == "🔥"
    assert annotation.metric_name == "wOBA"
    assert round(annotation.metric_value, 3) == 0.44
    assert annotation.plate_appearances == 5


def test_calculate_matchup_annotation_cold() -> None:
    annotation = calculate_matchup_annotation(_records([0.0, 0.1, 0.2, 0.3, 0.4]), 5)

    assert annotation == MatchupAnnotation("🧊", "wOBA", 0.2, 5)


def test_calculate_matchup_annotation_neutral() -> None:
    annotation = calculate_matchup_annotation(_records([0.32, 0.32, 0.32, 0.32, 0.32]), 5)

    assert annotation is None


def test_calculate_matchup_annotation_insufficient_sample() -> None:
    annotation = calculate_matchup_annotation(_records([0.8, 0.8, 0.8, 0.8]), 5)

    assert annotation is None


def test_matchup_service_caches_pairs_for_process_lifetime() -> None:
    calls = 0

    def fetcher(batter_id: int, pitcher_id: int) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        assert (batter_id, pitcher_id) == (101, 201)
        return _records([0.7, 0.5, 0.4, 0.4, 0.2])

    async def run() -> None:
        service = MatchupService(min_pa=5, fetcher=fetcher)
        first = await service.fetch_many([(101, 201), (101, 201)])
        second = await service.fetch_many([(101, 201)])
        assert first == second

    asyncio.run(run())
    assert calls == 1


def test_format_lineup_links_savant_player_page_and_adds_annotation() -> None:
    player = LineupPlayer(101, "Leadoff Hitter", "CF", 1, headshot_url(101))
    pitcher = PitcherInfo(201, "Opponent Starter")
    annotation = MatchupAnnotation("🔥", "wOBA", 0.44, 5)

    assert format_lineup((player,), pitcher, {(101, 201): annotation}) == (
        "1. CF [Leadoff Hitter 🔥]"
        f"({savant_player_url(101)}) (.440 wOBA, 5 PA)"
    )


def test_format_lineup_gracefully_ignores_annotations_without_pitcher() -> None:
    player = LineupPlayer(101, "Leadoff Hitter", "CF", 1, headshot_url(101))
    annotation = MatchupAnnotation("🔥", "wOBA", 0.44, 5)

    assert format_lineup((player,), None, {(101, 201): annotation}) == (
        "1. CF [Leadoff Hitter]"
        f"({savant_player_url(101)})"
    )


def test_lineup_embeds_include_annotations_for_both_teams() -> None:
    orioles_hitter = LineupPlayer(101, "Orioles Hitter", "SS", 1, headshot_url(101))
    opponent_hitter = LineupPlayer(301, "Opponent Hitter", "LF", 1, headshot_url(301))
    game = GameInfo(
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
        pitcher=PitcherInfo(201, "Orioles Starter"),
        opponent_pitcher=PitcherInfo(401, "Opponent Starter"),
        lineup=(orioles_hitter,),
        opponent_lineup=(opponent_hitter,),
    )

    embed = lineup_embeds(
        [game],
        date(2026, 8, 6),
        ZoneInfo("UTC"),
        {
            (101, 401): MatchupAnnotation("🔥", "wOBA", 0.5, 8),
            (301, 201): MatchupAnnotation("🧊", "wOBA", 0.1, 6),
        },
    )[0]

    body = embed.description or ""
    assert "Orioles Hitter 🔥" in body
    assert "Opponent Hitter 🧊" in body
    assert savant_player_url(101) in body
    assert savant_player_url(301) in body
    assert f"New York Yankees starter: [Opponent Starter]({savant_player_url(401)})" in body
    assert f"Baltimore Orioles starter: [Orioles Starter]({savant_player_url(201)})" in body
    assert embed.thumbnail.url == team_logo_url(110)


def test_statcast_matchup_csv_url_matches_search_filters() -> None:
    assert statcast_matchup_csv_url(101, 201) == (
        "https://baseballsavant.mlb.com/statcast_search/csv?"
        "all=true&batters_lookup%5B%5D=101&pitchers_lookup%5B%5D=201&hfGT=R%7C&"
        "type=details"
    )


def test_calculate_matchup_annotation_parses_savant_csv_rows() -> None:
    header = "events,woba_value,woba_denom\n"
    body = "".join(f"single,{value},1\n" for value in [0.9, 0.9, 0.9, 0.9, 0.9])
    rows = list(csv.DictReader(io.StringIO(header + body)))

    annotation = calculate_matchup_annotation(rows, 5)

    assert annotation is not None
    assert annotation.emoji == "🔥"
    assert annotation.plate_appearances == 5


def test_annotation_reports_plate_appearances_not_woba_denominator() -> None:
    """An intentional walk is a plate appearance even though wOBA skips it.

    Pete Alonso against Matthew Liberatore: seven plate appearances, one of
    them an intentional walk that Statcast files with ``woba_denom`` of 0. The
    card used to report the denominator, so it read six.
    """
    records = [
        {"events": "field_out", "woba_value": 0, "woba_denom": 1},
        {"events": "double", "woba_value": 1.25, "woba_denom": 1},
        {"events": "field_out", "woba_value": 0, "woba_denom": 1},
        {"events": "strikeout", "woba_value": 0, "woba_denom": 1},
        {"events": "intent_walk", "woba_value": 0.4, "woba_denom": 0},
        {"events": "field_out", "woba_value": 0, "woba_denom": 1},
        {"events": "strikeout", "woba_value": 0, "woba_denom": 1},
    ]

    history = calculate_matchup_history(records)
    assert history.plate_appearances == 7
    assert history.woba_denominator == 6
    # The intentional walk is excluded from the rate itself, as wOBA requires.
    assert round(history.woba or 0, 3) == 0.208

    annotation = annotation_from_history(history, 5)
    assert annotation is not None
    assert annotation.emoji == "🧊"
    assert annotation.metric_name == "wOBA"
    assert annotation.plate_appearances == 7


def test_annotation_gates_on_plate_appearances_alone() -> None:
    """A thin wOBA denominator must not divert the sample to average.

    Five plate appearances including an intentional walk leave a denominator of
    four. That used to fall through to average, whose looser thresholds turned
    a cold .225 wOBA into no emoji at all.
    """
    records = [
        {"events": "single", "woba_value": 0.9, "woba_denom": 1},
        {"events": "field_out", "woba_value": 0, "woba_denom": 1},
        {"events": "strikeout", "woba_value": 0, "woba_denom": 1},
        {"events": "field_out", "woba_value": 0, "woba_denom": 1},
        {"events": "intent_walk", "woba_value": 0.4, "woba_denom": 0},
    ]

    annotation = annotation_from_history(calculate_matchup_history(records), 5)

    assert annotation == MatchupAnnotation("🧊", "wOBA", 0.225, 5)


def test_average_still_covers_a_matchup_with_no_woba_at_all() -> None:
    records = [{"events": "sac_bunt", "woba_value": 0.2, "woba_denom": 0}] * 5

    history = calculate_matchup_history(records)
    assert history.plate_appearances == 5
    assert history.woba is None
    # Every plate appearance was a sacrifice, so there is no average either.
    assert annotation_from_history(history, 5) is None


def test_truncated_plate_appearance_counts_as_neither_pa_nor_at_bat() -> None:
    """Statcast's ``truncated_pa`` ends a row without ending a plate appearance."""
    records = [
        {"events": "single", "woba_value": 0.9, "woba_denom": 1},
        {"events": "single", "woba_value": 0.9, "woba_denom": 1},
        {"events": "field_out", "woba_value": 0, "woba_denom": 1},
        {"events": "field_out", "woba_value": 0, "woba_denom": 1},
        {"events": "field_out", "woba_value": 0, "woba_denom": 1},
        {"events": "truncated_pa", "woba_value": 0, "woba_denom": 0},
    ]

    history = calculate_matchup_history(records)

    assert history.plate_appearances == 5
    assert history.at_bats == 5
    assert history.average == 0.4


def test_lineup_embeds_link_statcast_game_preview() -> None:
    game = GameInfo(
        game_pk=823937,
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
        pitcher=PitcherInfo(201, "Orioles Starter"),
        opponent_pitcher=PitcherInfo(401, "Opponent Starter"),
        lineup=(LineupPlayer(101, "Orioles Hitter", "SS", 1, headshot_url(101)),),
        opponent_lineup=(),
    )

    embed = lineup_embeds([game], date(2026, 8, 6), ZoneInfo("UTC"))[0]

    preview = savant_preview_url(823937)
    assert embed.url == preview
    assert (embed.description or "").endswith(f"[Statcast game preview]({preview})")


def test_lineup_embeds_keep_preview_link_when_description_overflows() -> None:
    roster = tuple(
        LineupPlayer(1000 + index, f"Player Name Number {index}", "SS", index, "")
        for index in range(1, 400)
    )
    game = GameInfo(
        game_pk=823937,
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
        pitcher=None,
        opponent_pitcher=None,
        lineup=roster,
        opponent_lineup=roster,
    )

    body = lineup_embeds([game], date(2026, 8, 6), ZoneInfo("UTC"))[0].description or ""

    assert len(body) <= 4096
    assert body.endswith(f"[Statcast game preview]({savant_preview_url(823937)})")


def test_announcement_state_keys_are_per_channel(tmp_path) -> None:
    from orioles_bot.state import AnnouncementState, channel_key

    state = AnnouncementState(str(tmp_path / "state.json"))
    state.load()
    state.mark(channel_key("lineup:1", 111))

    assert not state.unseen(channel_key("lineup:1", 111))
    assert state.unseen(channel_key("lineup:1", 222))


def test_announcement_state_adopts_legacy_keys(tmp_path) -> None:
    from orioles_bot.state import AnnouncementState, channel_key

    path = tmp_path / "state.json"
    path.write_text('{"announced": ["lineup:1", "transaction:2"]}', encoding="utf-8")

    state = AnnouncementState(str(path))
    state.load()
    state.adopt_legacy_keys(111)

    assert not state.unseen(channel_key("lineup:1", 111))
    assert not state.unseen(channel_key("transaction:2", 111))
    assert state.unseen(channel_key("lineup:1", 222))

    reloaded = AnnouncementState(str(path))
    reloaded.load()
    assert not reloaded.unseen(channel_key("lineup:1", 111))


def test_announcement_state_adopts_undated_keys(tmp_path) -> None:
    """Upgrading mid-game must not repost the cards already sent today."""
    from orioles_bot.state import AnnouncementState, channel_key

    path = tmp_path / "state.json"
    path.write_text(
        '{"announced": ['
        '"lineup:2026-08-28:824960:1:2,3@111",'
        '"substitution:2026-08-28:824960:7:1:681297@111",'
        '"transaction:2026-08-28:99@111"'
        "]}",
        encoding="utf-8",
    )

    state = AnnouncementState(str(path))
    state.load()
    state.adopt_undated_keys()

    assert not state.unseen(channel_key("lineup:824960:1:2,3", 111))
    assert not state.unseen(channel_key("substitution:824960:7:1:681297", 111))
    # A transaction really is date-scoped, so its key keeps the date.
    assert state.unseen(channel_key("transaction:99", 111))

    reloaded = AnnouncementState(str(path))
    reloaded.load()
    assert not reloaded.unseen(channel_key("substitution:824960:7:1:681297", 111))


def test_adopting_undated_keys_leaves_current_keys_alone(tmp_path) -> None:
    from orioles_bot.state import AnnouncementState, channel_key

    path = tmp_path / "state.json"
    path.write_text(
        '{"announced": ["substitution:824960:2:1:683002@111"]}', encoding="utf-8"
    )

    state = AnnouncementState(str(path))
    state.load()
    state.adopt_undated_keys()

    assert not state.unseen(channel_key("substitution:824960:2:1:683002", 111))


def test_an_eight_digit_game_id_is_not_mistaken_for_a_date() -> None:
    """`fromisoformat` also accepts YYYYMMDD, which a game id could look like."""
    from orioles_bot.state import undated_key

    assert undated_key("substitution:20260828:2:1:683002") is None
    assert undated_key("lineup:824960:1:2,3") is None
    assert undated_key("substitution:2026-08-28:824960:2:1:683002") == (
        "substitution:824960:2:1:683002"
    )


def test_webhook_id_never_exposes_the_token() -> None:
    from orioles_bot.bot import webhook_id, webhook_label

    url = "https://discord.com/api/webhooks/12345678901234567/super-secret-tokenxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

    assert webhook_id(url) == "12345678901234567"
    assert webhook_label(url) == "webhook 12345678901234567"
    assert "super-secret-token" not in webhook_label(url)


def test_webhook_state_key_excludes_the_token(tmp_path) -> None:
    from orioles_bot.bot import webhook_id
    from orioles_bot.state import AnnouncementState, channel_key

    url = "https://discord.com/api/webhooks/12345678901234567/super-secret-tokenxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    path = tmp_path / "state.json"
    state = AnnouncementState(str(path))
    state.load()
    state.mark(channel_key("transaction:1", f"webhook:{webhook_id(url)}"))

    assert "super-secret-token" not in path.read_text(encoding="utf-8")
    assert not state.unseen(channel_key("transaction:1", "webhook:12345678901234567"))
    assert state.unseen(channel_key("transaction:1", "webhook:999"))


def _txn(tid: str, player_id, players, name="Coby Mayo"):
    from datetime import date

    from orioles_bot.models import TransactionInfo

    return TransactionInfo(
        transaction_id=tid,
        date=date(2026, 8, 6),
        player_id=player_id,
        player_name=name,
        type_description="Recalled",
        description=f"Orioles recalled {name}.",
        headshot_url=None if player_id is None else f"thumb-{player_id}",
        players=players,
    )


def test_single_player_transaction_keeps_a_thumbnail() -> None:
    """A full-width headshot takes up most of a phone screen on its own."""
    from orioles_bot.embeds import transaction_embeds
    from orioles_bot.models import TransactionPlayer

    payload = transaction_embeds(
        [_txn("1", 683002, (TransactionPlayer(683002, "Coby Mayo"),))],
        _txn("1", 683002, ()).date,
    )[0].to_dict()

    assert "image" not in payload
    assert payload["thumbnail"]["url"] == "thumb-683002"


def test_multi_player_transaction_keeps_a_thumbnail() -> None:
    """One face would misrepresent a trade involving several players."""
    from orioles_bot.embeds import transaction_embeds
    from orioles_bot.models import TransactionPlayer

    payload = transaction_embeds(
        [
            _txn(
                "1",
                1,
                (TransactionPlayer(1, "A Player"), TransactionPlayer(2, "B Player")),
            )
        ],
        _txn("1", 1, ()).date,
    )[0].to_dict()

    assert "image" not in payload
    assert payload["thumbnail"]["url"] == "thumb-1"


def test_transaction_digest_keeps_a_thumbnail() -> None:
    from orioles_bot.embeds import transaction_embeds
    from orioles_bot.models import TransactionPlayer

    payload = transaction_embeds(
        [
            _txn("1", 1, (TransactionPlayer(1, "A"),)),
            _txn("2", 2, (TransactionPlayer(2, "B"),)),
        ],
        _txn("1", 1, ()).date,
    )[0].to_dict()

    assert "image" not in payload
    assert payload["thumbnail"]["url"] == "thumb-1"


def test_transaction_without_a_player_id_has_no_image() -> None:
    from orioles_bot.embeds import transaction_embeds

    payload = transaction_embeds([_txn("1", None, ())], _txn("1", 1, ()).date)[
        0
    ].to_dict()

    assert "image" not in payload
    assert "thumbnail" not in payload
