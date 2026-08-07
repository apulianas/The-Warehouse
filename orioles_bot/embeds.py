from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from zoneinfo import ZoneInfo

import discord

from .formatting import (
    format_game_time,
    format_game_title,
    format_lineup,
    format_no_games,
    format_no_transactions,
    format_pitchers,
    format_transaction,
)
from .mlb import team_logo_url
from .models import GameInfo, MatchupAnnotation, ORIOLES_TEAM_NAME, TransactionInfo


ORIOLES_ORANGE = discord.Color.from_rgb(223, 70, 1)


def lineup_embeds(
    games: list[GameInfo],
    target_date: date,
    time_zone: ZoneInfo,
    matchup_annotations: Mapping[tuple[int, int], MatchupAnnotation] | None = None,
) -> list[discord.Embed]:
    if not games:
        return [
            discord.Embed(
                title="Orioles lineup",
                description=format_no_games(target_date),
                color=ORIOLES_ORANGE,
            )
        ]

    embeds: list[discord.Embed] = []
    for game in games:
        header = (
            f"{format_game_time(game, time_zone)} • {game.venue}\n"
            f"Status: {game.status}\n"
            f"{format_pitchers(game)}"
        )
        orioles_lineup = format_lineup(
            game.lineup, game.opponent_pitcher, matchup_annotations
        )
        opponent_lineup = format_lineup(
            game.opponent_lineup, game.pitcher, matchup_annotations
        )
        description = (
            f"{header}\n\n"
            f"**{ORIOLES_TEAM_NAME} batting order**\n{orioles_lineup}\n\n"
            f"**{game.opponent} batting order**\n{opponent_lineup}"
        )

        embed = discord.Embed(
            title=format_game_title(game),
            description=_limit_description(description),
            color=ORIOLES_ORANGE,
        )
        if game.home_team_id is not None:
            embed.set_thumbnail(url=team_logo_url(game.home_team_id))
        embed.set_footer(text=f"{ORIOLES_TEAM_NAME} • Game PK {game.game_pk}")
        embeds.append(embed)
    return embeds


def transaction_embeds(
    transactions: list[TransactionInfo], target_date: date
) -> list[discord.Embed]:
    if not transactions:
        return [
            discord.Embed(
                title="Orioles transactions",
                description=format_no_transactions(target_date),
                color=ORIOLES_ORANGE,
            )
        ]

    embed = discord.Embed(
        title=f"Orioles transactions — {target_date:%B %-d, %Y}",
        color=ORIOLES_ORANGE,
    )
    for transaction in transactions[:25]:
        embed.add_field(
            name=transaction.date.isoformat(),
            value=_limit_field(format_transaction(transaction)),
            inline=False,
        )

    first_headshot = next(
        (item.headshot_url for item in transactions if item.headshot_url), None
    )
    if first_headshot:
        embed.set_thumbnail(url=first_headshot)

    if len(transactions) > 25:
        embed.set_footer(text=f"Showing 25 of {len(transactions)} transactions.")
    return [embed]


def help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Orioles bot help",
        description="Slash commands for Baltimore Orioles lineups and roster moves.",
        color=ORIOLES_ORANGE,
    )
    embed.add_field(
        name="/lineup [date]",
        value=(
            "Show scheduled Orioles games, pitcher, and batting order when posted. "
            "Date accepts YYYY-MM-DD or today."
        ),
        inline=False,
    )
    embed.add_field(
        name="/transactions [date]",
        value="Show Orioles roster transactions for the date. Date accepts YYYY-MM-DD or today.",
        inline=False,
    )
    embed.add_field(
        name="/help",
        value="Show this help message.",
        inline=False,
    )
    embed.set_footer(text="Data: public MLB Stats API")
    return embed


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(
        title="MLB data unavailable", description=message, color=discord.Color.red()
    )


def _limit_description(text: str, max_chars: int = 4096) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 1]}…"


def _limit_field(text: str, max_chars: int = 1024) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 1]}…"
