from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence

from .cache import AsyncTtlCache
from .mlb import MlbApiError, MlbClient
from .models import (
    GameInfo,
    OutingPitchMix,
    PitchArsenalEntry,
    PitchTypeUsage,
    PlayerRef,
    ThrownPitch,
    UNKNOWN_PITCH_CODE,
    UNKNOWN_PITCH_NAME,
)


LOGGER = logging.getLogger(__name__)

# A live play-by-play grows with every pitch, so it is only worth holding for
# about as long as one plate appearance takes.
PLAY_BY_PLAY_TTL_SECONDS = 60
# A season arsenal barely moves across an outing, so it is held for hours.
ARSENAL_TTL_SECONDS = 21600


class PitchMixService:
    """What a pitcher threw in one outing, against his season baseline.

    The pitch types and velocities come from the game's play-by-play, and the
    baseline from the same pitcher's season arsenal. Both are cached, since a
    card asked for twice in an inning should not cost two requests.
    """

    def __init__(
        self,
        play_by_play_ttl_seconds: float = PLAY_BY_PLAY_TTL_SECONDS,
        arsenal_ttl_seconds: float = ARSENAL_TTL_SECONDS,
    ) -> None:
        self._pitches: AsyncTtlCache[int, tuple[ThrownPitch, ...]] = AsyncTtlCache(
            play_by_play_ttl_seconds
        )
        self._arsenals: AsyncTtlCache[
            tuple[int, int], dict[str, PitchArsenalEntry]
        ] = AsyncTtlCache(arsenal_ttl_seconds)

    async def outing(
        self,
        client: MlbClient,
        game: GameInfo,
        season: int,
        pitcher_id: int | None = None,
    ) -> OutingPitchMix | None:
        """The pitch mix for one pitcher in one game.

        ``None`` when nobody has thrown a pitch yet, or when the requested
        pitcher has not appeared in this game, which the card reports rather
        than showing an empty mix.
        """
        pitches = await self._pitches.get_or_fetch(
            game.game_pk, lambda: client.fetch_play_by_play(game.game_pk)
        )
        pitcher = select_pitcher(pitches, game, pitcher_id)
        if pitcher is None:
            return None

        thrown = [pitch for pitch in pitches if pitch.pitcher.player_id == pitcher.player_id]
        arsenal = await self._arsenal(client, pitcher.player_id, season)
        return build_pitch_mix(
            pitcher=pitcher,
            game_pk=game.game_pk,
            pitches=thrown,
            arsenal=arsenal,
            season=season if arsenal else None,
        )

    async def _arsenal(
        self, client: MlbClient, player_id: int, season: int
    ) -> dict[str, PitchArsenalEntry]:
        """The season baseline, or nothing when it cannot be read.

        A missing arsenal costs the card its velocity comparison, not the
        pitch mix itself, so the failure is logged rather than raised.
        """
        try:
            return await self._arsenals.get_or_fetch(
                (player_id, season),
                lambda: client.fetch_pitch_arsenal(player_id, season),
            )
        except MlbApiError as exc:
            LOGGER.warning(
                "Season pitch arsenal unavailable for player %s: %s", player_id, exc
            )
            return {}


def select_pitcher(
    pitches: Sequence[ThrownPitch], game: GameInfo, pitcher_id: int | None = None
) -> PlayerRef | None:
    """Whose outing the card is about.

    An explicit id wins, and has to have actually pitched in this game. With
    none, the default is the Orioles arm who threw most recently — the one on
    the mound, or the last one there when the game is over — falling back to
    the last pitcher of any side if the Orioles have not pitched yet.
    """
    if pitcher_id is not None:
        for pitch in reversed(pitches):
            if pitch.pitcher.player_id == pitcher_id:
                return pitch.pitcher
        return None

    for pitch in reversed(pitches):
        if is_orioles_pitch(pitch, game):
            return pitch.pitcher
    return pitches[-1].pitcher if pitches else None


def is_orioles_pitch(pitch: ThrownPitch, game: GameInfo) -> bool:
    """Whether an Orioles pitcher threw it.

    The feed names no pitching team, but the half inning gives it away: the
    home side pitches the top half.
    """
    if pitch.is_top_inning is None:
        return False
    return pitch.is_top_inning == game.is_home


def build_pitch_mix(
    pitcher: PlayerRef,
    game_pk: int,
    pitches: Iterable[ThrownPitch],
    arsenal: Mapping[str, PitchArsenalEntry] | None = None,
    season: int | None = None,
    batters_faced: int | None = None,
) -> OutingPitchMix:
    """Group an outing's pitches by type and join on the season baseline."""
    baseline = arsenal or {}
    grouped: dict[str, list[ThrownPitch]] = {}
    names: dict[str, str] = {}
    for pitch in pitches:
        code = (pitch.code or UNKNOWN_PITCH_CODE).strip().upper() or UNKNOWN_PITCH_CODE
        grouped.setdefault(code, []).append(pitch)
        if code not in names:
            names[code] = pitch.name or baseline.get(code, _unnamed(code)).name

    usages = [
        PitchTypeUsage(
            code=code,
            name=names.get(code, code),
            count=len(thrown),
            average_speed=_average_speed(thrown),
            season_average_speed=_baseline_speed(baseline, code),
        )
        for code, thrown in grouped.items()
    ]
    return OutingPitchMix(
        pitcher=pitcher,
        game_pk=game_pk,
        pitches=tuple(sorted(usages, key=_usage_sort_key)),
        batters_faced=batters_faced,
        baseline_season=season if baseline else None,
    )


def _unnamed(code: str) -> PitchArsenalEntry:
    name = UNKNOWN_PITCH_NAME if code == UNKNOWN_PITCH_CODE else code
    return PitchArsenalEntry(code=code, name=name)


def _baseline_speed(
    arsenal: Mapping[str, PitchArsenalEntry], code: str
) -> float | None:
    """The season speed for a pitch type, and never one for the unknown bucket.

    Pitches MLB left untyped are not one pitch, so averaging them against any
    single arsenal entry would be inventing a comparison.
    """
    if code == UNKNOWN_PITCH_CODE:
        return None
    entry = arsenal.get(code)
    return entry.average_speed if entry is not None else None


def _average_speed(pitches: Sequence[ThrownPitch]) -> float | None:
    speeds = [pitch.speed for pitch in pitches if pitch.speed is not None]
    if not speeds:
        return None
    return sum(speeds) / len(speeds)


def _usage_sort_key(usage: PitchTypeUsage) -> tuple[int, int, str]:
    """Most-thrown first, with the untyped bucket last however big it is."""
    return (1 if usage.code == UNKNOWN_PITCH_CODE else 0, -usage.count, usage.name)
