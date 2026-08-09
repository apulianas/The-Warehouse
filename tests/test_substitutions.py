from __future__ import annotations

import asyncio
from datetime import date

from orioles_bot.bot import lineup_announcement_key, substitution_announcement_key
from orioles_bot.embeds import substitution_embeds
from orioles_bot.formatting import (
    format_matchup_history,
    format_platoon_split,
    format_running_profile,
    format_substitution_headline,
    format_substitution_pitcher,
)
from orioles_bot.matchups import MatchupService, calculate_matchup_history
from orioles_bot.mlb import (
    MlbApiError,
    _entry_position,
    headshot_url,
    parse_game,
    parse_handedness,
    parse_platoon_splits,
    parse_running_stats,
)
from orioles_bot.models import (
    HittingSplit,
    LineupPlayer,
    MatchupHistory,
    PitcherInfo,
    RunningProfile,
    SUBSTITUTION_ROLE_FIELDER,
    SUBSTITUTION_ROLE_HITTER,
    SUBSTITUTION_ROLE_RUNNER,
    SUBSTITUTION_ROLE_UNKNOWN,
    Substitution,
)
from orioles_bot.running import SprintSpeedService


ORIOLES_ID = 110
OPPONENT_ID = 140


def _boxscore_player(
    player_id: int, name: str, batting_order: str | None, position: str
) -> dict[str, object]:
    player: dict[str, object] = {
        "person": {"id": player_id, "fullName": name},
        "position": {"abbreviation": position},
    }
    if batting_order is not None:
        player["battingOrder"] = batting_order
    return player


def _boxscore(
    *,
    orioles_subs: bool = False,
    opponent_subs: bool = False,
) -> dict[str, object]:
    """A two-team boxscore, optionally with a pinch hitter on either side."""
    away_players: dict[str, object] = {}
    away_order: list[int] = []
    for slot in range(1, 10):
        player_id = 1000 + slot
        away_players[f"ID{player_id}"] = _boxscore_player(
            player_id, f"Oriole {slot}", f"{slot}00", "LF"
        )
        away_order.append(player_id)
    if orioles_subs:
        away_players["ID1900"] = _boxscore_player(1900, "Pinch Oriole", "901", "PH")
        away_order[8] = 1900

    home_players: dict[str, object] = {}
    home_order: list[int] = []
    for slot in range(1, 10):
        player_id = 2000 + slot
        home_players[f"ID{player_id}"] = _boxscore_player(
            player_id, f"Rival {slot}", f"{slot}00", "RF"
        )
        home_order.append(player_id)
    if opponent_subs:
        home_players["ID2900"] = _boxscore_player(2900, "Pinch Rival", "401", "PH")
        home_order[3] = 2900

    away_players["ID1500"] = _boxscore_player(1500, "Orioles Starter", None, "P")
    away_players["ID1501"] = _boxscore_player(1501, "Orioles Reliever", None, "P")
    home_players["ID2500"] = _boxscore_player(2500, "Rival Starter", None, "P")
    home_players["ID2501"] = _boxscore_player(2501, "Rival Reliever", None, "P")

    return {
        "teams": {
            "away": {
                "battingOrder": away_order,
                "players": away_players,
                "pitchers": [1500, 1501],
            },
            "home": {
                "battingOrder": home_order,
                "players": home_players,
                "pitchers": [2500, 2501],
            },
        }
    }


def _raw_game(status: str = "In Progress") -> dict[str, object]:
    return {
        "gamePk": 777,
        "gameDate": "2026-08-07T23:05:00Z",
        "status": {"detailedState": status},
        "venue": {"name": "Camden Yards"},
        "teams": {
            "away": {"team": {"id": ORIOLES_ID, "name": "Baltimore Orioles"}},
            "home": {"team": {"id": OPPONENT_ID, "name": "Rival Club"}},
        },
    }


def test_lineup_key_is_unchanged_by_a_substitution() -> None:
    """The bug: a pinch hitter used to mint a new key and repost the lineup."""
    target_date = date(2026, 8, 7)
    before = parse_game(_raw_game(), _boxscore())
    after = parse_game(_raw_game(), _boxscore(orioles_subs=True))

    assert after.lineup[8].player_id == 1900
    assert after.lineup[8].is_substitute
    assert lineup_announcement_key(target_date, before) == lineup_announcement_key(
        target_date, after
    )


