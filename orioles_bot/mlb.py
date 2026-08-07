from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import aiohttp

from .models import (
    AL_EAST_DIVISION_ID,
    AMERICAN_LEAGUE_ID,
    ORIOLES_TEAM_ID,
    DivisionStandings,
    GameInfo,
    HittingSplit,
    LineupPlayer,
    NextGame,
    PitcherInfo,
    PitchingGame,
    PitchingSplit,
    PlayerRef,
    ScheduleWindow,
    StatsWindow,
    TeamRecord,
    TransactionInfo,
    TransactionPlayer,
    WildCardStandings,
)


BASE_URL = "https://statsapi.mlb.com/api/v1"
# d_... is a Cloudinary default: players without a photo (minor leaguers, new
# signings) return MLB's generic silhouette instead of a 404, which Discord
# would otherwise render as a broken image.
HEADSHOT_URL_TEMPLATE = (
    "https://img.mlbstatic.com/mlb-photos/image/upload/"
    "d_people:generic:headshot:67:current.png,w_{width},q_auto:good"
    "/v1/people/{player_id}/headshot/67/current"
)
HEADSHOT_THUMBNAIL_WIDTH = 180
HEADSHOT_FEATURE_WIDTH = 426
BASEBALL_SAVANT_PLAYER_URL = "https://baseballsavant.mlb.com/savant-player"
BASEBALL_SAVANT_PREVIEW_URL = "https://baseballsavant.mlb.com/preview"
BASEBALL_SAVANT_PLAYER_MATCHUP_URL = "https://baseballsavant.mlb.com/player_matchup"
TEAM_LOGO_URL_TEMPLATE = "https://midfield.mlbstatic.com/v1/team/{team_id}/spots/240"
PLAYER_SEARCH_LIMIT = 25
NEXT_GAME_LOOKAHEAD_DAYS = 10
# A game in one of these states is behind the team, so it is never "next". An
# in-progress game still counts, since that is what the team is playing now.
FINISHED_GAME_STATES = {"final", "game over", "completed early", "completed"}
UNPLAYED_GAME_STATES = {"postponed", "cancelled", "canceled", "suspended"}


class MlbApiError(RuntimeError):
    """Raised when the public MLB Stats API cannot satisfy a request."""


def build_mlb_url(path: str, params: dict[str, Any] | None = None) -> str:
    normalized = path if path.startswith("/") else f"/{path}"
    if not params:
        return f"{BASE_URL}{normalized}"

    clean_params = {
        key: value
        for key, value in sorted(params.items())
        if value is not None and value != ""
    }
    return f"{BASE_URL}{normalized}?{urlencode(clean_params, doseq=True)}"


def headshot_url(
    player_id: int | str, width: int = HEADSHOT_THUMBNAIL_WIDTH
) -> str:
    return HEADSHOT_URL_TEMPLATE.format(player_id=player_id, width=width)


def savant_matchup_params(batter_id: int | str, pitcher_id: int | str) -> str:
    """Filters for the Statcast CSV endpoint.

    These params only work on ``/statcast_search/csv``. The HTML search page
    ignores them for form pre-selection, so matchup results are fetched and
    rendered inline instead of linked.
    """
    return urlencode(
        {
            "all": "true",
            "batters_lookup[]": str(batter_id),
            "pitchers_lookup[]": str(pitcher_id),
            "hfGT": "R|",
            "type": "details",
        }
    )


def savant_player_url(player_id: int | str) -> str:
    return f"{BASEBALL_SAVANT_PLAYER_URL}/{player_id}"


def savant_preview_url(game_pk: int | str) -> str:
    """Statcast game preview: probables, lineups, and matchup splits."""
    return f"{BASEBALL_SAVANT_PREVIEW_URL}?{urlencode({'game_pk': str(game_pk)})}"


def savant_team_matchup_url(
    batting_team_id: int | str,
    pitching_team_id: int | str,
    pitcher_id: int | str,
) -> str:
    """Savant "Team Batters vs Individual Pitcher" page.

    Unlike the Statcast search page, this one renders server side, so the
    whole batting order's history against the opposing starter is visible
    immediately. All four params are required; omitting a team renders an
    empty form.
    """
    params = urlencode(
        {
            "type": "batter",
            "teamPitching": str(pitching_team_id),
            "teamBatting": str(batting_team_id),
            "player_id": str(pitcher_id),
        }
    )
    return f"{BASEBALL_SAVANT_PLAYER_MATCHUP_URL}?{params}"


def team_logo_url(team_id: int | str) -> str:
    return TEAM_LOGO_URL_TEMPLATE.format(team_id=team_id)


class MlbClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = build_mlb_url(path, params)
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status >= 400:
                    body = await response.text()
                    raise MlbApiError(
                        f"MLB Stats API returned HTTP {response.status}: {body[:200]}"
                    )
                data = await response.json()
        except TimeoutError as exc:
            raise MlbApiError("MLB Stats API request timed out") from exc
        except aiohttp.ClientError as exc:
            raise MlbApiError(f"MLB Stats API request failed: {exc}") from exc

        if not isinstance(data, dict):
            raise MlbApiError("MLB Stats API returned an unexpected response")
        return data

    async def fetch_games(self, target_date: date) -> list[GameInfo]:
        schedule = await self._get_json(
            "/schedule",
            {
                "sportId": 1,
                "teamId": ORIOLES_TEAM_ID,
                "date": target_date.isoformat(),
                "hydrate": "probablePitcher,team,linescore,flags",
            },
        )

        raw_games: list[dict[str, Any]] = []
        for day in schedule.get("dates", []):
            raw_games.extend(day.get("games", []))

        games: list[GameInfo] = []
        for raw_game in raw_games:
            boxscore: dict[str, Any] | None = None
            game_pk = _safe_int(raw_game.get("gamePk"))
            if game_pk is not None:
                try:
                    boxscore = await self._get_json(f"/game/{game_pk}/boxscore")
                except MlbApiError:
                    boxscore = None
            games.append(parse_game(raw_game, boxscore))
        return games

    async def fetch_transactions(self, target_date: date) -> list[TransactionInfo]:
        data = await self._get_json(
            "/transactions",
            {
                "teamId": ORIOLES_TEAM_ID,
                "startDate": target_date.isoformat(),
                "endDate": target_date.isoformat(),
            },
        )
        transactions = data.get("transactions", [])
        if not isinstance(transactions, list):
            return []
        return parse_transactions(transactions, target_date)

    async def fetch_roster(self) -> tuple[PlayerRef, ...]:
        data = await self._get_json(
            f"/teams/{ORIOLES_TEAM_ID}/roster", {"rosterType": "active"}
        )
        return parse_roster(data)

    async def search_players(self, query: str) -> tuple[PlayerRef, ...]:
        """League-wide name search, used when a name is not on the Orioles roster."""
        cleaned = query.strip()
        if not cleaned:
            return ()
        data = await self._get_json(
            "/people/search", {"names": cleaned, "sportIds": 1, "limit": PLAYER_SEARCH_LIMIT}
        )
        return parse_people(data)

    async def fetch_player(self, player_id: int) -> PlayerRef | None:
        data = await self._get_json(f"/people/{player_id}")
        people = parse_people(data)
        return people[0] if people else None

    async def fetch_player_stats(
        self, player_id: int, window: StatsWindow
    ) -> tuple[HittingSplit | None, PitchingSplit | None]:
        data = await self._get_json(
            f"/people/{player_id}/stats",
            {
                "stats": "byDateRange",
                "group": "hitting,pitching",
                "startDate": window.start.isoformat(),
                "endDate": window.end.isoformat(),
                "sportId": 1,
            },
        )
        return parse_player_stats(data)

    async def fetch_player_pitching_games(
        self, player_id: int, window: StatsWindow
    ) -> tuple[PitchingGame, ...]:
        data = await self._get_json(
            f"/people/{player_id}/stats",
            {
                "stats": "gameLog",
                "group": "pitching",
                "startDate": window.start.isoformat(),
                "endDate": window.end.isoformat(),
                "sportId": 1,
            },
        )
        games = parse_pitching_game_logs(data)
        team_ids = {game.team_id for game in games if game.team_id is not None}
        if not team_ids:
            return games

        schedule = await self._get_json(
            "/schedule",
            {
                "sportId": 1,
                "teamId": min(team_ids),
                "startDate": window.start.isoformat(),
                "endDate": window.end.isoformat(),
                "hydrate": "team,linescore",
            },
        )
        scores: dict[int, tuple[int, int]] = {}
        for day in schedule.get("dates", []):
            if not isinstance(day, dict):
                continue
            for raw_game in day.get("games", []):
                if not isinstance(raw_game, dict):
                    continue
                game_pk = _safe_int(raw_game.get("gamePk"))
                linescore = raw_game.get("linescore")
                teams = linescore.get("teams") if isinstance(linescore, dict) else None
                home = teams.get("home") if isinstance(teams, dict) else None
                away = teams.get("away") if isinstance(teams, dict) else None
                if (
                    game_pk is None
                    or not isinstance(home, dict)
                    or not isinstance(away, dict)
                ):
                    continue
                raw_teams = raw_game.get("teams")
                if not isinstance(raw_teams, dict):
                    continue
                home_team = raw_teams.get("home")
                away_team = raw_teams.get("away")
                if not isinstance(home_team, dict) or not isinstance(away_team, dict):
                    continue
                home_id = _safe_int(home_team.get("team", {}).get("id"))
                away_id = _safe_int(away_team.get("team", {}).get("id"))
                home_score = _safe_int(home.get("runs"))
                away_score = _safe_int(away.get("runs"))
                if (
                    home_id is None
                    or away_id is None
                    or home_score is None
                    or away_score is None
                ):
                    continue
                scores[game_pk] = (
                    home_score if home_id in team_ids else away_score,
                    away_score if home_id in team_ids else home_score,
                )
        return tuple(
            replace(
                game,
                team_score=scores.get(game.game_pk, (None, None))[0],
                opponent_score=scores.get(game.game_pk, (None, None))[1],
            )
            for game in games
        )

    async def fetch_division_standings(
        self, division_id: int = AL_EAST_DIVISION_ID
    ) -> DivisionStandings | None:
        """Standings for one division, defaulting to the AL East.

        The season is left to the API so the current one is always used; asking
        for a specific year would go stale every January.
        """
        data = await self._get_json(
            "/standings",
            {
                "leagueId": AMERICAN_LEAGUE_ID,
                "standingsTypes": "regularSeason",
                "hydrate": "division,team",
            },
        )
        return parse_standings(data, division_id)

    async def fetch_wild_card_standings(self) -> WildCardStandings | None:
        """The full AL wild card race in one call.

        ``standingsTypes=wildCard`` returns every team that does not lead its
        division, already ranked, which is exactly the race and saves ranking
        three divisions by hand.
        """
        data = await self._get_json(
            "/standings",
            {
                "leagueId": AMERICAN_LEAGUE_ID,
                "standingsTypes": "wildCard",
                "hydrate": "division,team",
            },
        )
        return parse_wild_card_standings(data)

    async def fetch_schedule(self, window: ScheduleWindow) -> list[GameInfo]:
        """Scheduled games across a date range.

        Unlike ``fetch_games`` this skips the per-game boxscore call: a schedule
        only needs probable starters, and hydrating N days of boxscores would
        multiply request volume for data that is not rendered.
        """
        schedule = await self._get_json(
            "/schedule",
            {
                "sportId": 1,
                "teamId": ORIOLES_TEAM_ID,
                "startDate": window.start.isoformat(),
                "endDate": window.end.isoformat(),
                "hydrate": "probablePitcher,team,linescore,flags",
            },
        )
        return parse_schedule(schedule)

    async def fetch_next_games(
        self, team_ids: Sequence[int], start: date, days: int = NEXT_GAME_LOOKAHEAD_DAYS
    ) -> dict[int, NextGame]:
        """The next upcoming game for each of several teams, in one request.

        The schedule endpoint accepts a comma-separated team list, so a whole
        division costs one call rather than one per team. The window extends
        past the request date because an off day, or the All-Star break, can
        leave a team without a game for several days.
        """
        ids = [team_id for team_id in dict.fromkeys(team_ids)]
        if not ids:
            return {}
        schedule = await self._get_json(
            "/schedule",
            {
                "sportId": 1,
                "teamId": ",".join(str(team_id) for team_id in ids),
                "startDate": start.isoformat(),
                "endDate": (start + timedelta(days=max(days, 1) - 1)).isoformat(),
                "hydrate": "team",
            },
        )
        return parse_next_games(schedule, ids)


