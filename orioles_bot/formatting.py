from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .mlb import savant_player_url, savant_team_matchup_url
from .models import (
    DivisionStandings,
    GameInfo,
    HittingSplit,
    InjuredPlayer,
    LineupPlayer,
    MatchupAnnotation,
    MatchupHistory,
    NextGame,
    ORIOLES_TEAM_NAME,
    OutingPitchMix,
    PitcherInfo,
    PitchingGame,
    PitchingSplit,
    PitchTypeUsage,
    PlayerRef,
    AtBatState,
    RECENT_SPLIT_DAYS,
    RehabAssignment,
    RELIEVER_AVAILABLE,
    RELIEVER_CAUTION,
    RELIEVER_UNAVAILABLE,
    RELIEVER_UNKNOWN,
    RelieverStatus,
    RunningProfile,
    ScheduleWindow,
    StatsWindow,
    Substitution,
    TeamRecord,
    TransactionInfo,
    TransactionPlayer,
    WildCardStandings,
)

PAIRING_MARKERS = (
    "🔴",
    "🟠",
    "🟡",
    "🟢",
    "🔵",
    "🟣",
)


def format_moment(moment: date | datetime, layout: str) -> str:
    """``strftime`` with the leading-zero-stripping directives made portable.

    ``%-d`` and ``%-I`` render a day or hour without its leading zero, which is
    what every date in this bot wants: "Aug 6", not "Aug 06". They are a glibc
    extension though, so they work on Linux and macOS and raise ``ValueError``
    on Windows. This bot is written on a Mac, maintained from a PC and deployed
    in a Linux container, so the numbers are substituted in before ``strftime``
    ever sees the layout.

    ``%-I`` and ``%-H`` are only substituted for a ``datetime``, since a plain
    ``date`` has no hour to render.
    """
    layout = layout.replace("%-d", str(moment.day))
    layout = layout.replace("%-m", str(moment.month))
    hour = getattr(moment, "hour", None)
    if hour is not None:
        layout = layout.replace("%-I", str(hour % 12 or 12))
        layout = layout.replace("%-H", str(hour))
    return moment.strftime(layout)


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
    return format_moment(
        game.game_date.astimezone(time_zone), "%a, %b %-d at %-I:%M %p %Z"
    )


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
    return f"{team} starter: {name} ({_pitcher_qualifier(pitcher)})"


def _pitcher_qualifier(pitcher: PitcherInfo) -> str:
    """What goes in the parentheses after a starter's name.

    "RHP" tells the reader something the rest of the line does not, whereas
    "Starting pitcher" only repeats the "starter:" label in front of it. The
    status is the fallback for a pitcher whose hand never came back from the
    API, and an unconfirmed starter keeps his caveat next to the hand so the
    line still reads as a projection rather than a posted lineup.
    """
    hand = HAND_NAMES.get(pitcher.throws or "")
    if hand is None:
        return pitcher.status
    if _is_probable(pitcher.status):
        return f"{hand}, probable"
    return hand


def _is_probable(status: str) -> bool:
    return status.strip().casefold().startswith("probable")


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


def format_at_bat_heading(state: AtBatState) -> str:
    """The half inning and the team hitting in it."""
    return f"{format_half_inning(state)} — {state.batting_team} batting"


def format_half_inning(state: AtBatState) -> str:
    if state.inning is None:
        return "Inning unknown"
    half = state.inning_state or ("Top" if state.is_top_inning else "Bottom")
    return f"{half} {format_ordinal(state.inning)}"


def format_ordinal(value: int) -> str:
    """1st, 2nd, 3rd, 4th — with the teens spelled the way English does."""
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def format_count(state: AtBatState) -> str:
    parts: list[str] = []
    if state.balls is not None and state.strikes is not None:
        parts.append(f"{state.balls}-{state.strikes} count")
    if state.outs is not None:
        out_label = "out" if state.outs == 1 else "outs"
        parts.append(f"{state.outs} {out_label}")
    return ", ".join(parts)


def format_runners(state: AtBatState) -> str:
    runners = state.runners
    if not runners:
        return "Bases empty"
    return "On base: " + ", ".join(
        f"{base} {format_player_link(runner)}" for base, runner in runners
    )


def format_player_link(player: PlayerRef) -> str:
    return f"[{player.name}]({savant_player_url(player.player_id)})"


