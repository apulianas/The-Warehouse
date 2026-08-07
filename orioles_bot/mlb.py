from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

import aiohttp

from .models import (
    ORIOLES_TEAM_ID,
    GameInfo,
    LineupPlayer,
    PitcherInfo,
    TransactionInfo,
)


BASE_URL = "https://statsapi.mlb.com/api/v1"
HEADSHOT_URL_TEMPLATE = (
    "https://img.mlbstatic.com/mlb-photos/image/upload/"
    "w_180,q_auto:good/v1/people/{player_id}/headshot/67/current"
)
BASEBALL_SAVANT_SEARCH_URL = "https://baseballsavant.mlb.com/statcast_search"
BASEBALL_SAVANT_PLAYER_URL = "https://baseballsavant.mlb.com/savant-player"
TEAM_LOGO_URL_TEMPLATE = "https://midfield.mlbstatic.com/v1/team/{team_id}/spots/240"


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


def headshot_url(player_id: int | str) -> str:
    return HEADSHOT_URL_TEMPLATE.format(player_id=player_id)


def savant_matchup_params(batter_id: int | str, pitcher_id: int | str) -> str:
    return urlencode(
        {
            "all": "true",
            "batters_lookup[]": str(batter_id),
            "pitchers_lookup[]": str(pitcher_id),
            "hfGT": "R|",
            "type": "details",
        }
    )


def savant_matchup_url(batter_id: int | str, pitcher_id: int | str) -> str:
    return f"{BASEBALL_SAVANT_SEARCH_URL}?{savant_matchup_params(batter_id, pitcher_id)}"


def savant_player_url(player_id: int | str) -> str:
    return f"{BASEBALL_SAVANT_PLAYER_URL}/{player_id}"


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
        return [parse_transaction(item, target_date) for item in transactions]


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
        is_home=side == "home",
        orioles_score=orioles_score,
        opponent_score=opponent_score,
        pitcher=pitcher,
        opponent_pitcher=opponent_pitcher,
        lineup=lineup,
        opponent_lineup=opponent_lineup,
    )


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
