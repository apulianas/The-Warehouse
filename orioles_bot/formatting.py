from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from zoneinfo import ZoneInfo

from .mlb import savant_player_url, savant_team_matchup_url
from .models import (
    GameInfo,
    LineupPlayer,
    MatchupAnnotation,
    ORIOLES_TEAM_NAME,
    PitcherInfo,
    TransactionInfo,
    TransactionPlayer,
)


def format_game_title(game: GameInfo) -> str:
    matchup = f"{game.away_team} at {game.home_team}"
    if game.orioles_score is not None and game.opponent_score is not None:
        score = (
            f"Orioles {game.orioles_score}, "
            f"{game.opponent} {game.opponent_score}"
        )
        return f"{matchup} — {score}"
    return matchup


def format_game_time(game: GameInfo, time_zone: ZoneInfo) -> str:
    if game.game_date is None:
        return "Time TBD"
    return game.game_date.astimezone(time_zone).strftime("%a, %b %-d at %-I:%M %p %Z")


def format_pitcher(game: GameInfo) -> str:
    return _format_pitcher_line(ORIOLES_TEAM_NAME, game.pitcher)


def format_opponent_pitcher(game: GameInfo) -> str:
    return _format_pitcher_line(game.opponent, game.opponent_pitcher)


def format_pitchers(game: GameInfo) -> str:
    return f"{format_pitcher(game)}\n{format_opponent_pitcher(game)}"


def _format_pitcher_line(team: str, pitcher: PitcherInfo | None) -> str:
    if pitcher is None:
        return f"{team} starter: not announced"
    if pitcher.player_id is not None:
        name = f"[{pitcher.name}]({savant_player_url(pitcher.player_id)})"
    else:
        name = pitcher.name
    return f"{team} starter: {name} ({pitcher.status})"


def format_lineup_heading(
    batting_team: str,
    batting_team_id: int | None,
    pitching_team_id: int | None,
    opposing_pitcher: PitcherInfo | None,
) -> str:
    heading = f"**{batting_team} batting order**"
    if (
        batting_team_id is None
        or pitching_team_id is None
        or opposing_pitcher is None
        or opposing_pitcher.player_id is None
    ):
        return heading
    url = savant_team_matchup_url(
        batting_team_id, pitching_team_id, opposing_pitcher.player_id
    )
    return f"{heading} — [full matchup vs {opposing_pitcher.name}]({url})"


def format_lineup(
    players: tuple[LineupPlayer, ...],
    opposing_pitcher: PitcherInfo | None = None,
    matchup_annotations: Mapping[tuple[int, int], MatchupAnnotation] | None = None,
) -> str:
    if not players:
        return "Lineup has not been posted yet."

    lines = []
    for player in players:
        annotation = _matchup_annotation(player, opposing_pitcher, matchup_annotations)
        name = f"{player.name} {annotation.emoji}" if annotation else player.name
        metric = f" ({_format_matchup_metric(annotation)})" if annotation else ""
        lines.append(
            f"{player.batting_order}. {player.position} "
            f"[{name}]({savant_player_url(player.player_id)}){metric}"
        )
    return "\n".join(lines)


def _matchup_annotation(
    player: LineupPlayer,
    opposing_pitcher: PitcherInfo | None,
    matchup_annotations: Mapping[tuple[int, int], MatchupAnnotation] | None,
) -> MatchupAnnotation | None:
    if (
        matchup_annotations is None
        or opposing_pitcher is None
        or opposing_pitcher.player_id is None
    ):
        return None
    return matchup_annotations.get((player.player_id, opposing_pitcher.player_id))


def _format_matchup_metric(annotation: MatchupAnnotation) -> str:
    value = f"{annotation.metric_value:.3f}"
    if value.startswith("0"):
        value = value[1:]
    return f"{value} {annotation.metric_name}, {annotation.plate_appearances} PA"


def format_no_games(target_date: date) -> str:
    return f"No {ORIOLES_TEAM_NAME} game is scheduled for {target_date:%A, %B %-d, %Y}."


def format_transaction(transaction: TransactionInfo) -> str:
    description, linked = _linkify_players(
        transaction.description, transaction.players
    )
    if linked:
        return f"**{transaction.type_description}** — {description}"

    player = transaction.player_name
    if player and transaction.player_id is not None:
        player = f"[{player}]({savant_player_url(transaction.player_id)})"
    elif not player:
        player = "Orioles"
    return f"**{transaction.type_description}** — {player}: {description}"


def _linkify_players(
    description: str, players: tuple[TransactionPlayer, ...]
) -> tuple[str, bool]:
    """Link every player named in the description to their Savant page.

    Matches are resolved in a single pass so an inserted link is never
    rewritten, and longer names win over shorter ones that overlap them.
    """
    spans: list[tuple[int, int, TransactionPlayer]] = []
    for player in players:
        if not player.name:
            continue
        start = description.find(player.name)
        while start != -1:
            spans.append((start, start + len(player.name), player))
            start = description.find(player.name, start + 1)

    spans.sort(key=lambda span: (span[0], span[0] - span[1]))

    pieces: list[str] = []
    cursor = 0
    linked = False
    for start, end, player in spans:
        if start < cursor:
            continue
        url = savant_player_url(player.player_id)
        pieces.append(description[cursor:start])
        pieces.append(f"[{description[start:end]}]({url})")
        cursor = end
        linked = True
    pieces.append(description[cursor:])
    return "".join(pieces), linked


def format_no_transactions(target_date: date) -> str:
    return f"No Orioles roster transactions found for {target_date:%A, %B %-d, %Y}."