AT_BAT_SLOT_LABELS = ("At bat", "On deck", "In the hole")


def format_at_bat_slots(state: AtBatState) -> str:
    """The next three hitters, labelled, skipping any MLB has not named yet."""
    lines = [
        f"**{label}** — {format_player_link(player)}"
        for label, player in zip(
            AT_BAT_SLOT_LABELS, (state.batter, state.on_deck, state.in_hole)
        )
        if player is not None
    ]
    return "\n".join(lines)


def format_at_bat_matchups(
    state: AtBatState,
    histories: Mapping[tuple[int, int], MatchupHistory] | None = None,
) -> str:
    """Each upcoming hitter's career line against the pitcher on the mound.

    Named per slot rather than per player so the order matches the slots
    above, and silent when there is no pitcher to have a history against.
    """
    pitcher = state.pitcher
    if pitcher is None:
        return ""

    lines = []
    for label, player in zip(
        AT_BAT_SLOT_LABELS, (state.batter, state.on_deck, state.in_hole)
    ):
        if player is None:
            continue
        history = (histories or {}).get((player.player_id, pitcher.player_id))
        if history is None:
            # Distinct from an empty history: the lookup failed, so claiming
            # there were no plate appearances would overstate what we know.
            detail = "history unavailable right now"
        elif not history.has_history:
            detail = "no prior plate appearances"
        else:
            detail = format_history_line(history)
        lines.append(f"**{label}** {player.name} — {detail}")
    return "\n".join(lines)


def format_at_bat_pitcher(state: AtBatState) -> str:
    if state.pitcher is None:
        return ""
    return f"**Pitching** — {format_player_link(state.pitcher)}"


def format_no_live_game(target_date: date) -> str:
    return (
        f"No {ORIOLES_TEAM_NAME} game is in progress. "
        f"Try /lineup for {format_moment(target_date, '%B %-d, %Y')}."
    )


def format_no_at_bat(game: GameInfo) -> str:
    return (
        f"{game.away_team} at {game.home_team} is underway, but MLB has not "
        "posted the current at-bat yet."
    )


def format_no_games(target_date: date) -> str:
    return (
        f"No {ORIOLES_TEAM_NAME} game is scheduled for "
        f"{format_moment(target_date, '%A, %B %-d, %Y')}."
    )


HAND_NAMES = {"L": "LHP", "R": "RHP", "S": "SHP"}
BAT_SIDE_NAMES = {"L": "L", "R": "R", "S": "S"}


def format_substitution_headline(substitution: Substitution) -> str:
    """One line naming who came in, for whom, and in which role."""
    batter = substitution.batter
    name = f"[{batter.name}]({savant_player_url(batter.player_id)})"
    # A pinch runner is not about to hit, so which side he bats from is noise.
    if batter.bat_side and not substitution.is_pinch_runner:
        name = f"{name} ({BAT_SIDE_NAMES.get(batter.bat_side, batter.bat_side)})"

    if substitution.replaced is not None:
        replaced = (
            f"[{substitution.replaced.name}]"
            f"({savant_player_url(substitution.replaced.player_id)})"
        )
        action = f"{name} replaces {replaced}"
    else:
        action = f"{name} enters"
    role = substitution.role
    # A defensive replacement is defined by where he plays, so name the spot.
    if substitution.is_defensive_substitution and batter.position:
        role = f"{role} at {batter.position}"
    return f"{action} — {role}, batting {_ordinal(substitution.slot)}"


def format_running_profile(profile: RunningProfile | None) -> str:
    """A pinch runner's stolen base record and Statcast speed.

    Either half can be missing on its own, so they are assembled separately
    rather than assuming a runner with no steals also has no speed rating.
    """
    if profile is None or profile.is_empty:
        return "Baserunning data is unavailable right now."

    parts: list[str] = []
    if profile.has_steal_record:
        rate = (
            f" ({format_rate(profile.stolen_base_percentage)})"
            if profile.stolen_base_percentage is not None
            else ""
        )
        parts.append(
            f"{profile.stolen_bases}-for-{profile.attempts} stealing{rate}"
        )
    elif profile.has_steal_line:
        parts.append("No stolen base attempts this season")
    else:
        # Speed came through but the steal lookup did not, so say so rather
        # than let the omission read as "he has never run".
        parts.append("Stolen base record unavailable")

    if profile.sprint_speed is not None:
        speed = f"{profile.sprint_speed:.1f} ft/s sprint speed"
        if profile.bolts:
            speed = f"{speed}, {profile.bolts} bolts"
        parts.append(speed)
    if profile.home_to_first is not None:
        parts.append(f"{profile.home_to_first:.2f}s home to first")

    return " • ".join(parts)