def parse_next_games(
    schedule: dict[str, Any], team_ids: Sequence[int]
) -> dict[int, NextGame]:
    """Pick each team's earliest game that has not already finished.

    A team appears in two rows of a division schedule when it plays a division
    rival, so both sides of every game are considered and the earliest surviving
    start time wins.
    """
    wanted = set(team_ids)
    dates = schedule.get("dates")
    if not isinstance(dates, list) or not wanted:
        return {}

    best: dict[int, tuple[tuple[int, float, int], NextGame]] = {}
    for day in dates:
        if not isinstance(day, dict):
            continue
        raw_games = day.get("games")
        if not isinstance(raw_games, list):
            continue
        for raw_game in raw_games:
            if not isinstance(raw_game, dict):
                continue
            if not _is_upcoming(raw_game):
                continue
            for team_id, entry in _next_game_sides(raw_game, wanted):
                sort_key = _next_game_sort_key(entry, raw_game)
                current = best.get(team_id)
                if current is None or sort_key < current[0]:
                    best[team_id] = (sort_key, entry)

    return {team_id: entry for team_id, (_, entry) in best.items()}


def _is_upcoming(raw_game: dict[str, Any]) -> bool:
    status = raw_game.get("status")
    status = status if isinstance(status, dict) else {}
    detailed = str(status.get("detailedState") or "").strip().casefold()
    abstract = str(status.get("abstractGameState") or "").strip().casefold()
    if detailed in UNPLAYED_GAME_STATES:
        return False
    if detailed in FINISHED_GAME_STATES or abstract == "final":
        return False
    return True


