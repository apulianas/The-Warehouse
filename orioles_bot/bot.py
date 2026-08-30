from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any
from collections.abc import Sequence
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from .bullpen import BullpenService, is_pitcher
from .cache import AsyncTtlCache
from .config import BotConfig, load_config, webhook_id
from .dates import (
    MAX_SCHEDULE_WINDOW_DAYS,
    MAX_STATS_WINDOW_DAYS,
    MIN_SCHEDULE_WINDOW_DAYS,
    MIN_STATS_WINDOW_DAYS,
    parse_user_date,
    schedule_window,
    stats_window,
    stats_window_ending,
    today_in_zone,
)
from .embeds import (
    bullpen_embed,
    error_embed,
    injuries_embed,
    no_live_game_embed,
    no_pitch_mix_game_embed,
    no_pitches_embed,
    on_deck_embed,
    pitch_mix_embed,
    help_embed,
    lineup_embeds,
    player_stats_embeds,
    schedule_embeds,
    standings_embed,
    substitution_embeds,
    transaction_embeds,
    wild_card_embed,
)
from .formatting import format_player_not_found
from .injuries import InjuryService
from .matchups import MatchupService
from .mlb import MlbApiError, MlbClient
from .models import (
    AL_EAST_DIVISION_ID,
    AtBatState,
    BULLPEN_WORKLOAD_DAYS,
    DivisionStandings,
    GameInfo,
    HittingSplit,
    InjuredPlayer,
    MatchupHistory,
    NextGame,
    PlayerRef,
    RECENT_SPLIT_DAYS,
    RECENT_SPLIT_HANDS,
    RelieverStatus,
    RunningProfile,
    Substitution,
    TransactionInfo,
    WildCardStandings,
)
from .pitch_mix import PitchMixService
from .player_stats import PlayerStatsService
from .running import SprintSpeedService
from .state import AnnouncementState, channel_key


LOGGER = logging.getLogger(__name__)

DEFAULT_STATS_DAYS = 7
DEFAULT_SCHEDULE_DAYS = 7
# Standings move only when games end, and a schedule barely moves at all, so a
# few minutes of staleness is invisible while sparing the API a request per use.
STANDINGS_TTL_SECONDS = 300
SCHEDULE_TTL_SECONDS = 300
# A bullpen card costs one request per pitcher, and usage only moves when
# someone warms up, so a few minutes of staleness is a cheap trade.
BULLPEN_TTL_SECONDS = 300
# An injury list costs a roster read, a season of transactions, and a game log
# per rehabbing player, and the injured list changes a couple of times a week
# at most, so it is cached for longer than anything else here.
INJURIES_TTL_SECONDS = 900
StatsDays = app_commands.Range[
    int, MIN_STATS_WINDOW_DAYS, MAX_STATS_WINDOW_DAYS
]
ScheduleDays = app_commands.Range[
    int, MIN_SCHEDULE_WINDOW_DAYS, MAX_SCHEDULE_WINDOW_DAYS
]
StandingsPayload = tuple[
    DivisionStandings | None, WildCardStandings | None, dict[int, NextGame]
]
STANDINGS_VIEW_BOTH = "both"
STANDINGS_VIEW_WILD_CARD = "wildcard"
STANDINGS_VIEW_DIVISION = "division"
# Which situational split matches the hand the incoming batter will face.
PLATOON_SPLIT_CODES = {"L": "vl", "R": "vr"}
# How far into the new local day a game from the previous one is still worth
# asking about. MLB files a game under the `officialDate` it started on, so a
# late start still in extra innings is only reachable by yesterday's date. Six
# hours clears the longest plausible finish for a 10:10 PM first pitch while
# keeping the extra request off the rest of the day.
CARRY_OVER_CUTOFF_HOUR = 6


def webhook_label(url: str) -> str:
    return f"webhook {webhook_id(url)}"


@dataclass(frozen=True)
class _AnnouncementTarget:
    key_id: str
    label: str
    destination: discord.abc.Messageable | discord.Webhook