def test_lineup_key_still_changes_when_the_starters_change() -> None:
    """A pre-game scratch is a real lineup correction and should repost."""
    target_date = date(2026, 8, 7)
    original = parse_game(_raw_game("Scheduled"), _boxscore())

    scratched = _boxscore()
    teams = scratched["teams"]["away"]
    teams["players"]["ID1009"] = _boxscore_player(1009, "Scratched", None, "LF")
    teams["players"]["ID1010"] = _boxscore_player(1010, "Replacement", "900", "LF")
    teams["battingOrder"][8] = 1010
    replaced = parse_game(_raw_game("Scheduled"), scratched)

    assert lineup_announcement_key(target_date, original) != lineup_announcement_key(
        target_date, replaced
    )


def test_substitution_is_paired_with_the_player_replaced() -> None:
    game = parse_game(_raw_game(), _boxscore(orioles_subs=True))

    assert len(game.substitutions) == 1
    substitution = game.substitutions[0]
    assert substitution.batter.name == "Pinch Oriole"
    assert substitution.replaced is not None
    assert substitution.replaced.name == "Oriole 9"
    assert substitution.slot == 9
    assert substitution.is_orioles
    assert substitution.batting_team == "Baltimore Orioles"


def test_substitution_faces_the_current_pitcher_not_the_starter() -> None:
    game = parse_game(_raw_game(), _boxscore(orioles_subs=True))

    assert game.pitcher is not None and game.pitcher.name == "Orioles Starter"
    assert game.substitutions[0].pitcher is not None
    assert game.substitutions[0].pitcher.name == "Rival Reliever"


def test_opponent_substitution_faces_the_orioles_pitcher() -> None:
    game = parse_game(_raw_game(), _boxscore(opponent_subs=True))

    assert len(game.substitutions) == 1
    substitution = game.substitutions[0]
    assert not substitution.is_orioles
    assert substitution.batting_team == "Rival Club"
    assert substitution.batter.name == "Pinch Rival"
    assert substitution.pitcher is not None
    assert substitution.pitcher.name == "Orioles Reliever"


def test_both_teams_substitutions_are_reported() -> None:
    game = parse_game(_raw_game(), _boxscore(orioles_subs=True, opponent_subs=True))

    assert {sub.batter.name for sub in game.substitutions} == {
        "Pinch Oriole",
        "Pinch Rival",
    }


def test_substitutions_are_ignored_before_first_pitch() -> None:
    game = parse_game(_raw_game("Scheduled"), _boxscore())

    assert not game.has_started
    assert parse_game(_raw_game("In Progress"), _boxscore()).has_started


def test_substitution_key_is_stable_across_polls() -> None:
    target_date = date(2026, 8, 7)
    first = parse_game(_raw_game(), _boxscore(orioles_subs=True))
    second = parse_game(_raw_game(), _boxscore(orioles_subs=True))

    assert substitution_announcement_key(
        target_date, first.substitutions[0]
    ) == substitution_announcement_key(target_date, second.substitutions[0])


def test_a_second_substitution_in_one_slot_replaces_the_first_sub() -> None:
    boxscore = _boxscore(orioles_subs=True)
    away = boxscore["teams"]["away"]
    away["players"]["ID1901"] = _boxscore_player(1901, "Second Sub", "902", "PH")
    away["battingOrder"][8] = 1901

    game = parse_game(_raw_game(), boxscore)
    latest = [sub for sub in game.substitutions if sub.batter.player_id == 1901]

    assert len(latest) == 1
    assert latest[0].replaced is not None
    assert latest[0].replaced.name == "Pinch Oriole"


