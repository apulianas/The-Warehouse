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
    calculate_matchup_annotation,
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