def format_substitution_pitcher(
    pitcher: PitcherInfo | None, *, label: str = "Facing"
) -> str:
    if pitcher is None:
        return f"{label}: pitcher not announced"
    name = (
        f"[{pitcher.name}]({savant_player_url(pitcher.player_id)})"
        if pitcher.player_id is not None
        else pitcher.name
    )
    hand = f" ({HAND_NAMES[pitcher.throws]})" if pitcher.throws in HAND_NAMES else ""
    return f"{label}: {name}{hand}"


def format_matchup_history(
    history: MatchupHistory | None, pitcher: PitcherInfo | None
) -> str:
    """The batter's career line against this pitcher, in box score shorthand."""
    pitcher_name = pitcher.name if pitcher is not None else "this pitcher"
    if history is None:
        # Distinct from an empty history: the lookup failed, so claiming there
        # were no plate appearances would be stating something we do not know.
        return f"Matchup history against {pitcher_name} is unavailable right now."
    if not history.has_history:
        return f"No prior plate appearances against {pitcher_name}."
    return format_history_line(history)


def format_recent_hand_split(
    history: MatchupHistory | None,
    hand: str | None,
    days: int = RECENT_SPLIT_DAYS,
) -> str:
    """The same split as the season one, over the last ``days`` days.

    A season platoon line absorbs a slump that started three weeks ago, so the
    short window is what says whether the batter is in form right now.
    """
    label = HAND_NAMES.get(hand or "", "this hand")
    if history is None:
        # As with a matchup, an unavailable lookup is not a claim about
        # whether the batter has faced this hand lately.
        return f"Recent form against {label} is unavailable right now."
    if not history.has_history:
        return f"No plate appearances against {label} in the last {days} days."
    return format_history_line(history)


def format_history_line(history: MatchupHistory) -> str:
    """A Statcast total as a box score line: hits, extras, then rates."""
    line = f"{history.hits}-for-{history.at_bats}"
    extras = []
    if history.doubles:
        extras.append(f"{history.doubles} 2B")
    if history.triples:
        extras.append(f"{history.triples} 3B")
    if history.home_runs:
        extras.append(f"{history.home_runs} HR")
    if history.walks:
        extras.append(f"{history.walks} BB")
    if history.strikeouts:
        extras.append(f"{history.strikeouts} K")

    rates = f"{format_rate(history.average)} AVG"
    if history.slugging_percentage is not None:
        rates = f"{rates}, {format_rate(history.slugging_percentage)} SLG"

    detail = f"{line}"
    if extras:
        detail = f"{detail} ({', '.join(extras)})"
    return f"{detail} — {rates} in {history.plate_appearances} PA"


def format_platoon_split(split: HittingSplit | None, hand: str | None) -> str:
    """This season's line against the hand the batter is about to see."""
    label = HAND_NAMES.get(hand or "", "this hand")
    if split is None:
        # Distinct from an empty split: the lookup failed, so we cannot say
        # whether there were plate appearances or not.
        return f"Splits against {label} are unavailable right now."
    if not split.plate_appearances:
        return f"No plate appearances against {label} this season."
    slash = (
        f"{format_rate(split.average)}/"
        f"{format_rate(split.on_base_percentage)}/"
        f"{format_rate(split.slugging_percentage)}"
    )
    counting = f"{split.home_runs} HR, {split.walks} BB, {split.strikeouts} K"
    return f"{slash} ({format_rate(split.ops)} OPS) — {counting} in {split.plate_appearances} PA"


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
    return (
        "No Orioles roster transactions found for "
        f"{format_moment(target_date, '%A, %B %-d, %Y')}."
    )


NO_STAT = "—"


def format_rate(value: float | None) -> str:
    """A batting rate in baseball's leading-dot style: .284, not 0.284."""
    if value is None:
        return NO_STAT
    text = f"{value:.3f}"
    if text.startswith("0."):
        return text[1:]
    if text.startswith("-0."):
        return f"-{text[2:]}"
    return text