class OriolesBot(commands.Bot):
    def __init__(self, config: BotConfig) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.config = config
        self.session: aiohttp.ClientSession | None = None
        self.mlb: MlbClient | None = None
        self.matchups = MatchupService(config.matchup_min_pa)
        self.sprint_speed = SprintSpeedService()
        self.player_stats = PlayerStatsService()
        self.bullpen = BullpenService()
        self.pitch_mix = PitchMixService()
        self.injuries = InjuryService()
        self.bullpen_cache: AsyncTtlCache[str, tuple[RelieverStatus, ...]] = (
            AsyncTtlCache(BULLPEN_TTL_SECONDS)
        )
        self.injuries_cache: AsyncTtlCache[str, tuple[InjuredPlayer, ...]] = (
            AsyncTtlCache(INJURIES_TTL_SECONDS)
        )
        self.standings_cache: AsyncTtlCache[int, StandingsPayload] = AsyncTtlCache(
            STANDINGS_TTL_SECONDS
        )
        self.schedule_cache: AsyncTtlCache[tuple[str, str], list[GameInfo]] = (
            AsyncTtlCache(SCHEDULE_TTL_SECONDS)
        )
        self.announcement_state = AnnouncementState(config.state_file)
        # Tracked so the interval is only changed, and logged, when it actually
        # differs from the cadence already running.
        self._poll_interval = config.poll_interval_seconds

    async def setup_hook(self) -> None:
        self.session = aiohttp.ClientSession()
        self.mlb = MlbClient(self.session)
        self.announcement_state.load()
        if self.config.discord_channel_ids:
            self.announcement_state.adopt_legacy_keys(
                self.config.discord_channel_ids[0]
            )
        self.announcement_state.adopt_undated_keys()
        self.tree.add_command(_lineup_command(self))
        self.tree.add_command(_transactions_command(self))
        self.tree.add_command(_player_stats_command(self))
        self.tree.add_command(_standings_command(self))
        self.tree.add_command(_schedule_command(self))
        self.tree.add_command(_bullpen_command(self))
        self.tree.add_command(_on_deck_command(self))
        self.tree.add_command(_pitches_command(self))
        self.tree.add_command(_injuries_command(self))
        self.tree.add_command(_help_command())
        await self.tree.sync()
        if self.config.has_announcement_targets:
            self.poll_updates.change_interval(seconds=self.config.poll_interval_seconds)
            self.poll_updates.start()
            LOGGER.info(
                "Started update polling every %s seconds for %s channel(s) "
                "and %s webhook(s): %s",
                self.config.poll_interval_seconds,
                len(self.config.discord_channel_ids),
                len(self.config.discord_webhook_urls),
                ", ".join(
                    [str(item) for item in self.config.discord_channel_ids]
                    + [webhook_label(url) for url in self.config.discord_webhook_urls]
                ),
            )
            if self.config.has_substitution_targets:
                LOGGER.info(
                    "Substitution cards go to %s",
                    ", ".join(
                        [
                            f"channel {item}"
                            for item in self.config.substitution_channel_ids
                        ]
                        + [
                            webhook_label(url)
                            for url in self.config.substitution_webhook_urls
                        ]
                    ),
                )

    async def close(self) -> None:
        if self.poll_updates.is_running():
            self.poll_updates.cancel()
        if self.session is not None:
            await self.session.close()
        await super().close()

    async def on_ready(self) -> None:
        LOGGER.info("Logged in as %s", self.user)

    @tasks.loop(seconds=300)
    async def poll_updates(self) -> None:
        if not self.config.has_announcement_targets or self.mlb is None:
            return

        targets = await self._announcement_targets(
            self.config.discord_channel_ids, self.config.discord_webhook_urls
        )
        substitution_targets = (
            await self._announcement_targets(
                self.config.substitution_channel_ids,
                self.config.substitution_webhook_urls,
            )
            if self.config.has_substitution_targets
            else targets
        )
        if not targets and not substitution_targets:
            return

        # One clock reading for the whole pass, so the date cannot roll over
        # between deciding what "today" is and deciding whether yesterday still
        # has a game running.
        now = datetime.now(timezone.utc)
        target_date = today_in_zone(self.config.time_zone, now)
        try:
            games = await self.mlb.fetch_games(target_date)
            transactions = await self.mlb.fetch_transactions(target_date)
        except MlbApiError as exc:
            LOGGER.warning("Polling skipped because MLB data could not be fetched: %s", exc)
            return

        # A game still being played from yesterday is older news than anything
        # today, so it goes first. Each game carries the date it is filed under
        # rather than the date the poll is working, since the two differ either
        # side of midnight and the cards read that date back.
        carried = await self._carried_over_games(target_date, now)
        dated_games = [(target_date - timedelta(days=1), game) for game in carried]
        dated_games += [(target_date, game) for game in games]

        for game_date, game in dated_games:
            if not game.lineup or not game.opponent_lineup:
                continue
            await self._announce_lineup(game, targets, game_date)
            await self._announce_substitutions(game, substitution_targets, game_date)

        await self._announce_transactions(transactions, targets, target_date)

        self._apply_poll_interval([game for _, game in dated_games], now)

    async def _carried_over_games(
        self, target_date: date, now: datetime
    ) -> list[GameInfo]:
        """Yesterday's games, for as long as one of them is still being played.

        `target_date` flips at local midnight, but MLB keeps a game filed under
        the `officialDate` it started on for good. A West Coast start still in
        extra innings therefore vanishes from the poll the moment the clock
        rolls over, taking every substitution after it with it: the new day's
        schedule simply does not list that game.

        Only consulted in the small hours, and only carried while something is
        genuinely live, so an ordinary day never pays for the extra requests. A
        postponed or suspended game reads as final here, so neither holds the
        lookback open.
        """
        if self.mlb is None:
            return []
        if now.astimezone(self.config.time_zone).hour >= CARRY_OVER_CUTOFF_HOUR:
            return []

        previous = target_date - timedelta(days=1)
        try:
            games = await self.mlb.fetch_games(previous)
        except MlbApiError as exc:
            LOGGER.info("Could not re-check %s for a game still running: %s", previous, exc)
            return []

        carried = [game for game in games if game.is_in_progress]
        if carried:
            LOGGER.info(
                "Still following %s game(s) from %s past midnight",
                len(carried),
                previous.isoformat(),
            )
        return carried

    def _apply_poll_interval(
        self, games: Sequence[GameInfo], now: datetime | None = None
    ) -> None:
        """Speed polling up around games and slow it back down afterwards."""
        seconds, reason = poll_interval_for(
            games, now or datetime.now(timezone.utc), self.config
        )
        if seconds == self._poll_interval:
            return
        self._poll_interval = seconds
        # discord.py recalculates the sleep already in flight, so a change here
        # takes effect before the next tick rather than after it.
        self.poll_updates.change_interval(seconds=seconds)
        LOGGER.info("Polling every %s seconds (%s)", seconds, reason)

    async def _announce_lineup(
        self,
        game: GameInfo,
        targets: list[_AnnouncementTarget],
        target_date: date,
    ) -> None:
        key = lineup_announcement_key(game)
        if not key:
            return
        pending = [
            target
            for target in targets
            if self.announcement_state.unseen(channel_key(key, target.key_id))
        ]
        if not pending:
            return
        matchup_annotations = await self.matchups.fetch_for_games([game])
        embeds = lineup_embeds(
            [game], target_date, self.config.time_zone, matchup_annotations
        )
        for target in pending:
            await self._announce(target, [key], embeds)

    async def _announce_transactions(
        self,
        transactions: Sequence[TransactionInfo],
        targets: list[_AnnouncementTarget],
        target_date: date,
    ) -> None:
        """Post everything new since the last check as a single card.

        Roster moves arrive in matched sets — an option out pays for the recall
        in — so posting one card per transaction splits a single piece of news
        across several messages. Each transaction is still marked individually,
        so a later move on the same day posts alone rather than repeating the
        ones already sent.
        """
        for target in targets:
            pending = [
                transaction
                for transaction in transactions
                if self.announcement_state.unseen(
                    channel_key(
                        transaction_announcement_key(transaction), target.key_id
                    )
                )
            ]
            if not pending:
                continue
            await self._announce(
                target,
                [transaction_announcement_key(item) for item in pending],
                transaction_embeds(pending, target_date),
            )

    async def _announcement_targets(
        self, channel_ids: Sequence[int], webhook_urls: Sequence[str]
    ) -> list[_AnnouncementTarget]:
        targets: list[_AnnouncementTarget] = []
        for channel_id in channel_ids:
            channel = self.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.fetch_channel(channel_id)
                except discord.DiscordException as exc:
                    LOGGER.warning("Channel %s is unavailable: %s", channel_id, exc)
                    continue
            if not isinstance(channel, discord.abc.Messageable):
                LOGGER.warning("Channel %s is not messageable", channel_id)
                continue
            targets.append(
                _AnnouncementTarget(str(channel_id), f"channel {channel_id}", channel)
            )

        for url in webhook_urls:
            if self.session is None:
                break
            try:
                webhook = discord.Webhook.from_url(url, session=self.session)
            except (ValueError, discord.DiscordException) as exc:
                LOGGER.warning("Webhook %s is unusable: %s", webhook_label(url), exc)
                continue
            targets.append(
                _AnnouncementTarget(
                    f"webhook:{webhook_id(url)}", webhook_label(url), webhook
                )
            )
        return targets

    async def _announce(
        self,
        target: _AnnouncementTarget,
        keys: Sequence[str],
        embeds: list[discord.Embed],
    ) -> None:
        """Post to one target, marking it sent only for that target.

        Nothing is written above the embeds: every card already titles itself,
        so a line of message text only said the same thing twice.

        A card can cover several announcements, so every key it settles is
        marked together: a partial mark would repost the rest on the next poll.
        """
        try:
            await target.destination.send(embeds=embeds)
        except discord.DiscordException as exc:
            LOGGER.warning("Could not post to %s: %s", target.label, exc)
            return
        for key in keys:
            self.announcement_state.mark(channel_key(key, target.key_id))

    async def _announce_substitutions(
        self,
        game: GameInfo,
        targets: list[_AnnouncementTarget],
        target_date: date,
    ) -> None:
        """Post a compact card for each new hitter once the game is underway.

        Before first pitch a changed batting order is a corrected lineup card,
        which the full lineup post already covers.

        ``target_date`` is the date the game is filed under, not the date the
        poll is working: it only feeds the stat lookups, since a game that ran
        past midnight still wants the season and recent form of the day it was
        played.
        """
        if not game.has_started or not game.substitutions:
            return

        pending: list[tuple[Substitution, str, list[_AnnouncementTarget]]] = []
        for substitution in game.substitutions:
            key = substitution_announcement_key(substitution)
            waiting = [
                target
                for target in targets
                if self.announcement_state.unseen(channel_key(key, target.key_id))
            ]
            if waiting:
                pending.append((substitution, key, waiting))
        if not pending:
            return

        resolved = await self._resolve_substitutions(
            [substitution for substitution, _, _ in pending]
        )
        histories, splits, recent, profiles = await self._substitution_stats(
            resolved, target_date
        )

        for substitution, (_, key, waiting) in zip(resolved, pending, strict=True):
            embeds = substitution_embeds(
                [substitution], histories, splits, profiles, recent
            )
            for target in waiting:
                await self._announce(target, [key], embeds)

    async def _resolve_substitutions(
        self, substitutions: list[Substitution]
    ) -> list[Substitution]:
        """Attach bat side and throwing hand, which the boxscore omits."""
        player_ids: set[int] = set()
        for substitution in substitutions:
            player_ids.add(substitution.batter.player_id)
            if substitution.pitcher and substitution.pitcher.player_id is not None:
                player_ids.add(substitution.pitcher.player_id)

        hands: dict[int, tuple[str | None, str | None]] = {}
        if player_ids and self.mlb is not None:
            try:
                hands = await self.mlb.fetch_handedness(sorted(player_ids))
            except MlbApiError as exc:
                LOGGER.info("Substitution handedness unavailable: %s", exc)

        resolved: list[Substitution] = []
        for substitution in substitutions:
            batter = substitution.batter
            bat_side = hands.get(batter.player_id, (None, None))[0]
            pitcher = substitution.pitcher
            if pitcher is not None and pitcher.player_id is not None:
                pitcher = replace(
                    pitcher, throws=hands.get(pitcher.player_id, (None, None))[1]
                )
            resolved.append(
                replace(
                    substitution,
                    batter=replace(batter, bat_side=bat_side),
                    pitcher=pitcher,
                )
            )
        return resolved

    async def _substitution_stats(
        self, substitutions: list[Substitution], target_date: date
    ) -> tuple[
        dict[tuple[int, int], MatchupHistory],
        dict[int, HittingSplit],
        dict[int, MatchupHistory],
        dict[int, RunningProfile],
    ]:
        """Stats for the incoming players, fetched per role.

        A pinch runner's card never shows matchup history, so looking it up
        would burn API calls on numbers nobody sees. The reverse holds for
        hitters and baserunning.
        """
        hitters = [sub for sub in substitutions if not sub.is_pinch_runner]
        runners = [sub for sub in substitutions if sub.is_pinch_runner]

        pairs = [
            (substitution.batter.player_id, substitution.pitcher.player_id)
            for substitution in hitters
            if substitution.pitcher is not None
            and substitution.pitcher.player_id is not None
        ]
        histories = await self.matchups.history_many(pairs)
        recent = await self._recent_hand_splits(hitters, target_date)

        splits: dict[int, HittingSplit] = {}
        profiles: dict[int, RunningProfile] = {}
        if self.mlb is None:
            return histories, splits, recent, profiles

        for substitution in hitters:
            pitcher = substitution.pitcher
            hand = pitcher.throws if pitcher is not None else None
            code = PLATOON_SPLIT_CODES.get(hand or "")
            if code is None:
                continue
            try:
                player_splits = await self.mlb.fetch_platoon_splits(
                    substitution.batter.player_id, target_date.year
                )
            except MlbApiError as exc:
                LOGGER.info(
                    "Platoon split unavailable for %s: %s",
                    substitution.batter.name,
                    exc,
                )
                continue
            split = player_splits.get(code)
            # An absent entry means the lookup failed, so the card says the
            # split is unavailable rather than claiming there were no plate
            # appearances. A successful lookup with nothing against this hand
            # records an empty split, which does make that claim.
            splits[substitution.batter.player_id] = (
                split if split is not None else HittingSplit()
            )

        for substitution in runners:
            profile = await self._running_profile(
                substitution.batter.player_id,
                substitution.batter.name,
                target_date.year,
            )
            if profile is not None:
                profiles[substitution.batter.player_id] = profile

        return histories, splits, recent, profiles

    async def _recent_hand_splits(
        self, hitters: Sequence[Substitution], target_date: date
    ) -> dict[int, MatchupHistory]:
        """The last stretch of games against the hand each hitter will face.

        Keyed by batter because a card only ever shows the one hand its
        pitcher throws with.
        """
        window = stats_window_ending(RECENT_SPLIT_DAYS, target_date)
        wanted: dict[int, str] = {}
        for substitution in hitters:
            pitcher = substitution.pitcher
            hand = pitcher.throws if pitcher is not None else None
            if hand in RECENT_SPLIT_HANDS:
                wanted[substitution.batter.player_id] = str(hand)

        histories = await self.matchups.hand_history_many(
            (player_id, hand, window.start, window.end)
            for player_id, hand in wanted.items()
        )
        return {
            player_id: history
            for player_id, hand in wanted.items()
            if (history := histories.get((player_id, hand, window.start, window.end)))
            is not None
        }

    async def _running_profile(
        self, player_id: int, name: str, season: int
    ) -> RunningProfile | None:
        """Steals from MLB plus Statcast speed, each optional on its own.

        Statcast only rates runners with enough tracked competitive runs, so a
        callup can have a real steal record and no speed number, or vice versa.
        Neither source failing should suppress the other.
        """
        if self.mlb is None:
            return None
        try:
            profile = await self.mlb.fetch_running_stats(player_id, season)
        except MlbApiError as exc:
            LOGGER.info("Stolen base record unavailable for %s: %s", name, exc)
            profile = RunningProfile()

        speed = await self.sprint_speed.for_player(player_id, season)
        if speed is not None:
            profile = replace(
                profile,
                sprint_speed=speed.get("sprint_speed"),
                bolts=speed.get("bolts"),
                home_to_first=speed.get("home_to_first"),
            )
        return profile if not profile.is_empty else None

    @poll_updates.before_loop
    async def before_poll_updates(self) -> None:
        await self.wait_until_ready()