def test_matchup_history_totals_a_full_line() -> None:
    records = [
        {"events": "single", "woba_value": 0.9, "woba_denom": 1},
        {"events": "home_run", "woba_value": 2.0, "woba_denom": 1},
        {"events": "strikeout", "woba_value": 0.0, "woba_denom": 1},
        {"events": "walk", "woba_value": 0.7, "woba_denom": 1},
        {"events": "field_out", "woba_value": 0.0, "woba_denom": 1},
    ]

    history = calculate_matchup_history(records)

    assert history.plate_appearances == 5
    assert history.at_bats == 4
    assert history.hits == 2
    assert history.home_runs == 1
    assert history.walks == 1
    assert history.strikeouts == 1
    assert history.average == 0.5
    assert history.slugging_percentage == 1.25


def test_matchup_service_exposes_history_and_annotation_from_one_fetch() -> None:
    calls = 0

    def fetcher(batter_id: int, pitcher_id: int) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        return [
            {"events": "single", "woba_value": 0.9, "woba_denom": 1}
            for _ in range(6)
        ]

    async def run() -> None:
        service = MatchupService(min_pa=5, fetcher=fetcher)
        history = await service.history(101, 201)
        annotations = await service.fetch_many([(101, 201)])
        assert history is not None
        assert history.plate_appearances == 6
        assert annotations[(101, 201)].emoji == "🔥"

    asyncio.run(run())
    assert calls == 1


def test_format_matchup_history_reads_like_a_box_score() -> None:
    pitcher = PitcherInfo(player_id=9, name="Ace Reliever", throws="L")
    history = MatchupHistory(
        plate_appearances=9,
        at_bats=8,
        hits=3,
        doubles=1,
        home_runs=1,
        walks=1,
        strikeouts=2,
        average=0.375,
        slugging_percentage=0.875,
    )

    text = format_matchup_history(history, pitcher)

    assert "3-for-8" in text
    assert "1 HR" in text
    assert ".375 AVG" in text
    assert "9 PA" in text


def test_format_matchup_history_handles_a_first_meeting() -> None:
    pitcher = PitcherInfo(player_id=9, name="Ace Reliever", throws="L")

    assert "No prior plate appearances" in format_matchup_history(
        MatchupHistory(), pitcher
    )
    # A failed lookup is not the same claim as an empty one.
    assert "unavailable" in format_matchup_history(None, pitcher)


def test_format_platoon_split_shows_the_slash_line() -> None:
    split = HittingSplit(
        plate_appearances=25,
        home_runs=3,
        walks=3,
        strikeouts=8,
        average=0.381,
        on_base_percentage=0.458,
        slugging_percentage=0.857,
        ops=1.315,
    )

    text = format_platoon_split(split, "L")

    assert ".381/.458/.857" in text
    assert "3 HR" in text
    assert "25 PA" in text


def test_format_platoon_split_handles_an_empty_sample() -> None:
    assert "No plate appearances against RHP" in format_platoon_split(
        HittingSplit(), "R"
    )
    # A failed lookup is not the same claim as an empty one.
    assert "unavailable" in format_platoon_split(None, "L")


def test_substitution_headline_names_both_players_and_the_slot() -> None:
    substitution = Substitution(
        game_pk=777,
        slot=3,
        batter=LineupPlayer(
            1900, "Pinch Oriole", "PH", 3, headshot_url(1900), 1, bat_side="L"
        ),
        replaced=LineupPlayer(1003, "Oriole 3", "LF", 3, headshot_url(1003)),
        pitcher=PitcherInfo(player_id=9, name="Ace Reliever", throws="R"),
        is_orioles=True,
        batting_team="Baltimore Orioles",
        batting_team_id=ORIOLES_ID,
    )

    headline = format_substitution_headline(substitution)

    assert "Pinch Oriole" in headline
    assert "Oriole 3" in headline
    assert "batting 3rd" in headline
    assert "(L)" in headline
    assert "(RHP)" in format_substitution_pitcher(substitution.pitcher)


