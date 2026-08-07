from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

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
    today_in_zone,
)
from .embeds import (
    error_embed,
    help_embed,
    lineup_embeds,
    player_stats_embed,
    schedule_embeds,
    standings_embed,
    transaction_embeds,
    wild_card_embed,
)
from .formatting import format_player_not_found
from .matchups import MatchupService
from .mlb import MlbApiError, MlbClient
from .models import (
    AL_EAST_DIVISION_ID,
    DivisionStandings,
    GameInfo,
    NextGame,
    TransactionInfo,
    WildCardStandings,
)
from .player_stats import PlayerStatsService
from .state import AnnouncementState, channel_key


LOGGER = logging.getLogger(__name__)

DEFAULT_STATS_DAYS = 7
DEFAULT_SCHEDULE_DAYS = 7
# Standings move only when games end, and a schedule barely moves at all, so a
# few minutes of staleness is invisible while sparing the API a request per use.
STANDINGS_TTL_SECONDS = 300
SCHEDULE_TTL_SECONDS = 300
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
        self.player_stats = PlayerStatsService()
        self.standings_cache: AsyncTtlCache[int, StandingsPayload] = AsyncTtlCache(
            STANDINGS_TTL_SECONDS
        )
        self.schedule_cache: AsyncTtlCache[tuple[str, str], list[GameInfo]] = (
            AsyncTtlCache(SCHEDULE_TTL_SECONDS)
        )
        self.announcement_state = AnnouncementState(config.state_file)

    async def setup_hook(self) -> None:
        self.session = aiohttp.ClientSession()
        self.mlb = MlbClient(self.session)
        self.announcement_state.load()
        if self.config.discord_channel_ids:
            self.announcement_state.adopt_legacy_keys(
                self.config.discord_channel_ids[0]
            )
        self.tree.add_command(_lineup_command(self))
        self.tree.add_command(_transactions_command(self))
        self.tree.add_command(_player_stats_command(self))
        self.tree.add_command(_standings_command(self))
        self.tree.add_command(_schedule_command(self))
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

        targets = await self._announcement_targets()
        if not targets:
            return

        target_date = today_in_zone(self.config.time_zone)
        try:
            games = await self.mlb.fetch_games(target_date)
            transactions = await self.mlb.fetch_transactions(target_date)
        except MlbApiError as exc:
            LOGGER.warning("Polling skipped because MLB data could not be fetched: %s", exc)
            return

        for game in games:
            if not game.lineup or not game.opponent_lineup:
                continue
            key = lineup_announcement_key(target_date, game)
            if not key:
                continue
            pending = [
                target
                for target in targets
                if self.announcement_state.unseen(channel_key(key, target.key_id))
            ]
            if not pending:
                continue
            matchup_annotations = await self.matchups.fetch_for_games([game])
            embeds = lineup_embeds(
                [game], target_date, self.config.time_zone, matchup_annotations
            )
            for target in pending:
                await self._announce(target, key, "Orioles lineup update", embeds)

        for transaction in transactions:
            key = transaction_announcement_key(transaction)
            embeds = transaction_embeds([transaction], target_date)
            for target in targets:
                if self.announcement_state.unseen(channel_key(key, target.key_id)):
                    await self._announce(
                        target, key, "Orioles roster transaction", embeds
                    )

    async def _announcement_targets(self) -> list[_AnnouncementTarget]:
        targets: list[_AnnouncementTarget] = []
        for channel_id in self.config.discord_channel_ids:
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

        for url in self.config.discord_webhook_urls:
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
        key: str,
        content: str,
        embeds: list[discord.Embed],
    ) -> None:
        """Post to one target, marking it sent only for that target."""
        try:
            await target.destination.send(content=content, embeds=embeds)
        except discord.DiscordException as exc:
            LOGGER.warning("Could not post to %s: %s", target.label, exc)
            return
        self.announcement_state.mark(channel_key(key, target.key_id))

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
        except MlbApiError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)), ephemeral=True)
            return

        await interaction.followup.send(
            embed=player_stats_embed(resolved, window, hitting, pitching)
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


def lineup_announcement_key(target_date: date, game: GameInfo) -> str | None:
    if not game.lineup:
        return None
    batting_order = ",".join(str(player.player_id) for player in game.lineup)
    pitcher = game.pitcher.player_id if game.pitcher else "none"
    return f"lineup:{target_date.isoformat()}:{game.game_pk}:{pitcher}:{batting_order}"


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