def _lineup_command(bot: OriolesBot) -> app_commands.Command[Any, ..., None]:
    @app_commands.command(name="lineup", description="Show today's Orioles lineup or another date.")
    @app_commands.describe(date="Optional date: today or YYYY-MM-DD")
    async def lineup(interaction: discord.Interaction, date: str | None = None) -> None:
        target_date = await _parse_or_respond(interaction, date, bot.config)
        if target_date is None:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            games = await _require_mlb(bot).fetch_games(target_date)
        except MlbApiError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return
        matchup_annotations = await bot.matchups.fetch_for_games(games)
        await interaction.followup.send(
            embeds=lineup_embeds(
                games, target_date, bot.config.time_zone, matchup_annotations
            )
        )

    return lineup


def _transactions_command(bot: OriolesBot) -> app_commands.Command[Any, ..., None]:
    @app_commands.command(
        name="transactions", description="Show Orioles roster transactions for a date."
    )
    @app_commands.describe(date="Optional date: today or YYYY-MM-DD")
    async def transactions(interaction: discord.Interaction, date: str | None = None) -> None:
        target_date = await _parse_or_respond(interaction, date, bot.config)
        if target_date is None:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            items = await _require_mlb(bot).fetch_transactions(target_date)
        except MlbApiError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return
        await interaction.followup.send(embeds=transaction_embeds(items, target_date))

    return transactions