def test_substitution_embed_carries_both_matchup_views() -> None:
    substitution = Substitution(
        game_pk=777,
        slot=9,
        batter=LineupPlayer(
            1900, "Pinch Oriole", "PH", 9, headshot_url(1900), 1, bat_side="R"
        ),
        replaced=LineupPlayer(1009, "Oriole 9", "C", 9, headshot_url(1009)),
        pitcher=PitcherInfo(player_id=9, name="Ace Reliever", throws="L"),
        is_orioles=True,
        batting_team="Baltimore Orioles",
        batting_team_id=ORIOLES_ID,
    )
    histories = {(1900, 9): MatchupHistory(plate_appearances=4, at_bats=4, hits=2, average=0.5)}
    splits = {1900: HittingSplit(plate_appearances=25, average=0.381, ops=1.315)}

    embeds = substitution_embeds([substitution], histories, splits)

    assert len(embeds) == 1
    embed = embeds[0]
    assert "substitution" in (embed.title or "")
    names = [field.name for field in embed.fields]
    assert "Career vs Ace Reliever" in names
    assert "This season vs LHP" in names
    assert embed.thumbnail.url == headshot_url(1900)


def test_substitution_embed_survives_missing_stats() -> None:
    substitution = Substitution(
        game_pk=777,
        slot=9,
        batter=LineupPlayer(1900, "Pinch Oriole", "PH", 9, headshot_url(1900), 1),
        replaced=None,
        pitcher=None,
        is_orioles=False,
        batting_team="Rival Club",
        batting_team_id=OPPONENT_ID,
    )

    embeds = substitution_embeds([substitution])

    assert len(embeds) == 1
    assert "enters" in (embeds[0].description or "")


def test_parse_handedness_reads_bat_side_and_pitch_hand() -> None:
    data = {
        "people": [
            {"id": 1, "batSide": {"code": "L"}, "pitchHand": {"code": "R"}},
            {"id": 2, "batSide": {"code": "S"}},
        ]
    }

    hands = parse_handedness(data)

    assert hands[1] == ("L", "R")
    assert hands[2] == ("S", None)


def test_parse_platoon_splits_keys_by_situation_code() -> None:
    data = {
        "stats": [
            {
                "splits": [
                    {
                        "split": {"code": "vl", "description": "vs Left"},
                        "stat": {"plateAppearances": 25, "avg": ".381", "ops": "1.315"},
                    },
                    {
                        "split": {"code": "vr", "description": "vs Right"},
                        "stat": {"plateAppearances": 30, "avg": ".143"},
                    },
                ]
            }
        ]
    }

    splits = parse_platoon_splits(data)

    assert set(splits) == {"vl", "vr"}
    assert splits["vl"].plate_appearances == 25
    assert splits["vr"].average == 0.143


