from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from zoneinfo import ZoneInfo

import discord

from .formatting import (
    format_game_time,
    format_game_title,
    format_lineup,
    format_lineup_heading,
    format_hitting_split,
    format_no_games,
    format_no_player_stats,
    format_no_transactions,
    format_pitchers,
    format_pitching_split,
    format_player_heading,
    format_stats_window,
    format_transaction,
)
from .mlb import (
    HEADSHOT_FEATURE_WIDTH,
    headshot_url,
    savant_player_url,
    savant_preview_url,
    team_logo_url,
)
from .models import (
    GameInfo,
    HittingSplit,
    MatchupAnnotation,
    ORIOLES_TEAM_ID,
    ORIOLES_TEAM_NAME,
    PitchingSplit,
    PlayerRef,
    StatsWindow,
    TransactionInfo,
)


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
        orioles_heading = format_lineup_heading(
            ORIOLES_TEAM_NAME,
            ORIOLES_TEAM_ID,
            game.opponent_team_id,
            game.opponent_pitcher,
        )
        opponent_heading = format_lineup_heading(
            game.opponent,
            game.opponent_team_id,
            ORIOLES_TEAM_ID,
            game.pitcher,
        )
        preview_link = f"[Statcast game preview]({savant_preview_url(game.game_pk)})"
        body = (
            f"{header}\n\n"
            f"{orioles_heading}\n{orioles_lineup}\n\n"
            f"{opponent_heading}\n{opponent_lineup}"
        )
        suffix = f"\n\n{preview_link}"
        description = f"{_limit_description(body, 4096 - len(suffix))}{suffix}"

        embed = discord.Embed(
            title=format_game_title(game),
            url=savant_preview_url(game.game_pk),
            description=description,
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

    _set_transaction_art(embed, transactions)

    if len(transactions) > 25:
        embed.set_footer(text=f"Showing 25 of {len(transactions)} transactions.")
    return [embed]


def _set_transaction_art(
    embed: discord.Embed, transactions: Sequence[TransactionInfo]
) -> None:
    """Show a large headshot when the post is about one specific player.

    Automatic announcements post one transaction at a time, so a call-up gets a
    full-width photo. A multi-player trade or a digest of several transactions
    falls back to a thumbnail, where a single face would misrepresent the post.
    """
    solo = transactions[0] if len(transactions) == 1 else None
    if solo is not None and solo.player_id is not None and len(solo.players) <= 1:
        embed.set_image(url=headshot_url(solo.player_id, HEADSHOT_FEATURE_WIDTH))
        return

    first_headshot = next(
        (item.headshot_url for item in transactions if item.headshot_url), None
    )
    if first_headshot:
        embed.set_thumbnail(url=first_headshot)


def player_stats_embed(
    player: PlayerRef,
    window: StatsWindow,
    hitting: HittingSplit | None,
    pitching: PitchingSplit | None,
) -> discord.Embed:
    sections = [format_player_heading(player), format_stats_window(window)]
    body = [
        section
        for section in (
            format_hitting_split(hitting) if hitting else None,
            format_pitching_split(pitching) if pitching else None,
        )
        if section
    ]
    if body:
        sections.extend(body)
    else:
        sections = [format_no_player_stats(player, window)]

    embed = discord.Embed(
        title=player.name,
        url=savant_player_url(player.player_id),
        description=_limit_description("\n\n".join(sections)),
        color=ORIOLES_ORANGE,
    )
    embed.set_thumbnail(url=headshot_url(player.player_id))
    embed.set_footer(text="Data: public MLB Stats API")
    return embed


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
        name="/playerstats <player> [days]",
        value=(
            "Show a player's hitting and pitching totals over the last N days "
            "(default 7, max 162). Start typing a name to pick from the Orioles "
            "roster, or type any big leaguer's full name."
        ),
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