def _player_stats_command(bot: OriolesBot) -> app_commands.Command[Any, ..., None]:
    @app_commands.command(
        name="playerstats", description="Show a player's stats over the last N days."
    )
    @app_commands.describe(
        player="Player name — pick from the Orioles roster or type any full name",
        days=(
            f"Window length in days ({MIN_STATS_WINDOW_DAYS}-"
            f"{MAX_STATS_WINDOW_DAYS}, default {DEFAULT_STATS_DAYS})"
        ),
    )
    async def playerstats(
        interaction: discord.Interaction,
        player: str,
        days: StatsDays = DEFAULT_STATS_DAYS,
    ) -> None:
        try:
            window = stats_window(days, bot.config.time_zone)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        client = _require_mlb(bot)
        try:
            resolved = await bot.player_stats.resolve(client, player)
            if resolved is None:
                await interaction.followup.send(
                    format_player_not_found(player), ephemeral=True
                )
                return
            hitting, pitching = await bot.player_stats.stats(
                client, resolved.player_id, window
            )
            pitching_games = (
                await bot.player_stats.pitching_games(client, resolved.player_id, window)
                if pitching
                else ()
            )
        except MlbApiError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return

        await interaction.followup.send(
            embeds=player_stats_embeds(
                resolved, window, hitting, pitching, pitching_games
            )
        )

    @playerstats.autocomplete("player")
    async def playerstats_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if bot.mlb is None:
            return []
        suggestions = await bot.player_stats.autocomplete(bot.mlb, current)
        return [
            app_commands.Choice(
                name=f"{item.name} ({item.position})" if item.position else item.name,
                value=str(item.player_id),
            )
            for item in suggestions
        ]

    return playerstats