class _Recorder:
    """Stands in for a Discord channel, capturing what would be posted."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, list[object]]] = []

    async def send(self, content: str, embeds: list[object]) -> None:
        self.posts.append((content, list(embeds)))


class _StubMlb:
    async def fetch_handedness(
        self, player_ids: list[int]
    ) -> dict[int, tuple[str | None, str | None]]:
        return {player_id: ("R", "L") for player_id in player_ids}

    async def fetch_platoon_splits(
        self, player_id: int, season: int
    ) -> dict[str, HittingSplit]:
        return {"vl": HittingSplit(plate_appearances=25, average=0.381, ops=1.315)}


def _bot(tmp_path):
    from orioles_bot.bot import OriolesBot
    from orioles_bot.config import BotConfig
    from zoneinfo import ZoneInfo

    config = BotConfig(
        discord_token="token",
        discord_channel_ids=(123,),
        discord_webhook_urls=(),
        poll_interval_seconds=300,
        matchup_min_pa=5,
        time_zone=ZoneInfo("UTC"),
        state_file=str(tmp_path / "state.json"),
    )
    bot = OriolesBot(config)
    bot.mlb = _StubMlb()
    bot.matchups = MatchupService(min_pa=5, fetcher=lambda batter, pitcher: [])
    return bot


def test_a_full_game_posts_one_lineup_and_one_card_per_substitution(tmp_path) -> None:
    """The whole point: three subs must not mean four lineup posts."""
    from orioles_bot.bot import _AnnouncementTarget

    bot = _bot(tmp_path)
    recorder = _Recorder()
    targets = [_AnnouncementTarget("123", "channel 123", recorder)]
    target_date = date(2026, 8, 7)

    async def poll(boxscore: dict[str, object], status: str) -> None:
        game = parse_game(_raw_game(status), boxscore)
        await bot._announce_lineup(game, targets, target_date)
        await bot._announce_substitutions(game, targets, target_date)

    async def run() -> None:
        # Lineup card posted pre-game, then polled again before first pitch.
        await poll(_boxscore(), "Scheduled")
        await poll(_boxscore(), "Scheduled")
        # Game starts, then a pinch hitter, then an opponent pinch hitter.
        await poll(_boxscore(), "In Progress")
        await poll(_boxscore(orioles_subs=True), "In Progress")
        await poll(_boxscore(orioles_subs=True), "In Progress")
        await poll(_boxscore(orioles_subs=True, opponent_subs=True), "In Progress")
        await poll(_boxscore(orioles_subs=True, opponent_subs=True), "Final")

    asyncio.run(run())

    lineups = [post for post in recorder.posts if "lineup" in post[0]]
    substitutions = [post for post in recorder.posts if "substitution" in post[0]]

    assert len(lineups) == 1
    assert len(substitutions) == 2
    assert all(len(embeds) == 1 for _, embeds in substitutions)


def test_substitutions_before_first_pitch_do_not_post_a_card(tmp_path) -> None:
    from orioles_bot.bot import _AnnouncementTarget

    bot = _bot(tmp_path)
    recorder = _Recorder()
    targets = [_AnnouncementTarget("123", "channel 123", recorder)]

    async def run() -> None:
        game = parse_game(_raw_game("Scheduled"), _boxscore(orioles_subs=True))
        await bot._announce_substitutions(game, targets, date(2026, 8, 7))

    asyncio.run(run())

    assert recorder.posts == []


def _lineup_player_with_entry(entry: str | None) -> LineupPlayer:
    return LineupPlayer(
        1900,
        "Sub Oriole",
        "LF",
        9,
        headshot_url(1900),
        1,
        bat_side="R",
        entry_position=entry,
    )


def _sub(batter: LineupPlayer, pitcher: PitcherInfo | None = None) -> Substitution:
    return Substitution(
        game_pk=777,
        slot=9,
        batter=batter,
        replaced=LineupPlayer(1009, "Oriole 9", "C", 9, headshot_url(1009)),
        pitcher=pitcher,
        is_orioles=True,
        batting_team="Baltimore Orioles",
        batting_team_id=ORIOLES_ID,
    )


_PITCHER = PitcherInfo(player_id=9, name="Ace Reliever", throws="L")


def test_entry_position_distinguishes_pinch_runner_from_pinch_hitter() -> None:
    """MLB records the entry role as the first position a player occupies."""
    assert _sub(_lineup_player_with_entry("PR")).role == SUBSTITUTION_ROLE_RUNNER
    assert _sub(_lineup_player_with_entry("PH")).role == SUBSTITUTION_ROLE_HITTER
    assert _sub(_lineup_player_with_entry("1B")).role == SUBSTITUTION_ROLE_FIELDER
    assert _sub(_lineup_player_with_entry("PR")).is_pinch_runner
    assert not _sub(_lineup_player_with_entry("PH")).is_pinch_runner


def test_entry_position_without_a_recorded_position_stays_generic() -> None:
    """Guessing a role would state something the boxscore has not said."""
    unknown = _sub(_lineup_player_with_entry(None))

    assert unknown.role == SUBSTITUTION_ROLE_UNKNOWN
    assert not unknown.is_pinch_runner
    assert not unknown.is_defensive_substitution


def test_parse_entry_position_reads_the_first_of_all_positions() -> None:
    """A pinch runner who later takes the field still entered as a runner."""
    player = {
        "person": {"id": 5, "fullName": "Speedy Sub"},
        "position": {"abbreviation": "LF"},
        "battingOrder": "501",
        "allPositions": [{"abbreviation": "PR"}, {"abbreviation": "LF"}],
    }

    assert _entry_position(player) == "PR"


def test_parse_entry_position_tolerates_missing_or_malformed_positions() -> None:
    assert _entry_position({"person": {"id": 5}}) is None
    assert _entry_position({"allPositions": []}) is None
    assert _entry_position({"allPositions": "PR"}) is None
    assert _entry_position({"allPositions": [{}]}) is None
    assert _entry_position(None) is None


def test_pinch_runner_card_shows_baserunning_not_a_matchup() -> None:
    """The bug this fixes: a pinch runner was given a hitting card."""
    substitution = _sub(_lineup_player_with_entry("PR"), pitcher=_PITCHER)
    profile = RunningProfile(
        stolen_bases=5,
        caught_stealing=4,
        stolen_base_percentage=0.556,
        sprint_speed=28.5,
        bolts=3,
    )

    embed = substitution_embeds([substitution], {}, {}, {1900: profile})[0]

    assert [field.name for field in embed.fields] == ["Baserunning"]
    assert (embed.title or "").startswith("🏃")
    value = embed.fields[0].value or ""
    assert "5-for-9 stealing" in value
    assert "28.5 ft/s" in value
    assert "3 bolts" in value
    assert "pinch runner" in (embed.description or "")
    # He is not hitting, so the pitcher is context rather than a matchup.
    assert "On the mound" in (embed.description or "")


def test_pinch_hitter_and_defensive_sub_keep_the_hitting_card() -> None:
    for entry in ("PH", "1B"):
        substitution = _sub(_lineup_player_with_entry(entry), pitcher=_PITCHER)

        embed = substitution_embeds([substitution], {}, {}, {})[0]

        assert [field.name for field in embed.fields] == [
            "Career vs Ace Reliever",
            "This season vs LHP",
        ]
        assert (embed.title or "").startswith("🔄")
        assert "Facing" in (embed.description or "")


def test_defensive_substitution_headline_names_the_position() -> None:
    headline = format_substitution_headline(_sub(_lineup_player_with_entry("1B")))

    assert "defensive substitution at LF" in headline


def test_pinch_runner_headline_omits_bat_side() -> None:
    """Which side he hits from is noise for a player who is not batting."""
    headline = format_substitution_headline(_sub(_lineup_player_with_entry("PR")))

    assert "(R)" not in headline
    assert "(R)" in format_substitution_headline(_sub(_lineup_player_with_entry("PH")))


def test_running_profile_renders_each_half_independently() -> None:
    """Statcast rates only frequent runners, so speed can be missing alone."""
    steals_only = RunningProfile(stolen_bases=3, caught_stealing=1)

    assert format_running_profile(steals_only) == "3-for-4 stealing"
    assert "29.1 ft/s sprint speed" in format_running_profile(
        RunningProfile(sprint_speed=29.1)
    )


def test_running_profile_separates_no_attempts_from_no_data() -> None:
    """A callup with no stat line is not the same as one who has not run."""
    no_attempts = RunningProfile(stolen_bases=0, caught_stealing=0, sprint_speed=27.0)

    assert "No stolen base attempts this season" in format_running_profile(no_attempts)
    assert format_running_profile(RunningProfile()) == (
        "Baserunning data is unavailable right now."
    )
    assert format_running_profile(None) == "Baserunning data is unavailable right now."


def test_parse_running_stats_reads_steals_from_the_hitting_group() -> None:
    """MLB returns nothing for group=running, so steals come from hitting."""
    data = {
        "stats": [
            {
                "splits": [
                    {
                        "stat": {
                            "stolenBases": 12,
                            "caughtStealing": 3,
                            "stolenBasePercentage": ".800",
                        }
                    }
                ]
            }
        ]
    }

    profile = parse_running_stats(data)

    assert profile.stolen_bases == 12
    assert profile.caught_stealing == 3
    assert profile.attempts == 15
    assert profile.has_steal_line


def test_parse_running_stats_without_a_stat_line_is_empty_not_zero() -> None:
    """A September callup returns no groups at all, which is not a zero."""
    assert parse_running_stats({"stats": []}).is_empty
    assert not parse_running_stats({"stats": []}).has_steal_line
    assert parse_running_stats({}).is_empty


def test_sprint_speed_leaderboard_is_fetched_once_for_all_runners() -> None:
    """It is one league wide document, so per player fetches would be waste."""
    calls: list[int] = []

    def fetcher(season: int) -> list[dict[str, str]]:
        calls.append(season)
        return [
            {
                "player_id": "696030",
                "sprint_speed": "28.5",
                "bolts": "3",
                "hp_to_1b": "4.27",
            },
            {
                "player_id": "681047",
                "sprint_speed": "27.1",
                "bolts": "0",
                "hp_to_1b": "",
            },
        ]

    service = SprintSpeedService(fetcher)

    async def run() -> tuple[object, object, object]:
        return (
            await service.for_player(696030, 2025),
            await service.for_player(681047, 2025),
            await service.for_player(1, 2025),
        )

    first, second, missing = asyncio.run(run())

    assert calls == [2025]
    assert first == {"sprint_speed": 28.5, "bolts": 3, "home_to_first": 4.27}
    assert second == {"sprint_speed": 27.1, "bolts": 0, "home_to_first": None}
    # Statcast simply does not rate a runner with too few tracked runs.
    assert missing is None


def test_sprint_speed_failure_degrades_instead_of_raising() -> None:
    """Speed is a nice to have; losing it must not drop the whole card."""

    def fetcher(season: int) -> list[dict[str, str]]:
        raise RuntimeError("savant is down")

    service = SprintSpeedService(fetcher)

    assert asyncio.run(service.for_player(696030, 2025)) is None


def test_parse_entry_position_translates_numeric_position_codes() -> None:
    """MLB position codes are numeric, so PH/PR never match them directly."""
    assert _entry_position({"allPositions": [{"code": "12"}]}) == "PR"
    assert _entry_position({"allPositions": [{"code": "11"}]}) == "PH"
    # A fielding code is not a pinch role, so it must not masquerade as one.
    assert _entry_position({"allPositions": [{"code": "4"}]}) is None


def test_empty_sprint_speed_leaderboard_is_not_cached() -> None:
    """Statcast publishes nothing early in a season; that must be retried."""
    responses: list[list[dict[str, str]]] = [
        [],
        [{"player_id": "1900", "sprint_speed": "28.0", "bolts": "1"}],
    ]
    calls: list[int] = []

    def fetcher(season: int) -> list[dict[str, str]]:
        calls.append(season)
        return responses[len(calls) - 1]

    service = SprintSpeedService(fetcher)

    async def run() -> tuple[object, object]:
        return (
            await service.for_player(1900, 2026),
            await service.for_player(1900, 2026),
        )

    first, second = asyncio.run(run())

    assert first is None
    assert second == {"sprint_speed": 28.0, "bolts": 1, "home_to_first": None}
    assert calls == [2026, 2026]


def test_running_profile_says_so_when_only_the_steal_lookup_failed() -> None:
    """Omitting the steal half would read as "he has never run"."""
    speed_only = RunningProfile(sprint_speed=29.3, bolts=5)

    rendered = format_running_profile(speed_only)

    assert "Stolen base record unavailable" in rendered
    assert "29.3 ft/s" in rendered
    assert "No stolen base attempts" not in rendered


def test_running_profile_keeps_speed_when_the_steal_lookup_fails(tmp_path) -> None:
    """One source failing should not suppress the other."""
    bot = _bot(tmp_path)

    class _FailingMlb:
        async def fetch_running_stats(self, player_id: int, season: int):
            raise MlbApiError("stats api is down")

    bot.mlb = _FailingMlb()
    bot.sprint_speed = SprintSpeedService(
        lambda season: [{"player_id": "1900", "sprint_speed": "29.3", "bolts": "5"}]
    )

    profile = asyncio.run(bot._running_profile(1900, "Sub Oriole", 2025))

    assert profile is not None
    assert profile.sprint_speed == 29.3
    assert not profile.has_steal_line
    assert "29.3 ft/s" in format_running_profile(profile)


def test_running_profile_is_none_when_both_sources_fail(tmp_path) -> None:
    bot = _bot(tmp_path)

    class _FailingMlb:
        async def fetch_running_stats(self, player_id: int, season: int):
            raise MlbApiError("stats api is down")

    bot.mlb = _FailingMlb()
    bot.sprint_speed = SprintSpeedService(_raise_savant)

    assert asyncio.run(bot._running_profile(1900, "Sub Oriole", 2025)) is None


def _raise_savant(season: int) -> list[dict[str, str]]:
    raise RuntimeError("savant is down")
