from __future__ import annotations

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
class Substitution:
    """A hitter who entered the game in another hitter's lineup slot."""

    game_pk: int
    slot: int
    batter: LineupPlayer
    replaced: LineupPlayer | None
    pitcher: PitcherInfo | None
    is_orioles: bool
    batting_team: str
    batting_team_id: int | None

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
