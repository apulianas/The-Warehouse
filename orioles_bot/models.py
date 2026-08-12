from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime


ORIOLES_TEAM_ID = 110
ORIOLES_TEAM_NAME = "Baltimore Orioles"
AMERICAN_LEAGUE_ID = 103
AL_EAST_DIVISION_ID = 201
# Statuses that mean first pitch has not happened yet.
PREGAME_GAME_STATES = frozenset(
    {
        "scheduled",
        "pre-game",
        "pregame",
        "warmup",
        "delayed start",
    }
)
# Statuses that mean the game is behind us.
FINISHED_GAME_STATES = frozenset(
    {"final", "game over", "completed early", "completed"}
)
# Statuses that mean the game will not be played, at least not now.
UNPLAYED_GAME_STATES = frozenset(
    {"postponed", "cancelled", "canceled", "suspended"}
)
# `codedGameState` values for the same. Note "T" is deliberately absent: it
# covers the suspended family but also "Scheduled: COVID-19", so it is
# ambiguous. The detailed state disambiguates those, and is checked first.
UNPLAYED_GAME_CODES = frozenset({"D", "C", "U"})
# MLB records the role a substitute entered in as the first position it lists
# for him, using these two pseudo-positions.
PINCH_HITTER_POSITION = "PH"
PINCH_RUNNER_POSITION = "PR"
SUBSTITUTION_ROLE_HITTER = "pinch hitter"
SUBSTITUTION_ROLE_RUNNER = "pinch runner"
SUBSTITUTION_ROLE_FIELDER = "defensive substitution"
SUBSTITUTION_ROLE_UNKNOWN = "substitution"
# How far back a substitute's recent-form split reaches. Long enough to gather
# a readable sample against one hand, short enough that a slump the season
# split has absorbed still shows.
RECENT_SPLIT_DAYS = 14
# Statcast filters on a literal pitching hand, so a switch pitcher or an
# unannounced arm has no recent-form split to fetch or show.
RECENT_SPLIT_HANDS = frozenset({"L", "R"})


def normalize_game_state(status: str | None) -> str:
    """Reduce a `detailedState` to its bare state.

    MLB appends the reason to several states, so a rain delay arrives as
    "Delayed: Rain" and a wet postponement as "Postponed: Rain". Dropping the
    suffix keeps set membership working no matter the weather.
    """
    return str(status or "").split(":")[0].strip().casefold()


@dataclass(frozen=True)
class LineupPlayer:
    player_id: int
    name: str
    position: str
    batting_order: int
    headshot_url: str
    # 0 for a starter, then 1 for the first replacement in that lineup slot, 2
    # for the next, and so on. The boxscore encodes this as slot * 100 + n.
    substitution_order: int = 0
    bat_side: str | None = None
    # The position the player first appeared at, which is how MLB records the
    # role they entered in: "PH" for a pinch hitter, "PR" for a pinch runner,
    # or a fielding position for a defensive replacement.
    entry_position: str | None = None

    @property
    def is_substitute(self) -> bool:
        return self.substitution_order > 0


@dataclass(frozen=True)
class PitcherInfo:
    player_id: int | None
    name: str
    headshot_url: str | None = None
    status: str = "Probable"
    throws: str | None = None


@dataclass(frozen=True)
class MatchupAnnotation:
    emoji: str
    metric_name: str
    metric_value: float
    plate_appearances: int


@dataclass(frozen=True)
class MatchupHistory:
    """A batter's complete Statcast history against one pitcher."""

    plate_appearances: int = 0
    at_bats: int = 0
    hits: int = 0
    doubles: int = 0
    triples: int = 0
    home_runs: int = 0
    walks: int = 0
    strikeouts: int = 0
    average: float | None = None
    slugging_percentage: float | None = None
    woba: float | None = None
    # Kept as the raw float the CSV reports so the hot/cold threshold compares
    # against the same denominator it always has.
    woba_denominator: float = 0.0

    @property
    def has_history(self) -> bool:
        return self.plate_appearances > 0