def format_era(value: float | None) -> str:
    """ERA and WHIP keep their leading zero: 0.89, not .890."""
    if value is None:
        return NO_STAT
    return f"{value:.2f}"


def format_innings(value: float | None) -> str:
    """Innings in baseball's thirds notation, where .1 and .2 mean one and two outs."""
    if value is None:
        return NO_STAT
    whole = int(value)
    outs = int(round((abs(value) - abs(whole)) * 10))
    if outs not in {0, 1, 2}:
        return f"{value:.1f}"
    return f"{whole}.{outs}"


def format_stats_window(window: StatsWindow) -> str:
    day_label = "day" if window.days == 1 else "days"
    return (
        f"Last {window.days} {day_label} "
        f"({format_moment(window.start, '%b %-d')} – "
        f"{format_moment(window.end, '%b %-d, %Y')})"
    )


def format_player_heading(player: PlayerRef) -> str:
    name = f"[{player.name}]({savant_player_url(player.player_id)})"
    return f"{name} ({player.position})" if player.position else name


RELIEVER_MARKERS = {
    RELIEVER_AVAILABLE: "🟢",
    RELIEVER_CAUTION: "🟡",
    RELIEVER_UNAVAILABLE: "🔴",
    RELIEVER_UNKNOWN: "⚪",
}
RELIEVER_SECTION_LABELS = (
    (RELIEVER_AVAILABLE, "Available"),
    (RELIEVER_CAUTION, "Use with caution"),
    (RELIEVER_UNAVAILABLE, "Unavailable"),
    (RELIEVER_UNKNOWN, "Workload unknown"),
)


def format_reliever(status: RelieverStatus) -> str:
    """One bullpen line: the read, then the usage it was read from."""
    marker = RELIEVER_MARKERS.get(status.availability, "⚪")
    line = f"{marker} {format_player_heading(status.player)} — {status.reason}"
    usage = format_reliever_usage(status)
    return f"{line}\n{usage}" if usage else line


def format_reliever_usage(status: RelieverStatus) -> str:
    if not status.outings:
        return ""
    return "Recent: " + "; ".join(
        format_reliever_outing(outing) for outing in status.outings
    )


def format_reliever_outing(outing: PitchingGame) -> str:
    location = "vs" if outing.is_home else "at"
    stat = outing.stat
    details = [f"{format_innings(stat.innings_pitched)} IP"]
    if stat.pitches:
        details.append(f"{stat.pitches} P")
    if stat.batters_faced:
        details.append(f"{stat.batters_faced} BF")
    return (
        f"{format_moment(outing.game_date, '%b %-d')} {location} {outing.opponent} "
        f"({', '.join(details)})"
    )


def format_injured_player(player: InjuredPlayer, today: date) -> str:
    """One injury list line: who, since when, and whatever is known since."""
    heading = format_player_heading(player.player)
    lines = [f"🏥 {heading} — {player.status}"]
    placed = format_injury_placement(player, today)
    if placed:
        lines.append(placed)
    if player.injury_note:
        lines.append(f"Injury: {player.injury_note}")
    rehab = format_rehab(player.rehab)
    if rehab:
        lines.append(rehab)
    update = format_injury_update(player)
    if update:
        lines.append(update)
    return "\n".join(lines)


def format_injury_placement(player: InjuredPlayer, today: date) -> str:
    """When the stint started, and how long ago that was.

    A backdated placement shows both dates, since the retroactive one decides
    when he is eligible to come back but the announcement is the day the news
    broke.
    """
    effective = player.effective_date
    if effective is None:
        return "Placed on the IL: date unavailable"

    text = f"On the IL since {format_moment(effective, '%b %-d, %Y')}"
    if player.retroactive_to is not None and player.placed_on is not None:
        if player.retroactive_to != player.placed_on:
            text += (
                f" (retroactive; announced "
                f"{format_moment(player.placed_on, '%b %-d')})"
            )
    days = player.days_out(today)
    if days is not None:
        day_label = "day" if days == 1 else "days"
        text += f" — {days} {day_label}"
    return text