def _standings_command(bot: OriolesBot) -> app_commands.Command[Any, ..., None]:
    @app_commands.command(
        name="standings",
        description="Show the AL East standings, the AL wild card race, or both.",
    )
    @app_commands.describe(
        view="Which picture to show — defaults to the wild card race and the AL East"
    )
    @app_commands.choices(
        view=[
            app_commands.Choice(name="Both", value=STANDINGS_VIEW_BOTH),
            app_commands.Choice(name="Wild card", value=STANDINGS_VIEW_WILD_CARD),
            app_commands.Choice(name="AL East", value=STANDINGS_VIEW_DIVISION),
        ]
    )
    async def standings(
        interaction: discord.Interaction,
        view: app_commands.Choice[str] | None = None,
    ) -> None:
        selected = view.value if view is not None else STANDINGS_VIEW_BOTH
        await interaction.response.defer(ephemeral=True)
        client = _require_mlb(bot)
        try:
            payload = await bot.standings_cache.get_or_fetch(
                AL_EAST_DIVISION_ID,
                lambda: _fetch_standings_payload(bot, client),
            )
        except MlbApiError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return

        await interaction.followup.send(
            embeds=_standings_embeds(payload, selected, bot.config.time_zone)
        )

    return standings


def _standings_embeds(
    payload: StandingsPayload, view: str, time_zone: ZoneInfo
) -> list[discord.Embed]:
    """The requested embeds, wild card first since it is the wider picture."""
    division, wild_card, next_games = payload
    embeds: list[discord.Embed] = []
    if view in {STANDINGS_VIEW_BOTH, STANDINGS_VIEW_WILD_CARD}:
        embeds.append(wild_card_embed(wild_card, next_games, time_zone))
    if view in {STANDINGS_VIEW_BOTH, STANDINGS_VIEW_DIVISION}:
        embeds.append(standings_embed(division, next_games, time_zone))
    return embeds