@dataclass(frozen=True)
class RunningProfile:
    """What a pinch runner was brought in to do.

    Stolen bases come from the season hitting stats, sprint speed from
    Statcast. Either source can be missing on its own: a September callup may
    have no steals yet, and Statcast only rates players with enough tracked
    runs, so both halves render independently.
    """

    stolen_bases: int | None = None
    caught_stealing: int | None = None
    stolen_base_percentage: float | None = None
    # Feet per second on a player's fastest competitive runs, and the number of
    # those runs at 30 ft/s or better, which Statcast calls a "bolt".
    sprint_speed: float | None = None
    bolts: int | None = None
    home_to_first: float | None = None

    @property
    def attempts(self) -> int:
        return (self.stolen_bases or 0) + (self.caught_stealing or 0)

    @property
    def has_steal_line(self) -> bool:
        """True when MLB actually returned a stat line.

        A callup with no season stats is different from a player who has
        simply not run yet, and only the second is safe to state as fact.
        """
        return self.stolen_bases is not None or self.caught_stealing is not None

    @property
    def has_steal_record(self) -> bool:
        return self.attempts > 0

    @property
    def has_speed(self) -> bool:
        return self.sprint_speed is not None

    @property
    def is_empty(self) -> bool:
        return not self.has_steal_line and not self.has_speed


@dataclass(frozen=True)
class Substitution:
    """A player who entered the game in another player's lineup slot."""

    game_pk: int
    slot: int
    batter: LineupPlayer
    replaced: LineupPlayer | None
    pitcher: PitcherInfo | None
    is_orioles: bool
    batting_team: str
    batting_team_id: int | None

    @property
    def role(self) -> str:
        """How the player entered: pinch hitting, pinch running, or fielding.

        A pinch runner is not about to bat, so the card shows what he was
        brought in to do rather than how he hits the pitcher. When the
        boxscore has not recorded a position yet the role stays generic, since
        guessing "defensive substitution" would state something unknown.
        """
        entry = (self.batter.entry_position or "").strip().upper()
        if not entry:
            return SUBSTITUTION_ROLE_UNKNOWN
        if entry == PINCH_RUNNER_POSITION:
            return SUBSTITUTION_ROLE_RUNNER
        if entry == PINCH_HITTER_POSITION:
            return SUBSTITUTION_ROLE_HITTER
        return SUBSTITUTION_ROLE_FIELDER

    @property
    def is_pinch_runner(self) -> bool:
        return self.role == SUBSTITUTION_ROLE_RUNNER

    @property
    def is_defensive_substitution(self) -> bool:
        return self.role == SUBSTITUTION_ROLE_FIELDER

    @property
    def key(self) -> str:
        """Stable across polls: the same sub never announces twice."""
        return (
            f"{self.game_pk}:{self.slot}:"
            f"{self.batter.substitution_order}:{self.batter.player_id}"
        )


