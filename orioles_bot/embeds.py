from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from zoneinfo import ZoneInfo

import discord

from .formatting import (
    WILD_CARD_SPOTS,
    format_at_bat_heading,
    format_at_bat_matchups,
    format_at_bat_pitcher,
    format_at_bat_slots,
    format_count,
    format_no_at_bat,
    format_no_live_game,
    format_runners,
    HAND_NAMES,
    format_game_time,
    format_game_title,
    format_lineup,
    format_lineup_heading,
    format_hitting_split,
    format_matchup_history,
    format_moment,
    format_injured_player,
    format_injury_summary,
    format_no_games,
    format_no_injuries,
    format_no_pitch_mix_game,
    format_no_pitches,
    format_no_player_stats,
    format_no_relievers,
    format_no_scheduled_games,
    format_no_standings,
    format_no_transactions,
    format_orioles_standing,
    format_orioles_wild_card,
    format_pitch_mix,
    format_pitch_mix_summary,
    format_pitcher_not_in_game,
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
    format_recent_hand_split,
    format_reliever,
    format_bullpen_window,
    RELIEVER_SECTION_LABELS,
    format_running_profile,
    format_transaction,
    format_wild_card,
)
from .mlb import (
    headshot_url,
    savant_player_url,
    savant_preview_url,
    team_logo_url,
)
from .models import (
    DivisionStandings,
    GameInfo,
    HittingSplit,
    InjuredPlayer,
    MatchupAnnotation,
    MatchupHistory,
    NextGame,
    ORIOLES_TEAM_ID,
    ORIOLES_TEAM_NAME,
    OutingPitchMix,
    PitchingSplit,
    PitchingGame,
    PlayerRef,
    RECENT_SPLIT_DAYS,
    RECENT_SPLIT_HANDS,
    BULLPEN_WORKLOAD_DAYS,
    AtBatState,
    RelieverStatus,
    RunningProfile,
    ScheduleWindow,
    StatsWindow,
    Substitution,
    TransactionInfo,
    WildCardStandings,
)


