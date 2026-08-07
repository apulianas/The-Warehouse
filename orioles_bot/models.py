from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


ORIOLES_TEAM_ID = 110
ORIOLES_TEAM_NAME = "Baltimore Orioles"


@dataclass(frozen=True)
class LineupPlayer:
    player_id: int
    name: str
    position: str
    batting_order: int
    headshot_url: str


@dataclass(frozen=True)
class PitcherInfo:
    player_id: int | None
    name: str
    headshot_url: str | None = None
    status: str = "Probable"


@dataclass(frozen=True)
class MatchupAnnotation:
    emoji: str
    metric_name: str
    metric_value: float
    plate_appearances: int


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