@dataclass(frozen=True)
class GameInfo:
    game_pk: int
    game_date: datetime | None
    status: str
    venue: str
    home_team: str
    home_team_id: int | None
    away_team: str
    opponent: str
    opponent_team_id: int | None
    is_home: bool
    orioles_score: int | None
    opponent_score: int | None
    pitcher: PitcherInfo | None
    opponent_pitcher: PitcherInfo | None
    lineup: tuple[LineupPlayer, ...]
    opponent_lineup: tuple[LineupPlayer, ...]
    # The batting orders as originally posted. Substitutions change `lineup`
    # but never these, so they are what announcements are keyed on.
    starting_lineup: tuple[LineupPlayer, ...] = ()
    opponent_starting_lineup: tuple[LineupPlayer, ...] = ()
    # The arm on the mound right now, which is the starter until the first
    # pitching change. Substitutions are judged against these, not the starters.
    current_pitcher: PitcherInfo | None = None
    current_opponent_pitcher: PitcherInfo | None = None
    substitutions: tuple[Substitution, ...] = ()
    # `abstractGameState` ("Preview", "Live", "Final") and `codedGameState`.
    # These are single stable tokens, whereas `status` is the display string
    # that carries the reason for a delay. Optional so callers that only know
    # the display status still get sensible answers from the fallbacks below.
    abstract_status: str = ""
    coded_status: str = ""

    @property
    def is_unplayed(self) -> bool:
        """Postponed, cancelled or suspended, so nothing more happens today.

        The detailed state decides, because it is the only field that separates
        a suspension from a game still in progress: MLB reports both suspended
        families ("T" and "U") as abstract "Live". Postponements and
        cancellations report abstract "Final", hence not deferring to that
        either. The coded state is only a backstop for a missing detailed one.
        """
        if normalize_game_state(self.status) in UNPLAYED_GAME_STATES:
            return True
        return self.coded_status.strip().upper()[:1] in UNPLAYED_GAME_CODES

    @property
    def has_started(self) -> bool:
        """True once first pitch has happened, and still true afterwards.

        Before first pitch a changed batting order is a corrected lineup card,
        which is worth reposting in full. After it, a change is a substitution.
        """
        if self.is_unplayed:
            return False
        # Checked before the abstract state because MLB calls warmup "Live"
        # even though the game has not begun.
        if normalize_game_state(self.status) in PREGAME_GAME_STATES:
            return False
        abstract = self.abstract_status.strip().casefold()
        if abstract:
            return abstract in {"live", "final"}
        return True

    @property
    def is_final(self) -> bool:
        """True once the game is over, or has been called off."""
        if self.is_unplayed:
            return True
        if normalize_game_state(self.status) in PREGAME_GAME_STATES:
            return False
        abstract = self.abstract_status.strip().casefold()
        if abstract:
            return abstract == "final"
        return normalize_game_state(self.status) in FINISHED_GAME_STATES

    @property
    def is_in_progress(self) -> bool:
        """Being played right now, which is when updates are worth chasing.

        A rain delay after first pitch still counts: MLB keeps the game "Live",
        play can resume at any moment, and substitutions often follow one.
        """
        return self.has_started and not self.is_final


@dataclass(frozen=True)
class TransactionPlayer:
    player_id: int
    name: str


# Wording MLB uses when a move puts a player on the roster rather than takes
# him off it. Roster moves come in matched sets, and the arriving player is the
# news, so a card covering several moves leads with his face.
ARRIVAL_TRANSACTION_PHRASES = (
    "recalled",
    "activated",
    "reinstated",
    "called up",
    "selected the contract",
    "purchased the contract",
    "claimed off waivers",
    "signed",
)
# The other direction. Checked only after the arrival phrases, so activating a
# player off the injured list is not read as a departure by "injured list".
DEPARTURE_TRANSACTION_PHRASES = (
    "optioned",
    "designated for assignment",
    "released",
    "outright",
    "outrighted",
    "injured list",
    "restricted list",
    "bereavement list",
    "paternity list",
    "granted free agency",
    "non-tendered",
    "placed on waivers",
)


def _phrase_pattern(phrases: tuple[str, ...]) -> re.Pattern[str]:
    """Whole words only: "assigned" and "reassigned" both contain "signed"."""
    return re.compile(r"\b(?:%s)\b" % "|".join(re.escape(item) for item in phrases))


ARRIVAL_TRANSACTION_PATTERN = _phrase_pattern(ARRIVAL_TRANSACTION_PHRASES)
DEPARTURE_TRANSACTION_PATTERN = _phrase_pattern(DEPARTURE_TRANSACTION_PHRASES)


@dataclass(frozen=True)
class TransactionInfo:
    transaction_id: str
    date: date
    player_id: int | None
    player_name: str | None
    type_description: str
    description: str
    headshot_url: str | None
    players: tuple[TransactionPlayer, ...] = ()

    @property
    def _searchable(self) -> str:
        return f"{self.type_description} {self.description}".casefold()

    @property
    def is_arrival(self) -> bool:
        """Whether this move adds a player to the roster."""
        return ARRIVAL_TRANSACTION_PATTERN.search(self._searchable) is not None

    @property
    def is_departure(self) -> bool:
        """Whether this move takes a player off the roster.

        A trade is neither: it names both directions at once, so claiming it
        for either side would misread it.
        """
        if self.is_arrival:
            return False
        return DEPARTURE_TRANSACTION_PATTERN.search(self._searchable) is not None


@dataclass(frozen=True)
class StatsWindow:
    """An inclusive day range used for rolling "last N days" stat queries."""

    days: int
    start: date
    end: date