ORIOLES_ORANGE = discord.Color.from_rgb(223, 70, 1)
MAX_EMBED_FIELDS = 25
SECTION_JOINING = "Joining the roster"
SECTION_LEAVING = "Leaving the roster"
SECTION_OTHER = "Other moves"
TRANSACTION_SECTIONS = (SECTION_JOINING, SECTION_LEAVING, SECTION_OTHER)
# Discord rejects an empty field name, so a section that spills over a second
# field continues under a zero-width space instead of repeating its heading.
BLANK_FIELD_NAME = "\u200b"
# Discord allows ten embeds per message. A handful of player cards plus the
# card holding everything else stays well inside that, and stops a deadline day
# turning the post into a column of photos.
MAX_TRANSACTION_PHOTO_CARDS = 4


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
            # Discord swallows the break between a heading line and a numbered
            # list unless a blank line separates them, so the first batter would
            # otherwise run on from the matchup link.
            f"{orioles_heading}\n\n{orioles_lineup}\n\n"
            f"{opponent_heading}\n\n{opponent_lineup}"
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
    running_profiles: Mapping[int, RunningProfile] | None = None,
    recent_splits: Mapping[int, MatchupHistory] | None = None,
) -> list[discord.Embed]:
    """One compact card per player who entered the game.

    Deliberately narrow: a substitution is worth a note about the new player,
    not a repost of the whole batting order. Pinch runners get a baserunning
    card since they are not coming up to hit.
    """
    embeds: list[discord.Embed] = []
    for substitution in substitutions:
        batter = substitution.batter
        pitcher = substitution.pitcher

        # A runner cares about who is holding him on, not who he is hitting off.
        mound_label = "On the mound" if substitution.is_pinch_runner else "Facing"
        description = "\n".join(
            [
                format_substitution_headline(substitution),
                format_substitution_pitcher(pitcher, label=mound_label),
            ]
        )
        # A runner is the one substitution that is not about to hit, so the
        # icon says at a glance which kind of card this is.
        icon = "🏃" if substitution.is_pinch_runner else "🔄"
        embed = discord.Embed(
            title=f"{icon} {substitution.batting_team} substitution",
            description=_limit_description(description),
            color=ORIOLES_ORANGE if substitution.is_orioles else SUBSTITUTION_COLOR,
        )

        if substitution.is_pinch_runner:
            profile = (running_profiles or {}).get(batter.player_id)
            embed.add_field(
                name="Baserunning",
                value=_limit_field(format_running_profile(profile)),
                inline=False,
            )
        else:
            history = None
            if (
                histories is not None
                and pitcher is not None
                and pitcher.player_id is not None
            ):
                history = histories.get((batter.player_id, pitcher.player_id))
            split = (platoon_splits or {}).get(batter.player_id)

            pitcher_name = pitcher.name if pitcher is not None else "current pitcher"
            embed.add_field(
                name=f"Career vs {pitcher_name}",
                value=_limit_field(format_matchup_history(history, pitcher)),
                inline=False,
            )
            hand = pitcher.throws if pitcher is not None else None
            hand_label = HAND_NAMES.get(hand or "", "this hand")
            embed.add_field(
                name=f"This season vs {hand_label}",
                value=_limit_field(format_platoon_split(split, hand)),
                inline=False,
            )
            # Without a known hand there is no recent split to fetch, and a
            # second "unavailable" row would only pad the card.
            if hand in RECENT_SPLIT_HANDS:
                recent = (recent_splits or {}).get(batter.player_id)
                embed.add_field(
                    name=f"Last {RECENT_SPLIT_DAYS} days vs {hand_label}",
                    value=_limit_field(format_recent_hand_split(recent, hand)),
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

    shown = list(transactions[:MAX_EMBED_FIELDS])
    title = f"Orioles transactions — {format_moment(target_date, '%B %-d, %Y')}"
    footer = (
        f"Showing {MAX_EMBED_FIELDS} of {len(transactions)} transactions."
        if len(transactions) > MAX_EMBED_FIELDS
        else None
    )

    featured = _photo_arrivals(shown)
    if len(featured) < 2:
        card = _transaction_card(title, shown, footer)
        _set_transaction_art(card, shown)
        return [card]

    # Several players joined at once and one card carries one face, so each of
    # them gets a card of his own. They stay thumbnails: a column of full-width
    # photos would swallow a phone screen.
    cards: list[discord.Embed] = []
    for index, arrival in enumerate(featured):
        card = discord.Embed(color=ORIOLES_ORANGE)
        if index == 0:
            card.title = title
        card.add_field(
            name=SECTION_JOINING if index == 0 else BLANK_FIELD_NAME,
            value=_limit_field(format_transaction(arrival)),
            inline=False,
        )
        card.set_thumbnail(url=arrival.headshot_url)
        cards.append(card)

    remainder = [item for item in shown if item not in featured]
    if remainder:
        cards.append(_transaction_card(None, remainder, footer))
    elif footer:
        cards[-1].set_footer(text=footer)
    return cards


def _transaction_card(
    title: str | None,
    transactions: Sequence[TransactionInfo],
    footer: str | None,
) -> discord.Embed:
    embed = discord.Embed(color=ORIOLES_ORANGE)
    if title:
        embed.title = title
    for name, value in _transaction_sections(transactions):
        embed.add_field(name=name, value=value, inline=False)
    if footer:
        embed.set_footer(text=footer)
    return embed


def _photo_arrivals(
    transactions: Sequence[TransactionInfo],
) -> list[TransactionInfo]:
    """Arriving players who can carry a card of their own.

    A move naming several players has no single face to show, so it stays in
    the shared card rather than claiming a photo it cannot justify.
    """
    arrivals = [
        item
        for item in transactions
        if item.is_arrival and item.headshot_url and len(item.players) <= 1
    ]
    return arrivals[:MAX_TRANSACTION_PHOTO_CARDS]


def _transaction_sections(
    transactions: Sequence[TransactionInfo],
) -> list[tuple[str, str]]:
    """Sort a card into who is coming and who is going.

    MLB's feed interleaves moves, so two options and the two recalls they pay
    for arrive shuffled and a flat list hides which players changed places. The
    headings also replace a per-move date that only ever repeated the one in
    the title; a retroactive date still reads in the move's own wording.
    Trades name both directions at once and get their own section rather than
    being forced onto one side.
    """
    grouped: dict[str, list[str]] = {label: [] for label in TRANSACTION_SECTIONS}
    for transaction in transactions:
        grouped[_section_label(transaction)].append(format_transaction(transaction))

    fields: list[tuple[str, str]] = []
    for label in TRANSACTION_SECTIONS:
        lines = grouped[label]
        if not lines:
            continue
        # A busy day can outrun a single field, so a long section spills into
        # more of them rather than being truncated away.
        for index, chunk in enumerate(_pack_field_values(lines)):
            fields.append((label if index == 0 else BLANK_FIELD_NAME, chunk))
    return fields


def _section_label(transaction: TransactionInfo) -> str:
    if transaction.is_arrival:
        return SECTION_JOINING
    if transaction.is_departure:
        return SECTION_LEAVING
    return SECTION_OTHER


def _pack_field_values(lines: Sequence[str], max_chars: int = 1024) -> list[str]:
    values: list[str] = []
    current: list[str] = []
    length = 0
    for line in lines:
        line = _limit_field(line, max_chars)
        # The +1 covers the newline joining this line to the one before it.
        if current and length + len(line) + 1 > max_chars:
            values.append("\n".join(current))
            current, length = [], 0
        length += len(line) + (1 if current else 0)
        current.append(line)
    if current:
        values.append("\n".join(current))
    return values


def _set_transaction_art(
    embed: discord.Embed, transactions: Sequence[TransactionInfo]
) -> None:
    """Show the arriving player's face, small.

    In a paired option-out/recall-in the player joining the roster is the news.
    It stays a thumbnail whatever the card covers: a full-width headshot takes
    up most of a phone screen on its own, and a card can hold only one anyway,
    so a lone move earns no more room than the rest.
    """
    # `sorted` is stable, so arrivals keep their posted order among themselves.
    arrivals_first = sorted(transactions, key=lambda item: not item.is_arrival)
    first_headshot = next(
        (item.headshot_url for item in arrivals_first if item.headshot_url), None
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
                    f"**{format_moment(game.game_date, '%b %-d')} {location} "
                    f"{game.opponent} — "
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


def on_deck_embed(
    state: AtBatState,
    game: GameInfo,
    histories: Mapping[tuple[int, int], MatchupHistory] | None = None,
) -> discord.Embed:
    """Who is at bat, on deck, and in the hole, with the situation around them."""
    if state.is_empty:
        embed = discord.Embed(
            title="Orioles on deck",
            description=format_no_at_bat(game),
            color=ORIOLES_ORANGE,
        )
        embed.set_thumbnail(url=team_logo_url(ORIOLES_TEAM_ID))
        embed.set_footer(text="Data: public MLB Stats API")
        return embed

    sections = [
        section
        for section in (
            format_at_bat_slots(state),
            format_at_bat_pitcher(state),
            format_runners(state),
        )
        if section
    ]
    embed = discord.Embed(
        title=format_game_title(game),
        url=savant_preview_url(game.game_pk),
        description=_limit_description("\n\n".join(sections)),
        color=ORIOLES_ORANGE,
    )
    embed.set_author(name=format_at_bat_heading(state))
    if state.batter is not None:
        embed.set_thumbnail(url=headshot_url(state.batter.player_id))
    else:
        embed.set_thumbnail(url=team_logo_url(ORIOLES_TEAM_ID))
    if histories is not None and state.pitcher is not None:
        matchups = format_at_bat_matchups(state, histories)
        if matchups:
            embed.add_field(
                name=f"Career vs {state.pitcher.name}",
                value=_limit_field(matchups),
                inline=False,
            )
    count = format_count(state)
    embed.set_footer(
        text=f"{count} • Data: public MLB Stats API"
        if count
        else "Data: public MLB Stats API"
    )
    return embed


def no_live_game_embed(target_date: date) -> discord.Embed:
    embed = discord.Embed(
        title="Orioles on deck",
        description=format_no_live_game(target_date),
        color=ORIOLES_ORANGE,
    )
    embed.set_thumbnail(url=team_logo_url(ORIOLES_TEAM_ID))
    embed.set_footer(text="Data: public MLB Stats API")
    return embed


def bullpen_embed(
    relievers: Sequence[RelieverStatus],
    workload_days: int = BULLPEN_WORKLOAD_DAYS,
) -> discord.Embed:
    """The bullpen graded by recent usage, grouped by how usable each arm is."""
    embed = discord.Embed(
        title="Orioles bullpen",
        description=format_bullpen_window(workload_days),
        color=ORIOLES_ORANGE,
    )
    embed.set_thumbnail(url=team_logo_url(ORIOLES_TEAM_ID))
    if not relievers:
        embed.description = format_no_relievers()
        embed.set_footer(text="Data: public MLB Stats API")
        return embed

    for availability, label in RELIEVER_SECTION_LABELS:
        group = [
            status for status in relievers if status.availability == availability
        ]
        if not group:
            continue
        lines = [format_reliever(status) for status in group]
        for index, chunk in enumerate(_pack_field_values(lines)):
            embed.add_field(
                name=f"{label} ({len(group)})" if index == 0 else BLANK_FIELD_NAME,
                value=chunk,
                inline=False,
            )
    embed.set_footer(text="Data: public MLB Stats API")
    return embed


def pitch_mix_embed(mix: OutingPitchMix, game: GameInfo) -> discord.Embed:
    """One pitcher's outing broken down by pitch type, speed and share."""
    embed = discord.Embed(
        title=format_game_title(game),
        url=savant_preview_url(game.game_pk),
        description=_limit_description(format_pitch_mix_summary(mix)),
        color=ORIOLES_ORANGE,
    )
    embed.set_author(name=f"{mix.pitcher.name} — pitch usage")
    embed.set_thumbnail(url=headshot_url(mix.pitcher.player_id))
    for index, chunk in enumerate(
        _pack_field_values(format_pitch_mix(mix).split("\n"))
    ):
        embed.add_field(
            name="Pitch mix" if index == 0 else BLANK_FIELD_NAME,
            value=chunk,
            inline=False,
        )
    embed.set_footer(text=f"{game.status} • Data: public MLB Stats API")
    return embed


def no_pitches_embed(
    game: GameInfo, player: PlayerRef | None = None
) -> discord.Embed:
    """Nobody has thrown yet, or the pitcher asked about has not appeared."""
    embed = discord.Embed(
        title="Orioles pitch usage",
        description=(
            format_no_pitches(game)
            if player is None
            else format_pitcher_not_in_game(player, game)
        ),
        color=ORIOLES_ORANGE,
    )
    embed.set_thumbnail(url=team_logo_url(ORIOLES_TEAM_ID))
    embed.set_footer(text="Data: public MLB Stats API")
    return embed


def no_pitch_mix_game_embed(target_date: date) -> discord.Embed:
    embed = discord.Embed(
        title="Orioles pitch usage",
        description=format_no_pitch_mix_game(target_date),
        color=ORIOLES_ORANGE,
    )
    embed.set_thumbnail(url=team_logo_url(ORIOLES_TEAM_ID))
    embed.set_footer(text="Data: public MLB Stats API")
    return embed


def injuries_embed(
    players: Sequence[InjuredPlayer], today: date
) -> discord.Embed:
    """The current injured list, grouped by which list each player is on."""
    embed = discord.Embed(
        title="Orioles injured list",
        color=ORIOLES_ORANGE,
    )
    embed.set_thumbnail(url=team_logo_url(ORIOLES_TEAM_ID))
    if not players:
        embed.description = format_no_injuries()
        embed.set_footer(text="Data: public MLB Stats API")
        return embed

    embed.description = _limit_description(format_injury_summary(len(players)))
    for label, group in _injury_groups(players):
        lines = [format_injured_player(player, today) for player in group]
        for index, chunk in enumerate(_pack_field_values(lines)):
            embed.add_field(
                name=f"{label} ({len(group)})" if index == 0 else BLANK_FIELD_NAME,
                value=chunk,
                inline=False,
            )
    embed.set_footer(text="Data: public MLB Stats API")
    return embed


def _injury_groups(
    players: Sequence[InjuredPlayer],
) -> list[tuple[str, list[InjuredPlayer]]]:
    """Split the list by status, shortest stint first.

    The 10-day and 60-day lists mean very different things for a return date,
    so they are worth reading apart rather than as one roll call.
    """
    grouped: dict[str, list[InjuredPlayer]] = {}
    for player in players:
        grouped.setdefault(player.status, []).append(player)
    return sorted(grouped.items(), key=lambda item: (_status_length(item[0]), item[0]))


def _status_length(status: str) -> int:
    """The number of days in a status name, used only to order the sections."""
    match = re.search(r"\d+", status)
    return int(match.group()) if match else 999


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
        name="/ondeck",
        value=(
            "Show who is at bat, on deck, and in the hole in the Orioles game "
            "being played right now, with the count, outs, runners, the "
            "pitcher facing them, and each hitter's career line against him."
        ),
        inline=False,
    )
    embed.add_field(
        name="/bullpen",
        value=(
            "Show which Orioles relievers are available, judged from their "
            "usage over the last few days — who threw today, who is on a "
            "back-to-back, and how much rest everyone else has."
        ),
        inline=False,
    )
    embed.add_field(
        name="/pitchmix [pitcher]",
        value=(
            "Show a pitcher's pitch usage in the game being played now, or the "
            "last one played: how many of each pitch he threw, each as a share "
            "of his pitch count, and today's average speed against his season "
            "average. Defaults to the Orioles arm most recently on the mound."
        ),
        inline=False,
    )
    embed.add_field(
        name="/injuries",
        value=(
            "Show the Orioles injured list: which list each player is on, the "
            "day the stint started, the injury when MLB names it, and any "
            "rehab assignment with how many rehab games he has played."
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