def _next_game_sides(
    raw_game: dict[str, Any], wanted: set[int]
) -> list[tuple[int, NextGame]]:
    teams = raw_game.get("teams")
    teams = teams if isinstance(teams, dict) else {}
    status = raw_game.get("status")
    status = status if isinstance(status, dict) else {}
    game_date = _parse_game_datetime(raw_game.get("gameDate"))

    entries: list[tuple[int, NextGame]] = []
    for side in ("home", "away"):
        other_side = "away" if side == "home" else "home"
        team = _schedule_team(teams, side)
        opponent = _schedule_team(teams, other_side)
        team_id = _safe_int(team.get("id"))
        if team_id is None or team_id not in wanted:
            continue
        entries.append(
            (
                team_id,
                NextGame(
                    team_id=team_id,
                    opponent=str(opponent.get("name") or "Opponent TBD"),
                    opponent_abbreviation=_optional_text(opponent.get("abbreviation")),
                    opponent_team_id=_safe_int(opponent.get("id")),
                    is_home=side == "home",
                    game_date=game_date,
                    status=str(status.get("detailedState") or "Scheduled"),
                ),
            )
        )
    return entries


def _schedule_team(teams: dict[str, Any], side: str) -> dict[str, Any]:
    entry = teams.get(side)
    entry = entry if isinstance(entry, dict) else {}
    team = entry.get("team")
    return team if isinstance(team, dict) else {}


def _next_game_sort_key(
    entry: NextGame, raw_game: dict[str, Any]
) -> tuple[int, float, int]:
    game_pk = _safe_int(raw_game.get("gamePk")) or 0
    if entry.game_date is None:
        return (1, 0.0, game_pk)
    game_date = entry.game_date
    if game_date.tzinfo is None:
        game_date = game_date.replace(tzinfo=UTC)
    return (0, game_date.timestamp(), game_pk)