def format_rehab(rehab: RehabAssignment | None) -> str:
    if rehab is None:
        return ""
    where = f" with {rehab.team_name}" if rehab.team_name else ""
    text = (
        f"Rehab assignment{where} since "
        f"{format_moment(rehab.started, '%b %-d')}"
    )
    if not rehab.games_known:
        return f"{text} — games played unavailable"
    games = rehab.games
    game_label = "game" if games == 1 else "games"
    text += f" — {games} rehab {game_label}"
    last = rehab.last_game
    if last is not None:
        text += f", last {format_moment(last, '%b %-d')}"
    return text


def format_injury_update(player: InjuredPlayer) -> str:
    """The newest roster move naming him, when it says more than the placement.

    MLB's transaction feed is the only status news the Stats API carries, so a
    transfer to the 60-day list or the start of a rehab stint is as close to a
    return timetable as this can get.
    """
    update = player.latest_update
    if not update:
        return ""
    if player.placed_on is not None and player.latest_update_date == player.placed_on:
        return ""
    when = (
        f"{format_moment(player.latest_update_date, '%b %-d')}: "
        if player.latest_update_date is not None
        else ""
    )
    return f"Latest: {when}{update}"


def format_injury_summary(count: int) -> str:
    player_label = "player" if count == 1 else "players"
    return (
        f"{count} {player_label} on the injured list. Dates, injuries and rehab "
        "assignments come from MLB's transaction feed; MLB publishes no injury "
        "report through the Stats API."
    )


def format_no_injuries() -> str:
    return "No Orioles are on the injured list."


def format_bullpen_window(workload_days: int) -> str:
    day_label = "day" if workload_days == 1 else "days"
    return (
        "Availability is inferred from each reliever's usage over the last "
        f"{workload_days} {day_label}. MLB publishes no availability list."
    )


def format_no_relievers() -> str:
    return "No relievers found on the active roster."


def format_pitch_mix(mix: OutingPitchMix) -> str:
    """The pitch mix as one line per pitch type, most thrown first."""
    shares = mix.shares()
    return "\n".join(
        format_pitch_usage(usage, share)
        for usage, share in zip(mix.pitches, shares)
    )


def format_pitch_usage(usage: PitchTypeUsage, share: int) -> str:
    """One pitch type: how often it was thrown, and how hard versus the season."""
    line = f"**{usage.name}** — {usage.count} ({share}%)"
    if usage.average_speed is None:
        return f"{line} · speed not tracked"
    return f"{line} · {format_speed(usage.average_speed)} {format_velocity_delta(usage)}"


def format_velocity_delta(usage: PitchTypeUsage) -> str:
    """The gap to the season average, or a note that there is no baseline."""
    delta = usage.velocity_delta
    if delta is None or usage.season_average_speed is None:
        return "(no season average)"
    return (
        f"({delta:+.1f} vs season {format_speed(usage.season_average_speed)})"
    )


def format_speed(value: float | None) -> str:
    """A pitch speed the way a broadcast reads it: one decimal, in mph."""
    if value is None:
        return NO_STAT
    return f"{value:.1f} mph"


def format_pitch_mix_summary(mix: OutingPitchMix) -> str:
    """The header line: who threw how many pitches, and against what baseline."""
    pitch_label = "pitch" if mix.total_pitches == 1 else "pitches"
    parts = [f"{mix.total_pitches} {pitch_label}"]
    if mix.batters_faced:
        parts.append(f"{mix.batters_faced} BF")
    type_count = len(mix.pitches)
    type_label = "pitch type" if type_count == 1 else "pitch types"
    parts.append(f"{type_count} {type_label}")
    summary = f"{format_player_heading(mix.pitcher)} — {', '.join(parts)}"
    if mix.has_baseline and mix.baseline_season is not None:
        return f"{summary}\nSpeeds compared with his {mix.baseline_season} season averages."
    return f"{summary}\nNo season averages available to compare speeds against."


def format_no_pitches(game: GameInfo) -> str:
    return (
        "No pitches have been thrown yet in "
        f"{format_game_title(game)}."
    )


def format_pitcher_not_in_game(player: PlayerRef, game: GameInfo) -> str:
    return f"{player.name} has not pitched in {format_game_title(game)}."


def format_no_pitch_mix_game(target_date: date) -> str:
    return (
        "No Orioles game has been played yet on "
        f"{format_moment(target_date, '%A, %B %-d, %Y')}. Try /schedule for what "
        "is coming up."
    )


