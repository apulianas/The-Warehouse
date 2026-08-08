from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from zoneinfo import ZoneInfo

import discord

from .formatting import (
    WILD_CARD_SPOTS,
    HAND_NAMES,
    format_game_time,
    format_game_title,
    format_lineup,
    format_lineup_heading,
    format_hitting_split,
    format_matchup_history,
    format_no_games,
    format_no_player_stats,
    format_no_scheduled_games,
    format_no_standings,
    format_no_transactions,
    format_orioles_standing,
    format_orioles_wild_card,
    format_pitchers,
    format_pitching_split,
    format_pitching_game,
    format_platoon_split,
    format_player_heading,
    format_schedule_day,
    format_schedule_entry,
    format_schedule_window,
    format_standings,
    format_stats_window,
    format_substitution_headline,
    format_substitution_pitcher,
    format_transaction,
    format_wild_card,
)
from .mlb import (
    HEADSHOT_FEATURE_WIDTH,
    headshot_url,
    savant_player_url,
    savant_preview_url,
    team_logo_url,
)
from .models import (
    DivisionStandings,
    GameInfo,
    HittingSplit,
    MatchupAnnotation,
    MatchupHistory,
    NextGame,
    ORIOLES_TEAM_ID,
    ORIOLES_TEAM_NAME,
    PitchingSplit,
    PitchingGame,
    PlayerRef,
    ScheduleWindow,
    StatsWindow,
    Substitution,
    TransactionInfo,
    WildCardStandings,
)


ORIOLES_ORANGE = discord.Color.from_rgb(223, 70, 1)
MAX_EMBED_FIELDS = 25


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


SUBSTITUTION_COLOR = discord.Color.from_rgb(45, 125, 70)


def substitution_embeds(
    substitutions: Sequence[Substitution],
    histories: Mapping[tuple[int, int], MatchupHistory] | None = None,
    platoon_splits: Mapping[int, HittingSplit] | None = None,
) -> list[discord.Embed]:
    """One compact card per hitter who entered the game.

    Deliberately narrow: a substitution is worth a note about the new bat, not
    a repost of the whole batting order.
    """
    embeds: list[discord.Embed] = []
    for substitution in substitutions:
        batter = substitution.batter
        pitcher = substitution.pitcher
        history = None
        if histories is not None and pitcher is not None and pitcher.player_id is not None:
            history = histories.get((batter.player_id, pitcher.player_id))
        split = (platoon_splits or {}).get(batter.player_id)

        description = "\n".join(
            [
                format_substitution_headline(substitution),
                format_substitution_pitcher(pitcher),
            ]
        )
        embed = discord.Embed(
            title=f"🔄 {substitution.batting_team} substitution",
            description=_limit_description(description),
            color=ORIOLES_ORANGE if substitution.is_orioles else SUBSTITUTION_COLOR,
        )
        pitcher_name = pitcher.name if pitcher is not None else "current pitcher"
        embed.add_field(
            name=f"Career vs {pitcher_name}",
            value=_limit_field(format_matchup_history(history, pitcher)),
            inline=False,
        )
        hand = pitcher.throws if pitcher is not None else None
        embed.add_field(
            name=f"This season vs {HAND_NAMES.get(hand or '', 'this hand')}",
            value=_limit_field(format_platoon_split(split, hand)),
            inline=False,
        )
        embed.set_thumbnail(url=headshot_url(batter.player_id))
        embed.set_footer(text=f"Game PK {substitution.game_pk}")
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
    return player_stats_embeds(player, window, hitting, pitching, ())[0]


def player_stats_embeds(
    player: PlayerRef,
    window: StatsWindow,
    hitting: HittingSplit | None,
    pitching: PitchingSplit | None,
    pitching_games: Sequence[PitchingGame],
) -> list[discord.Embed]:
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

    summary = discord.Embed(
        title=player.name,
        url=savant_player_url(player.player_id),
        description=_limit_description("\n\n".join(sections)),
        color=ORIOLES_ORANGE,
    )
    summary.set_thumbnail(url=headshot_url(player.player_id))
    summary.set_footer(text="Data: public MLB Stats API")
    if not pitching_games:
        return [summary]

    embeds = [summary]
    for offset in range(0, len(pitching_games), MAX_EMBED_FIELDS):
        page = pitching_games[offset : offset + MAX_EMBED_FIELDS]
        embed = discord.Embed(
            title=f"{player.name} — pitching game log",
            color=ORIOLES_ORANGE,
        )
        for game in page:
            location = "vs" if game.is_home else "at"
            result = f"Team {game.result}" if game.result else "Team result unknown"
            score = (
                f" ({game.team_score}-{game.opponent_score})"
                if game.team_score is not None and game.opponent_score is not None
                else ""
            )
            embed.add_field(
                name=(
                    f"**{game.game_date:%b %-d} {location} {game.opponent} — "
                    f"{result}{score}, Pitcher {game.decision}**"
                ),
                value=_limit_field(format_pitching_game(game)),
                inline=False,
            )
        end = offset + len(page)
        embed.set_footer(
            text=f"Games {offset + 1}–{end} of {len(pitching_games)} • Data: public MLB Stats API"
        )
        embeds.append(embed)
    return embeds


