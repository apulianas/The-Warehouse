from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from .mlb import savant_matchup_url
from .models import GameInfo, LineupPlayer, ORIOLES_TEAM_NAME, PitcherInfo, TransactionInfo


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
    if game.pitcher is None:
        return "Pitcher not announced."
    if game.pitcher.headshot_url:
        return f"{game.pitcher.status}: [{game.pitcher.name}]({game.pitcher.headshot_url})"
    return f"{game.pitcher.status}: {game.pitcher.name}"


def format_lineup(
    players: tuple[LineupPlayer, ...], opposing_pitcher: PitcherInfo | None = None
) -> str:
    if not players:
        return "Lineup has not been posted yet."

    lines = []
    for player in players:
        link = (
            savant_matchup_url(player.player_id, opposing_pitcher.player_id)
            if opposing_pitcher and opposing_pitcher.player_id
            else player.headshot_url
        )
        lines.append(
            f"{player.batting_order}. {player.position} "
            f"[{player.name}]({link})"
        )
    return "\n".join(lines)


def format_no_games(target_date: date) -> str:
    return f"No {ORIOLES_TEAM_NAME} game is scheduled for {target_date:%A, %B %-d, %Y}."


def format_transaction(transaction: TransactionInfo) -> str:
    player = transaction.player_name
    if player and transaction.headshot_url:
        player = f"[{player}]({transaction.headshot_url})"
    elif not player:
        player = "Orioles"
    return f"**{transaction.type_description}** — {player}: {transaction.description}"


def format_no_transactions(target_date: date) -> str:
    return f"No Orioles roster transactions found for {target_date:%A, %B %-d, %Y}."