def format_hitting_split(split: HittingSplit) -> str:
    slash = (
        f"{format_rate(split.average)}/"
        f"{format_rate(split.on_base_percentage)}/"
        f"{format_rate(split.slugging_percentage)}"
    )
    return (
        f"**Hitting** — {split.games} G, {split.plate_appearances} PA\n"
        f"{slash} (OPS {format_rate(split.ops)})\n"
        f"{split.hits} H, {split.doubles} 2B, {split.triples} 3B, "
        f"{split.home_runs} HR, {split.rbi} RBI, {split.runs} R\n"
        f"{split.walks} BB, {split.strikeouts} K, {split.stolen_bases} SB"
    )


def format_pitching_split(split: PitchingSplit) -> str:
    record = f"{split.wins}-{split.losses}"
    if split.saves:
        record = f"{record}, {split.saves} SV"
    return (
        f"**Pitching** — {split.games} G ({split.games_started} GS), "
        f"{format_innings(split.innings_pitched)} IP\n"
        f"{record}, {format_era(split.era)} ERA, {format_era(split.whip)} WHIP\n"
        f"{split.hits} H, {split.runs} R, {split.earned_runs} ER, "
        f"{split.home_runs} HR\n"
        f"{split.walks} BB, {split.strikeouts} K"
    )


def format_pitching_game(game: PitchingGame) -> str:
    stat = game.stat
    return (
        f"{format_innings(stat.innings_pitched)} IP, "
        f"{stat.hits} H, {stat.runs} R, {stat.earned_runs} ER, "
        f"{stat.walks} BB, {stat.strikeouts} K"
    )


def format_no_player_stats(player: PlayerRef, window: StatsWindow) -> str:
    return (
        f"{format_player_heading(player)} has no recorded games in the "
        f"last {window.days} days."
    )


def format_player_not_found(query: str) -> str:
    return f"No player found for “{query.strip()}”. Try a full name, like Adley Rutschman."


# MLB sends "-" for a team with no deficit; a leader should read as even, not
# as missing data.
NO_GAMES_BACK = {"-", "–", "—", "+0.0", "0.0", "0"}
# Mirrors the states MlbClient treats as played, so a finished game renders as a
# result rather than a preview.
FINISHED_STATUSES = {"final", "game over", "completed early", "completed"}


def format_games_back(value: str | None) -> str:
    if value is None:
        return NO_STAT
    text = value.strip()
    if not text:
        return NO_STAT
    return "—" if text in NO_GAMES_BACK else text


def format_streak(value: str | None) -> str:
    text = (value or "").strip()
    return text or NO_STAT


def format_run_differential(value: int | None) -> str:
    if value is None:
        return NO_STAT
    return f"+{value}" if value > 0 else str(value)


def format_standings_row(
    record: TeamRecord,
    next_games: Mapping[int, NextGame] | None = None,
    time_zone: ZoneInfo | None = None,
    pairing_marker: str | None = None,
) -> str:
    """One division line: rank, team, record, GB, streak, and the next game.

    The Orioles row is bolded so the team the bot exists for is findable at a
    glance, and a clinch letter is appended only when the API reports one.
    """
    rank = record.division_rank or "-"
    name = record.team_name
    if record.clinch_indicator:
        name = f"{name} ({record.clinch_indicator})"
    if record.is_orioles:
        name = f"**{name}**"
    marker = f"{pairing_marker} " if pairing_marker else ""
    line = (
        f"{marker}{rank}. {name} — {record.wins}-{record.losses} "
        f"({format_rate_text(record.winning_percentage)}), "
        f"GB {format_games_back(record.games_back)}, "
        f"{format_streak(record.streak)}"
    )

    next_game = (next_games or {}).get(record.team_id)
    if next_game is None:
        return line
    return f"{line}\n{format_next_game(next_game, time_zone)}"


def format_next_game(
    next_game: NextGame, time_zone: ZoneInfo | None = None
) -> str:
    """A team's upcoming opponent, abbreviated to keep the row scannable."""
    opponent = next_game.opponent_abbreviation or next_game.opponent
    location = "vs" if next_game.is_home else "@"
    when = format_next_game_time(next_game, time_zone)
    status = next_game.status.strip()
    if status and status.casefold() not in {"scheduled", "pre-game", "warmup"}:
        return f"↳ Next: {location} {opponent} — {when} ({status})"
    return f"↳ Next: {location} {opponent} — {when}"


