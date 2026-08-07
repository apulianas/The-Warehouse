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