async def _fetch_standings_payload(
    bot: OriolesBot, client: MlbClient
) -> StandingsPayload:
    """Division standings, the wild card race, and every team's next game.

    All three are fetched together so one cache entry serves any view, and the
    next-game lookup covers both tables in a single schedule request. That
    lookup is best effort: a failure there should still leave the standings
    postable rather than turning the whole command into an error.
    """
    division = await client.fetch_division_standings(AL_EAST_DIVISION_ID)
    wild_card = await client.fetch_wild_card_standings()

    team_ids = [record.team_id for record in (division.teams if division else ())]
    team_ids.extend(
        record.team_id for record in (wild_card.teams if wild_card else ())
    )
    if not team_ids:
        return division, wild_card, {}

    try:
        next_games = await client.fetch_next_games(
            team_ids, today_in_zone(bot.config.time_zone)
        )
    except MlbApiError as exc:
        LOGGER.warning("Standings posted without next games: %s", exc)
        next_games = {}
    return division, wild_card, next_games


def _schedule_command(bot: OriolesBot) -> app_commands.Command[Any, ..., None]:
    @app_commands.command(
        name="schedule", description="Show upcoming Orioles games."
    )
    @app_commands.describe(
        days=(
            f"How many days ahead ({MIN_SCHEDULE_WINDOW_DAYS}-"
            f"{MAX_SCHEDULE_WINDOW_DAYS}, default {DEFAULT_SCHEDULE_DAYS})"
        )
    )
    async def schedule(
        interaction: discord.Interaction,
        days: ScheduleDays = DEFAULT_SCHEDULE_DAYS,
    ) -> None:
        try:
            window = schedule_window(days, bot.config.time_zone)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        client = _require_mlb(bot)
        key = (window.start.isoformat(), window.end.isoformat())
        try:
            games = await bot.schedule_cache.get_or_fetch(
                key, lambda: client.fetch_schedule(window)
            )
        except MlbApiError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return

        await interaction.followup.send(
            embeds=schedule_embeds(games, window, bot.config.time_zone)
        )

    return schedule