def format_next_game_time(
    next_game: NextGame, time_zone: ZoneInfo | None = None
) -> str:
    if next_game.game_date is None:
        return "time TBD"
    moment = next_game.game_date
    if time_zone is not None:
        moment = moment.astimezone(time_zone)
    return format_moment(moment, "%a %b %-d, %-I:%M %p %Z").strip()


def format_rate_text(value: str | None) -> str:
    """A winning percentage as MLB sends it, already leading-dot styled."""
    text = (value or "").strip()
    if not text:
        return NO_STAT
    return text[1:] if text.startswith("0.") else text


def format_standings(
    standings: DivisionStandings,
    next_games: Mapping[int, NextGame] | None = None,
    time_zone: ZoneInfo | None = None,
) -> str:
    if not standings.teams:
        return "Standings are not available yet."
    pairing_markers = _pairing_markers(standings.teams, next_games)
    lines = [
        format_standings_row(
            record,
            next_games,
            time_zone,
            pairing_markers.get(record.team_id),
        )
        for record in standings.teams
    ]
    if pairing_markers:
        lines.insert(0, "Pairings: same colored dot = next opponent")
    return "\n".join(lines)


def format_orioles_standing(standings: DivisionStandings) -> str | None:
    """A one-line summary of where the Orioles sit, for the embed footer."""
    orioles = next(
        (record for record in standings.teams if record.is_orioles), None
    )
    if orioles is None:
        return None
    parts = [f"{ORIOLES_TEAM_NAME}: {orioles.wins}-{orioles.losses}"]
    if orioles.division_rank:
        parts.append(f"{_ordinal(orioles.division_rank)} in {standings.division_name}")
    wild_card = format_games_back(orioles.wild_card_games_back)
    if wild_card != NO_STAT:
        parts.append(f"wild card {wild_card}")
    parts.append(f"run diff {format_run_differential(orioles.run_differential)}")
    return " • ".join(parts)


def _ordinal(rank: str | int) -> str:
    """Render a rank as "1st", "2nd" and so on.

    Takes standings ranks, which arrive as strings and are passed through
    unchanged when they are not numeric, as well as batting order slots, which
    are already integers.
    """
    try:
        number = int(rank)
    except (TypeError, ValueError):
        return str(rank)
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def format_no_standings() -> str:
    return "Standings are unavailable right now. Try again shortly."


# The American League awards three wild card berths, so the playoff line falls
# after the third team in the race.
WILD_CARD_SPOTS = 3
PLAYOFF_LINE = "───────── playoff line ─────────"


def format_wild_card_row(
    record: TeamRecord,
    next_games: Mapping[int, NextGame] | None = None,
    time_zone: ZoneInfo | None = None,
    pairing_marker: str | None = None,
) -> str:
    """One wild card line, showing games up on or back of the last berth."""
    rank = record.wild_card_rank or "-"
    name = record.team_name
    if record.clinch_indicator:
        name = f"{name} ({record.clinch_indicator})"
    if record.is_orioles:
        name = f"**{name}**"
    marker = f"{pairing_marker} " if pairing_marker else ""
    line = (
        f"{marker}{rank}. {name} — {record.wins}-{record.losses} "
        f"({format_rate_text(record.winning_percentage)}), "
        f"{format_wild_card_gap(record)}, "
        f"{format_streak(record.streak)}"
    )

    next_game = (next_games or {}).get(record.team_id)
    if next_game is None:
        return line
    return f"{line}\n{format_next_game(next_game, time_zone)}"


def format_wild_card_gap(record: TeamRecord) -> str:
    """Games ahead of or behind the last wild card berth.

    MLB reports a team holding a berth with a leading ``+``, meaning games up on
    the first team outside the picture, and everyone else as games back.
    """
    raw = (record.wild_card_games_back or "").strip()
    if raw.startswith("+"):
        return f"{raw} up"
    gap = format_games_back(record.wild_card_games_back)
    if gap == NO_STAT:
        return "even with the line" if record.wild_card_leader else NO_STAT
    return f"{gap} GB"


