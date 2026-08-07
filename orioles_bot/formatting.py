from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from zoneinfo import ZoneInfo

from .mlb import savant_player_url, savant_team_matchup_url
from .models import (
    DivisionStandings,
    GameInfo,
    HittingSplit,
    LineupPlayer,
    MatchupAnnotation,
    NextGame,
    ORIOLES_TEAM_NAME,
    PitcherInfo,
    PitchingSplit,
    PlayerRef,
    ScheduleWindow,
    StatsWindow,
    TeamRecord,
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
        f"({window.start:%b %-d} – {window.end:%b %-d, %Y})"
    )


def format_player_heading(player: PlayerRef) -> str:
    name = f"[{player.name}]({savant_player_url(player.player_id)})"
    return f"{name} ({player.position})" if player.position else name


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
    line = (
        f"{rank}. {name} — {record.wins}-{record.losses} "
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
    return moment.strftime("%a %b %-d, %-I:%M %p %Z").strip()


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
    return "\n".join(
        format_standings_row(record, next_games, time_zone)
        for record in standings.teams
    )


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


def _ordinal(rank: str) -> str:
    try:
        number = int(rank)
    except (TypeError, ValueError):
        return rank
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def format_no_standings() -> str:
    return "Standings are unavailable right now. Try again shortly."


def format_schedule_window(window: ScheduleWindow) -> str:
    day_label = "day" if window.days == 1 else "days"
    return (
        f"Next {window.days} {day_label} "
        f"({window.start:%b %-d} – {window.end:%b %-d, %Y})"
    )


def format_no_scheduled_games(window: ScheduleWindow) -> str:
    return (
        f"No {ORIOLES_TEAM_NAME} games are scheduled between "
        f"{window.start:%B %-d} and {window.end:%B %-d, %Y}."
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
    return game.game_date.astimezone(time_zone).strftime("%a, %b %-d")