def _parse_game_datetime(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_schedule(schedule: dict[str, Any]) -> list[GameInfo]:
    """Flatten a date-range schedule payload into games in chronological order.

    The API already returns dates in order, but a game whose ``gameDate`` failed
    to parse must not jump the queue, so those sort last instead of raising.
    """
    dates = schedule.get("dates")
    if not isinstance(dates, list):
        return []

    games: list[GameInfo] = []
    for day in dates:
        if not isinstance(day, dict):
            continue
        raw_games = day.get("games")
        if not isinstance(raw_games, list):
            continue
        for raw_game in raw_games:
            if isinstance(raw_game, dict):
                games.append(parse_game(raw_game))

    return sorted(games, key=_schedule_sort_key)


def _schedule_sort_key(game: GameInfo) -> tuple[int, float, int]:
    """Chronological order, tolerating unparsed and naive start times.

    A game whose ``gameDate`` could not be parsed sorts last rather than
    breaking the comparison, and a naive timestamp is read as UTC so it never
    raises against the aware ones alongside it.
    """
    game_date = game.game_date
    if game_date is None:
        return (1, 0.0, game.game_pk)
    if game_date.tzinfo is None:
        game_date = game_date.replace(tzinfo=UTC)
    return (0, game_date.timestamp(), game.game_pk)


def parse_standings(
    data: dict[str, Any], division_id: int = AL_EAST_DIVISION_ID
) -> DivisionStandings | None:
    """Pull one division's records out of a league-wide standings payload."""
    records = data.get("records")
    if not isinstance(records, list):
        return None

    for record in records:
        if not isinstance(record, dict):
            continue
        division = record.get("division")
        division = division if isinstance(division, dict) else {}
        if _safe_int(division.get("id")) != division_id:
            continue

        teams = parse_team_records(record.get("teamRecords"))
        return DivisionStandings(
            division_id=division_id,
            division_name=str(
                division.get("nameShort") or division.get("name") or "Division"
            ),
            teams=teams,
            season=_optional_text(record.get("season"))
            or _optional_text(division.get("season"))
            or _first_team_season(record.get("teamRecords")),
        )
    return None


def parse_wild_card_standings(data: dict[str, Any]) -> WildCardStandings | None:
    """Flatten a wildCard standings payload into one ranked league-wide race.

    The payload nests team records under one or more records entries, so they
    are gathered and re-sorted by wild card rank rather than trusting the
    grouping to already be in race order.
    """
    records = data.get("records")
    if not isinstance(records, list):
        return None

    collected: list[TeamRecord] = []
    season: str | None = None
    for record in records:
        if not isinstance(record, dict):
            continue
        collected.extend(parse_team_records(record.get("teamRecords")))
        season = season or _first_team_season(record.get("teamRecords"))

    if not collected:
        return None

    return WildCardStandings(
        league_id=AMERICAN_LEAGUE_ID,
        league_name="American League",
        teams=tuple(sorted(collected, key=_wild_card_sort_key)),
        season=season,
    )


def _wild_card_sort_key(record: TeamRecord) -> tuple[int, float, int]:
    """Order by wild card rank, falling back to wins when the rank is absent."""
    rank = _safe_int(record.wild_card_rank)
    if rank is not None:
        return (0, float(rank), -record.wins)
    return (1, 0.0, -record.wins)


def _first_team_season(raw_records: Any) -> str | None:
    """Season year, which the API reports per team record rather than per division."""
    if not isinstance(raw_records, list):
        return None
    for raw in raw_records:
        if isinstance(raw, dict):
            season = _optional_text(raw.get("season"))
            if season:
                return season
    return None


def parse_team_records(raw_records: Any) -> tuple[TeamRecord, ...]:
    if not isinstance(raw_records, list):
        return ()

    records: list[TeamRecord] = []
    for raw in raw_records:
        if not isinstance(raw, dict):
            continue
        team = raw.get("team")
        team = team if isinstance(team, dict) else {}
        team_id = _safe_int(team.get("id"))
        if team_id is None:
            continue
        league_record = raw.get("leagueRecord")
        league_record = league_record if isinstance(league_record, dict) else {}
        records.append(
            TeamRecord(
                team_id=team_id,
                team_name=str(team.get("name") or f"Team {team_id}"),
                wins=_safe_int(raw.get("wins"))
                or _safe_int(league_record.get("wins"))
                or 0,
                losses=_safe_int(raw.get("losses"))
                or _safe_int(league_record.get("losses"))
                or 0,
                winning_percentage=_optional_text(raw.get("winningPercentage"))
                or _optional_text(league_record.get("pct")),
                division_rank=_optional_text(raw.get("divisionRank")),
                games_back=_optional_text(raw.get("gamesBack")),
                wild_card_games_back=_optional_text(raw.get("wildCardGamesBack")),
                streak=_parse_streak(raw.get("streak")),
                run_differential=_safe_int(raw.get("runDifferential")),
                division_leader=bool(raw.get("divisionLeader")),
                clinch_indicator=_optional_text(raw.get("clinchIndicator")),
                wild_card_rank=_optional_text(raw.get("wildCardRank")),
                wild_card_leader=bool(raw.get("wildCardLeader")),
            )
        )

    return tuple(
        sorted(records, key=lambda record: _rank_sort_key(record))
    )


def _rank_sort_key(record: TeamRecord) -> tuple[int, float, int]:
    """Order by division rank, falling back to win total when rank is absent.

    Rank arrives as a string and is missing entirely before the season starts,
    so an unranked team sorts by record rather than landing at the top.
    """
    rank = _safe_int(record.division_rank)
    if rank is not None:
        return (0, float(rank), -record.wins)
    return (1, 0.0, -record.wins)


def _parse_streak(raw: Any) -> str | None:
    if isinstance(raw, dict):
        return _optional_text(raw.get("streakCode"))
    return _optional_text(raw)


def _optional_text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def parse_game(raw_game: dict[str, Any], boxscore: dict[str, Any] | None = None) -> GameInfo:
    teams = raw_game.get("teams", {})
    side = _orioles_side(teams)
    other_side = "away" if side == "home" else "home"
    opponent = teams.get(other_side, {}).get("team", {})
    status = raw_game.get("status", {}).get("detailedState") or "Unknown"
    venue = raw_game.get("venue", {}).get("name") or "TBD"

    lineup = _extract_lineup(boxscore, side)
    opponent_lineup = _extract_lineup(boxscore, other_side)
    pitcher = _extract_confirmed_pitcher(boxscore, side) or _extract_probable_pitcher(
        teams.get(side, {})
    )
    opponent_pitcher = _extract_confirmed_pitcher(boxscore, other_side) or _extract_probable_pitcher(
        teams.get(other_side, {})
    )
    orioles_score = _safe_int(teams.get(side, {}).get("score"))
    opponent_score = _safe_int(teams.get(other_side, {}).get("score"))

    game_date = None
    raw_game_date = raw_game.get("gameDate")
    if isinstance(raw_game_date, str):
        try:
            game_date = datetime.fromisoformat(raw_game_date.replace("Z", "+00:00"))
        except ValueError:
            game_date = None

    return GameInfo(
        game_pk=_safe_int(raw_game.get("gamePk")) or 0,
        game_date=game_date,
        status=status,
        venue=venue,
        home_team=teams.get("home", {}).get("team", {}).get("name") or "Home",
        home_team_id=_safe_int(teams.get("home", {}).get("team", {}).get("id")),
        away_team=teams.get("away", {}).get("team", {}).get("name") or "Away",
        opponent=opponent.get("name") or "Opponent TBD",
        opponent_team_id=_safe_int(opponent.get("id")),
        is_home=side == "home",
        orioles_score=orioles_score,
        opponent_score=opponent_score,
        pitcher=pitcher,
        opponent_pitcher=opponent_pitcher,
        lineup=lineup,
        opponent_lineup=opponent_lineup,
    )


def parse_transactions(
    raw_transactions: list[Any], fallback_date: date
) -> list[TransactionInfo]:
    """Merge the API's per-player rows into one entry per transaction.

    The transactions endpoint repeats a transaction once per player involved,
    each row naming a different ``person``. Grouping by transaction id keeps
    every player and stops multi-player trades posting as duplicates.
    """
    grouped: dict[str, TransactionInfo] = {}
    players: dict[str, list[TransactionPlayer]] = {}
    for raw in raw_transactions:
        if not isinstance(raw, dict):
            continue
        parsed = parse_transaction(raw, fallback_date)
        key = parsed.transaction_id
        collected = players.setdefault(key, [])
        for player in parsed.players:
            if all(existing.player_id != player.player_id for existing in collected):
                collected.append(player)
        existing_entry = grouped.get(key)
        if existing_entry is None:
            grouped[key] = parsed
        elif existing_entry.player_id is None and parsed.player_id is not None:
            grouped[key] = parsed

    return [
        replace(
            entry,
            players=tuple(players[key]),
            player_id=entry.player_id
            if entry.player_id is not None
            else (players[key][0].player_id if players[key] else None),
            player_name=entry.player_name
            if entry.player_name is not None
            else (players[key][0].name if players[key] else None),
            headshot_url=entry.headshot_url
            if entry.headshot_url is not None
            else (headshot_url(players[key][0].player_id) if players[key] else None),
        )
        for key, entry in grouped.items()
    ]


def parse_transaction(raw: dict[str, Any], fallback_date: date) -> TransactionInfo:
    person = raw.get("person") if isinstance(raw.get("person"), dict) else {}
    player_id = _safe_int(person.get("id"))
    player_name = person.get("fullName")
    raw_date = raw.get("effectiveDate") or raw.get("date")
    transaction_date = fallback_date
    if isinstance(raw_date, str):
        try:
            transaction_date = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
        except ValueError:
            transaction_date = fallback_date

    description = (
        raw.get("description")
        or raw.get("typeDesc")
        or "Roster transaction details unavailable."
    )
    type_description = raw.get("typeDesc") or raw.get("typeCode") or "Transaction"
    transaction_id = str(raw.get("id") or _stable_transaction_id(raw, description))

    return TransactionInfo(
        transaction_id=transaction_id,
        date=transaction_date,
        player_id=player_id,
        player_name=player_name,
        type_description=type_description,
        description=description,
        headshot_url=headshot_url(player_id) if player_id is not None else None,
        players=(
            (TransactionPlayer(player_id, player_name),)
            if player_id is not None and player_name
            else ()
        ),
    )


def _orioles_side(teams: dict[str, Any]) -> str:
    for side in ("home", "away"):
        if _safe_int(teams.get(side, {}).get("team", {}).get("id")) == ORIOLES_TEAM_ID:
            return side
    return "home"


def _extract_lineup(boxscore: dict[str, Any] | None, side: str) -> tuple[LineupPlayer, ...]:
    team_box = _boxscore_team(boxscore, side)
    batting_order = team_box.get("battingOrder") or []
    players = team_box.get("players") or {}
    lineup: list[LineupPlayer] = []

    for index, raw_player_id in enumerate(batting_order, start=1):
        player_id = _safe_int(raw_player_id)
        if player_id is None:
            continue
        player = players.get(f"ID{player_id}", {})
        person = player.get("person", {})
        position = player.get("position", {})
        lineup.append(
            LineupPlayer(
                player_id=player_id,
                name=person.get("fullName") or f"Player {player_id}",
                position=position.get("abbreviation") or position.get("name") or "—",
                batting_order=index,
                headshot_url=headshot_url(player_id),
            )
        )
    return tuple(lineup)


def _extract_confirmed_pitcher(boxscore: dict[str, Any] | None, side: str) -> PitcherInfo | None:
    team_box = _boxscore_team(boxscore, side)
    pitcher_ids = team_box.get("pitchers") or team_box.get("pitchingOrder") or []
    if not pitcher_ids:
        return None
    player_id = _safe_int(pitcher_ids[0])
    if player_id is None:
        return None

    player = (team_box.get("players") or {}).get(f"ID{player_id}", {})
    name = player.get("person", {}).get("fullName")
    if not name:
        return None
    return PitcherInfo(
        player_id=player_id,
        name=name,
        headshot_url=headshot_url(player_id),
        status="Starting pitcher",
    )


def _extract_probable_pitcher(raw_team: dict[str, Any]) -> PitcherInfo | None:
    probable = raw_team.get("probablePitcher")
    if not isinstance(probable, dict):
        return None
    player_id = _safe_int(probable.get("id"))
    name = probable.get("fullName")
    if not name:
        return None
    return PitcherInfo(
        player_id=player_id,
        name=name,
        headshot_url=headshot_url(player_id) if player_id is not None else None,
        status="Probable pitcher",
    )


def _boxscore_team(boxscore: dict[str, Any] | None, side: str) -> dict[str, Any]:
    data = boxscore or {}
    teams = data.get("teams")
    if isinstance(teams, dict):
        return teams.get(side, {})

    live_teams = (
        data.get("liveData", {})
        .get("boxscore", {})
        .get("teams", {})
    )
    if isinstance(live_teams, dict):
        return live_teams.get(side, {})
    return {}


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _stable_transaction_id(raw: dict[str, Any], description: str) -> str:
    digest = hashlib.sha256(repr(sorted(raw.items())).encode("utf-8")).hexdigest()
    return f"generated-{digest[:16]}-{hashlib.sha1(description.encode('utf-8')).hexdigest()[:8]}"


def parse_roster(data: dict[str, Any]) -> tuple[PlayerRef, ...]:
    entries = data.get("roster")
    if not isinstance(entries, list):
        return ()

    players: list[PlayerRef] = []
    seen: set[int] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        person = entry.get("person")
        person = person if isinstance(person, dict) else {}
        player_id = _safe_int(person.get("id"))
        name = str(person.get("fullName") or "").strip()
        if player_id is None or not name or player_id in seen:
            continue
        seen.add(player_id)
        players.append(PlayerRef(player_id, name, _position_abbreviation(entry)))
    return tuple(players)


def parse_people(data: dict[str, Any]) -> tuple[PlayerRef, ...]:
    people = data.get("people")
    if not isinstance(people, list):
        return ()

    players: list[PlayerRef] = []
    seen: set[int] = set()
    for person in people:
        if not isinstance(person, dict):
            continue
        player_id = _safe_int(person.get("id"))
        name = str(person.get("fullName") or "").strip()
        if player_id is None or not name or player_id in seen:
            continue
        seen.add(player_id)
        players.append(PlayerRef(player_id, name, _position_abbreviation(person)))
    return tuple(players)


def _position_abbreviation(raw: dict[str, Any]) -> str | None:
    for key in ("position", "primaryPosition"):
        position = raw.get(key)
        if isinstance(position, dict):
            abbreviation = str(position.get("abbreviation") or "").strip()
            if abbreviation:
                return abbreviation
    return None


def parse_player_stats(
    data: dict[str, Any],
) -> tuple[HittingSplit | None, PitchingSplit | None]:
    """Split a byDateRange stats payload into its hitting and pitching totals.

    A window can legitimately contain both groups — a two-way player, or a
    position player who mopped up an inning — so both are returned and the
    caller decides what to render.
    """
    groups = data.get("stats")
    if not isinstance(groups, list):
        return None, None

    hitting: HittingSplit | None = None
    pitching: PitchingSplit | None = None
    for group in groups:
        if not isinstance(group, dict):
            continue
        name = str((group.get("group") or {}).get("displayName") or "").strip().lower()
        stat = _first_split_stat(group)
        if stat is None:
            continue
        if name == "hitting" and hitting is None:
            hitting = parse_hitting_split(stat)
        elif name == "pitching" and pitching is None:
            pitching = parse_pitching_split(stat)
    return hitting, pitching


def parse_pitching_game_logs(data: dict[str, Any]) -> tuple[PitchingGame, ...]:
    groups = data.get("stats")
    if not isinstance(groups, list):
        return ()

    games: list[PitchingGame] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        if str((group.get("group") or {}).get("displayName") or "").lower() != "pitching":
            continue
        splits = group.get("splits")
        if not isinstance(splits, list):
            continue
        for split in splits:
            if not isinstance(split, dict):
                continue
            stat = split.get("stat")
            raw_date = split.get("date")
            opponent = split.get("opponent")
            if not isinstance(stat, dict) or not isinstance(raw_date, str):
                continue
            try:
                game_date = date.fromisoformat(raw_date)
            except ValueError:
                continue
            opponent_name = (
                str(opponent.get("name") or "").strip()
                if isinstance(opponent, dict)
                else ""
            )
            if not opponent_name:
                opponent_name = "Opponent"
            result: str | None = None
            if isinstance(split.get("isWin"), bool):
                result = "W" if split["isWin"] else "L"
            game = split.get("game")
            team = split.get("team")
            decision = "No decision"
            if _stat_int(stat, "wins"):
                decision = "Win"
            elif _stat_int(stat, "losses"):
                decision = "Loss"
            games.append(
                PitchingGame(
                    game_date=game_date,
                    opponent=opponent_name,
                    is_home=bool(split.get("isHome")),
                    result=result,
                    stat=parse_pitching_split(stat),
                    decision=decision,
                    game_pk=_safe_int(game.get("gamePk"))
                    if isinstance(game, dict)
                    else None,
                    team_id=_safe_int(team.get("id"))
                    if isinstance(team, dict)
                    else None,
                )
            )
    return tuple(games)


def _first_split_stat(group: dict[str, Any]) -> dict[str, Any] | None:
    """The first split that actually holds totals.

    byDateRange returns one split per player, but an empty ``splits`` list (no
    appearances in the window) and split entries without a ``stat`` object both
    occur, so neither is assumed.
    """
    splits = group.get("splits")
    if not isinstance(splits, list):
        return None
    for split in splits:
        if not isinstance(split, dict):
            continue
        stat = split.get("stat")
        if isinstance(stat, dict) and stat:
            return stat
    return None


def parse_hitting_split(stat: dict[str, Any]) -> HittingSplit:
    return HittingSplit(
        games=_stat_int(stat, "gamesPlayed"),
        plate_appearances=_stat_int(stat, "plateAppearances"),
        at_bats=_stat_int(stat, "atBats"),
        runs=_stat_int(stat, "runs"),
        hits=_stat_int(stat, "hits"),
        doubles=_stat_int(stat, "doubles"),
        triples=_stat_int(stat, "triples"),
        home_runs=_stat_int(stat, "homeRuns"),
        rbi=_stat_int(stat, "rbi"),
        walks=_stat_int(stat, "baseOnBalls"),
        strikeouts=_stat_int(stat, "strikeOuts"),
        stolen_bases=_stat_int(stat, "stolenBases"),
        average=_stat_float(stat, "avg"),
        on_base_percentage=_stat_float(stat, "obp"),
        slugging_percentage=_stat_float(stat, "slg"),
        ops=_stat_float(stat, "ops"),
    )


def parse_pitching_split(stat: dict[str, Any]) -> PitchingSplit:
    return PitchingSplit(
        games=_stat_int(stat, "gamesPlayed"),
        games_started=_stat_int(stat, "gamesStarted"),
        wins=_stat_int(stat, "wins"),
        losses=_stat_int(stat, "losses"),
        saves=_stat_int(stat, "saves"),
        innings_pitched=_stat_float(stat, "inningsPitched"),
        hits=_stat_int(stat, "hits"),
        runs=_stat_int(stat, "runs"),
        earned_runs=_stat_int(stat, "earnedRuns"),
        home_runs=_stat_int(stat, "homeRuns"),
        walks=_stat_int(stat, "baseOnBalls"),
        strikeouts=_stat_int(stat, "strikeOuts"),
        era=_stat_float(stat, "era"),
        whip=_stat_float(stat, "whip"),
    )


def _stat_int(stat: dict[str, Any], key: str) -> int:
    return _safe_int(stat.get(key)) or 0


def _stat_float(stat: dict[str, Any], key: str) -> float | None:
    """Parse a rate stat, tolerating MLB's placeholders for undefined values.

    Rates arrive as strings, and an undefined one is sent as ``.---`` (or
    ``-.--`` for ERA), which must read as "no data" rather than zero.
    """
    value = stat.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return None if number != number else number
    text = str(value).strip()
    if not text or set(text) <= {"-", "."}:
        return None
    if text.lower() in {"inf", "infinity", "nan"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None