def _bullpen_command(bot: OriolesBot) -> app_commands.Command[Any, ..., None]:
    @app_commands.command(
        name="bullpen",
        description="Show which Orioles relievers are available, from recent usage.",
    )
    async def bullpen(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        client = _require_mlb(bot)
        today = today_in_zone(bot.config.time_zone)
        try:
            relievers = await bot.bullpen_cache.get_or_fetch(
                today.isoformat(),
                lambda: bot.bullpen.relievers(client, today),
            )
        except MlbApiError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return

        await interaction.followup.send(
            embed=bullpen_embed(relievers, BULLPEN_WORKLOAD_DAYS)
        )

    return bullpen


def _injuries_command(bot: OriolesBot) -> app_commands.Command[Any, ..., None]:
    @app_commands.command(
        name="injuries",
        description="Show the Orioles injured list, with dates and rehab assignments.",
    )
    async def injuries(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        client = _require_mlb(bot)
        today = today_in_zone(bot.config.time_zone)
        try:
            injured = await bot.injuries_cache.get_or_fetch(
                today.isoformat(),
                lambda: bot.injuries.injured_list(client, today),
            )
        except MlbApiError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return

        await interaction.followup.send(embed=injuries_embed(injured, today))

    return injuries


def _on_deck_command(bot: OriolesBot) -> app_commands.Command[Any, ..., None]:
    @app_commands.command(
        name="ondeck",
        description="Show who is at bat, on deck, and in the hole right now.",
    )
    async def ondeck(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        client = _require_mlb(bot)
        today = today_in_zone(bot.config.time_zone)
        try:
            games = await client.fetch_games(today)
        except MlbApiError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return

        # The at-bat only exists while a game is being played, and a
        # doubleheader can have one finished and one underway.
        live = next((game for game in games if game.is_in_progress), None)
        if live is None:
            await interaction.followup.send(embed=no_live_game_embed(today))
            return

        try:
            state = await client.fetch_linescore(live)
        except MlbApiError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return

        await interaction.followup.send(
            embed=on_deck_embed(state, live, await _at_bat_histories(bot, state))
        )

    return ondeck


async def _at_bat_histories(
    bot: OriolesBot, state: AtBatState
) -> dict[tuple[int, int], MatchupHistory] | None:
    """Career lines for the three upcoming hitters against the current pitcher.

    ``None`` when there is no pitcher to look up, so the card leaves the
    matchup field off entirely rather than printing three "unavailable" rows.
    """
    pitcher = state.pitcher
    if pitcher is None:
        return None
    pairs = [
        (player.player_id, pitcher.player_id)
        for player in (state.batter, state.on_deck, state.in_hole)
        if player is not None
    ]
    if not pairs:
        return None
    return await bot.matchups.history_many(pairs)


def _pitches_command(bot: OriolesBot) -> app_commands.Command[Any, ..., None]:
    @app_commands.command(
        name="pitches",
        description="Show a pitcher's pitch usage in the current or last game.",
    )
    @app_commands.describe(
        pitcher=(
            "Pitcher — pick from the Orioles roster or type any full name. "
            "Defaults to whoever is on the mound."
        )
    )
    async def pitches(
        interaction: discord.Interaction, pitcher: str | None = None
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        client = _require_mlb(bot)
        today = today_in_zone(bot.config.time_zone)
        try:
            resolved: PlayerRef | None = None
            if pitcher and pitcher.strip():
                resolved = await bot.player_stats.resolve(client, pitcher)
                if resolved is None:
                    await interaction.followup.send(
                        format_player_not_found(pitcher), ephemeral=True
                    )
                    return

            game = await _pitch_mix_game(bot, client, today)
            if game is None:
                await interaction.followup.send(
                    embed=no_pitch_mix_game_embed(today)
                )
                return

            season = game.game_date.year if game.game_date else today.year
            mix = await bot.pitch_mix.outing(
                client,
                game,
                season,
                resolved.player_id if resolved is not None else None,
            )
        except MlbApiError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return

        if mix is None or mix.is_empty:
            await interaction.followup.send(embed=no_pitches_embed(game, resolved))
            return

        await interaction.followup.send(embed=pitch_mix_embed(mix, game))

    @pitches.autocomplete("pitcher")
    async def pitches_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if bot.mlb is None:
            return []
        suggestions = await bot.player_stats.autocomplete(bot.mlb, current)
        return [
            app_commands.Choice(
                name=f"{item.name} ({item.position})" if item.position else item.name,
                value=str(item.player_id),
            )
            for item in suggestions
            if is_pitcher(item)
        ]

    return pitches


async def _pitch_mix_game(
    bot: OriolesBot, client: MlbClient, today: date
) -> GameInfo | None:
    """The game a pitch mix should be read from: the live one, or the last one.

    Yesterday is only asked about when today has nothing to show, so a card
    pulled up in the morning still describes last night's start rather than
    reporting that no game has been played.
    """
    for target in (today, today - timedelta(days=1)):
        game = pitch_mix_game(await client.fetch_games(target))
        if game is not None:
            return game
    return None


def pitch_mix_game(games: Sequence[GameInfo]) -> GameInfo | None:
    """Pick the game whose pitches are worth reading, from one day's slate.

    A game underway wins over a finished one, so a doubleheader reports the
    game actually being played rather than the one already in the books.
    """
    live = [game for game in games if game.is_in_progress]
    if live:
        return live[-1]
    played = [
        game for game in games if game.has_started and not game.is_unplayed
    ]
    return played[-1] if played else None


def _help_command() -> app_commands.Command[Any, ..., None]:
    @app_commands.command(name="help", description="Show Orioles bot command help.")
    async def help_command(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=help_embed(), ephemeral=True)

    return help_command


async def _parse_or_respond(
    interaction: discord.Interaction, raw_date: str | None, config: BotConfig
) -> date | None:
    try:
        return parse_user_date(raw_date, config.time_zone)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return None


def _require_mlb(bot: OriolesBot) -> MlbClient:
    if bot.mlb is None:
        raise RuntimeError("MLB client is not initialized")
    return bot.mlb


def poll_interval_for(
    games: Sequence[GameInfo], now: datetime, config: BotConfig
) -> tuple[int, str]:
    """Pick a polling cadence for the day's games, with the reason why.

    Three cadences, fastest first:

    * a game underway, including one stuck in a rain delay, since play can
      resume at any moment and substitutions arrive in bursts;
    * first pitch approaching, which is when the lineup card drops;
    * otherwise idle.

    Postponed, cancelled and suspended games are ignored entirely. They keep
    their original start time all day, so counting them would otherwise pin the
    bot to the pre-game cadence for hours with nothing left to report.
    """
    playable = [game for game in games if not game.is_unplayed]

    if any(game.is_in_progress for game in playable):
        return config.live_poll_interval_seconds, "a game is in progress"

    window = timedelta(minutes=config.pregame_lead_minutes)
    for game in playable:
        if game.is_final:
            continue
        # No start time means the game is imminent or TBD; either way it is
        # worth watching. A start time already past means a delayed start,
        # where the lineup is usually out and first pitch could come any time.
        if game.game_date is None or game.game_date - now <= window:
            return config.pregame_poll_interval_seconds, "a game is coming up"

    return config.poll_interval_seconds, "no game is near"


def lineup_announcement_key(game: GameInfo) -> str | None:
    """Identify a posted lineup card.

    Keyed on the announced starters rather than the batting order as it stands,
    so a pinch hitter does not look like a brand new lineup and trigger a
    second full post. Pre-game changes still move the key, because those are
    genuine lineup corrections worth reposting.

    Deliberately carries no date. ``game_pk`` already identifies one game on
    one day, and a game running past local midnight is polled under both dates
    either side of it — a date here would make the second pass look
    unannounced and repost the whole card.
    """
    starters = game.starting_lineup or game.lineup
    if not starters:
        return None
    batting_order = ",".join(str(player.player_id) for player in starters)
    pitcher = game.pitcher.player_id if game.pitcher else "none"
    return f"lineup:{game.game_pk}:{pitcher}:{batting_order}"


def substitution_announcement_key(substitution: Substitution) -> str:
    """Identify a posted substitution card.

    Undated for the same reason as the lineup key: ``Substitution.key`` opens
    with the game id, so a game polled either side of midnight would otherwise
    repost every substitution it had already announced. On a doubleheader that
    is both games' worth of cards at once.
    """
    return f"substitution:{substitution.key}"


def transaction_announcement_key(transaction: TransactionInfo) -> str:
    return f"transaction:{transaction.date.isoformat()}:{transaction.transaction_id}"


def run() -> None:
    try:
        config = load_config()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    bot = OriolesBot(config)
    try:
        bot.run(config.discord_token, log_handler=None)
    except KeyboardInterrupt:
        LOGGER.info("Shutting down")
    except asyncio.CancelledError:
        LOGGER.info("Cancelled")