@dataclass(frozen=True)
class PlayerRef:
    player_id: int
    name: str
    position: str | None = None


@dataclass(frozen=True)
class HittingSplit:
    games: int = 0
    plate_appearances: int = 0
    at_bats: int = 0
    runs: int = 0
    hits: int = 0
    doubles: int = 0
    triples: int = 0
    home_runs: int = 0
    rbi: int = 0
    walks: int = 0
    strikeouts: int = 0
    stolen_bases: int = 0
    average: float | None = None
    on_base_percentage: float | None = None
    slugging_percentage: float | None = None
    ops: float | None = None


@dataclass(frozen=True)
class PitchingSplit:
    games: int = 0
    games_started: int = 0
    wins: int = 0
    losses: int = 0
    saves: int = 0
    innings_pitched: float | None = None
    hits: int = 0
    runs: int = 0
    earned_runs: int = 0
    home_runs: int = 0
    walks: int = 0
    strikeouts: int = 0
    era: float | None = None
    whip: float | None = None
    pitches: int = 0
    batters_faced: int = 0


@dataclass(frozen=True)
class PitchingGame:
    game_date: date
    opponent: str
    is_home: bool
    result: str | None
    stat: PitchingSplit
    decision: str = "No decision"
    game_pk: int | None = None
    team_id: int | None = None
    team_score: int | None = None
    opponent_score: int | None = None


# How far back a reliever's game log is pulled. Long enough to tell a starter
# from a reliever by how he has been used, short enough to stay one request.
BULLPEN_LOG_DAYS = 30
# Only the last few days shape availability: a bullpen arm is judged on the
# work he has not yet recovered from, not on the whole month.
BULLPEN_WORKLOAD_DAYS = 3
# A back-to-back outing this heavy is what usually costs a reliever the day.
BULLPEN_HEAVY_PITCHES = 30
BULLPEN_HEAVY_INNINGS = 2.0

RELIEVER_AVAILABLE = "available"
RELIEVER_CAUTION = "caution"
RELIEVER_UNAVAILABLE = "unavailable"
RELIEVER_UNKNOWN = "unknown"


@dataclass(frozen=True)
class RelieverStatus:
    """One bullpen arm, with the recent work behind his availability read.

    ``availability`` is a judgement from the game log, not an official status:
    MLB publishes no availability feed, so recent workload is the only public
    signal for who can pitch tonight.
    """

    player: PlayerRef
    availability: str
    reason: str
    outings: tuple[PitchingGame, ...] = ()
    days_rest: int | None = None

    @property
    def last_outing(self) -> PitchingGame | None:
        return self.outings[0] if self.outings else None

    @property
    def is_available(self) -> bool:
        return self.availability == RELIEVER_AVAILABLE


@dataclass(frozen=True)
class ScheduleWindow:
    """An inclusive day range covering the next N days, today included."""

    days: int
    start: date
    end: date


@dataclass(frozen=True)
class NextGame:
    """A team's upcoming game, used to annotate a standings row."""

    team_id: int
    opponent: str
    opponent_abbreviation: str | None
    opponent_team_id: int | None
    is_home: bool
    game_date: datetime | None
    status: str


@dataclass(frozen=True)
class TeamRecord:
    team_id: int
    team_name: str
    wins: int
    losses: int
    winning_percentage: str | None = None
    division_rank: str | None = None
    games_back: str | None = None
    wild_card_games_back: str | None = None
    streak: str | None = None
    run_differential: int | None = None
    division_leader: bool = False
    clinch_indicator: str | None = None
    wild_card_rank: str | None = None
    wild_card_leader: bool = False

    @property
    def is_orioles(self) -> bool:
        return self.team_id == ORIOLES_TEAM_ID


@dataclass(frozen=True)
class WildCardStandings:
    """The wild card race for one league, ordered by wild card rank.

    Division leaders are excluded by the API, since they hold a playoff spot
    outright and are not chasing one.
    """

    league_id: int | None
    league_name: str
    teams: tuple[TeamRecord, ...]
    season: str | None = None


@dataclass(frozen=True)
class DivisionStandings:
    division_id: int | None
    division_name: str
    teams: tuple[TeamRecord, ...]
    season: str | None = None