def standings_embed(
    standings: DivisionStandings | None,
    next_games: Mapping[int, NextGame] | None = None,
    time_zone: ZoneInfo | None = None,
) -> discord.Embed:
    if standings is None:
        return discord.Embed(
            title="AL East standings",
            description=format_no_standings(),
            color=ORIOLES_ORANGE,
        )

    title = f"{standings.division_name} standings"
    if standings.season:
        title = f"{title} — {standings.season}"

    embed = discord.Embed(
        title=title,
        description=_limit_description(
            format_standings(standings, next_games, time_zone)
        ),
        color=ORIOLES_ORANGE,
    )
    embed.set_thumbnail(url=team_logo_url(ORIOLES_TEAM_ID))
    summary = format_orioles_standing(standings)
    embed.set_footer(
        text=f"{summary} • Data: public MLB Stats API"
        if summary
        else "Data: public MLB Stats API"
    )
    return embed


def wild_card_embed(
    standings: WildCardStandings | None,
    next_games: Mapping[int, NextGame] | None = None,
    time_zone: ZoneInfo | None = None,
) -> discord.Embed:
    if standings is None:
        return discord.Embed(
            title="AL wild card",
            description=format_no_standings(),
            color=ORIOLES_ORANGE,
        )

    title = f"{standings.league_name} wild card"
    if standings.season:
        title = f"{title} — {standings.season}"

    embed = discord.Embed(
        title=title,
        description=_limit_description(
            format_wild_card(standings, next_games, time_zone)
        ),
        color=ORIOLES_ORANGE,
    )
    embed.set_thumbnail(url=team_logo_url(ORIOLES_TEAM_ID))
    summary = format_orioles_wild_card(standings)
    embed.set_footer(
        text=f"{summary} • Top {WILD_CARD_SPOTS} make the playoffs"
        if summary
        else f"Top {WILD_CARD_SPOTS} make the playoffs"
    )
    return embed


def schedule_embeds(    games: Sequence[GameInfo], window: ScheduleWindow, time_zone: ZoneInfo
) -> list[discord.Embed]:
    if not games:
        return [
            discord.Embed(
                title="Orioles schedule",
                description=format_no_scheduled_games(window),
                color=ORIOLES_ORANGE,
            )
        ]

    embed = discord.Embed(
        title="Orioles schedule",
        description=format_schedule_window(window),
        color=ORIOLES_ORANGE,
    )
    for game in games[:MAX_EMBED_FIELDS]:
        embed.add_field(
            name=format_schedule_day(game, time_zone),
            value=_limit_field(format_schedule_entry(game, time_zone)),
            inline=False,
        )
    embed.set_thumbnail(url=team_logo_url(ORIOLES_TEAM_ID))
    if len(games) > MAX_EMBED_FIELDS:
        embed.set_footer(
            text=f"Showing {MAX_EMBED_FIELDS} of {len(games)} games."
        )
    else:
        embed.set_footer(text="Data: public MLB Stats API")
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
        name="/playerstats <player> [days]",
        value=(
            "Show a player's hitting and pitching totals over the last N days "
            "(default 7, max 162). Start typing a name to pick from the Orioles "
            "roster, or type any big leaguer's full name."
        ),
        inline=False,
    )
    embed.add_field(
        name="/standings [view]",
        value=(
            "Show the AL wild card race and the AL East, with each team's record, "
            "games back, streak, and next opponent. The wild card view marks the "
            "playoff line. Pick a view to see just one."
        ),
        inline=False,
    )
    embed.add_field(
        name="/schedule [days]",
        value=(
            "Show upcoming Orioles games over the next N days (default 7, max 30) "
            "with opponent, start time, and probable starters."
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