def format_wild_card(
    standings: WildCardStandings,
    next_games: Mapping[int, NextGame] | None = None,
    time_zone: ZoneInfo | None = None,
) -> str:
    """The wild card race with a divider drawn after the final berth."""
    if not standings.teams:
        return "Wild card standings are not available yet."

    pairing_markers = _pairing_markers(standings.teams, next_games)
    lines: list[str] = []
    line_drawn = False
    for index, record in enumerate(standings.teams):
        if not line_drawn and _is_below_the_line(record, index):
            lines.append(PLAYOFF_LINE)
            line_drawn = True
        lines.append(
            format_wild_card_row(
                record,
                next_games,
                time_zone,
                pairing_markers.get(record.team_id),
            )
        )
    if pairing_markers:
        lines.insert(0, "Pairings: same colored dot = next opponent")
    return "\n".join(lines)


def _pairing_markers(
    records: tuple[TeamRecord, ...],
    next_games: Mapping[int, NextGame] | None,
) -> dict[int, str]:
    """Assign the same colored marker to teams sharing the next game."""
    if not next_games:
        return {}

    record_ids = {record.team_id for record in records}
    pairs = sorted(
        {
            tuple(sorted((record.team_id, next_game.opponent_team_id)))
            for record in records
            if (next_game := next_games.get(record.team_id)) is not None
            and next_game.opponent_team_id in record_ids
            and next_game.opponent_team_id != record.team_id
        }
    )
    markers: dict[int, str] = {}
    for index, pair in enumerate(pairs):
        marker = PAIRING_MARKERS[index % len(PAIRING_MARKERS)]
        markers[pair[0]] = marker
        markers[pair[1]] = marker
    return markers


def _is_below_the_line(record: TeamRecord, index: int) -> bool:
    """Whether a team sits outside the wild card berths.

    The API's own ``wildCardLeader`` flag is preferred, since it already
    accounts for ties, and position is only used when the flag is absent.
    """
    rank = _safe_rank(record.wild_card_rank)
    if rank is not None:
        return rank > WILD_CARD_SPOTS
    if record.wild_card_leader:
        return False
    return index >= WILD_CARD_SPOTS


def _safe_rank(value: str | None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def format_orioles_wild_card(standings: WildCardStandings) -> str | None:
    """A one-line summary of the Orioles' position in the race, for the footer."""
    orioles = next(
        (record for record in standings.teams if record.is_orioles), None
    )
    if orioles is None:
        return None
    parts = [f"{ORIOLES_TEAM_NAME}: {orioles.wins}-{orioles.losses}"]
    if orioles.wild_card_rank:
        parts.append(f"{_ordinal(orioles.wild_card_rank)} in the AL wild card")
    parts.append(format_wild_card_gap(orioles))
    return " • ".join(parts)


def format_schedule_window(window: ScheduleWindow) -> str:
    day_label = "day" if window.days == 1 else "days"
    return (
        f"Next {window.days} {day_label} "
        f"({format_moment(window.start, '%b %-d')} – "
        f"{format_moment(window.end, '%b %-d, %Y')})"
    )


def format_no_scheduled_games(window: ScheduleWindow) -> str:
    return (
        f"No {ORIOLES_TEAM_NAME} games are scheduled between "
        f"{format_moment(window.start, '%B %-d')} and "
        f"{format_moment(window.end, '%B %-d, %Y')}."
    )


def format_schedule_entry(game: GameInfo, time_zone: ZoneInfo) -> str:
    """One scheduled game: opponent, home/away, start time, and probables.

    A game that has already finished shows its score instead of its probable
    starters, since "Probable pitcher" reads as wrong once the game is over.
    """
    location = "vs" if game.is_home else "@"
    lines = [f"**{location} {game.opponent}** — {format_game_time(game, time_zone)}"]

    finished = game.status.strip().casefold() in FINISHED_STATUSES
    if game.orioles_score is not None and game.opponent_score is not None:
        lines.append(
            f"{game.status}: Orioles {game.orioles_score}, "
            f"{game.opponent} {game.opponent_score}"
        )
    elif game.status and game.status != "Scheduled":
        lines.append(f"Status: {game.status}")

    if not finished:
        lines.extend([format_pitcher(game), format_opponent_pitcher(game)])
    return "\n".join(lines)


def format_schedule_day(game: GameInfo, time_zone: ZoneInfo) -> str:
    if game.game_date is None:
        return "Date TBD"
    return format_moment(game.game_date.astimezone(time_zone), "%a, %b %-d")
