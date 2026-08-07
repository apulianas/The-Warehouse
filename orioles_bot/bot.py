from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from .config import BotConfig, load_config
from .dates import parse_user_date, today_in_zone
from .embeds import error_embed, help_embed, lineup_embeds, transaction_embeds
from .matchups import MatchupService
from .mlb import MlbApiError, MlbClient
from .models import GameInfo, TransactionInfo
from .state import AnnouncementState, channel_key


LOGGER = logging.getLogger(__name__)


class OriolesBot(commands.Bot):
    def __init__(self, config: BotConfig) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.config = config
        self.session: aiohttp.ClientSession | None = None
        self.mlb: MlbClient | None = None
        self.matchups = MatchupService(config.matchup_min_pa)
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
        self.tree.add_command(_help_command())
        await self.tree.sync()
        if self.config.discord_channel_ids:
            self.poll_updates.change_interval(seconds=self.config.poll_interval_seconds)
            self.poll_updates.start()
            LOGGER.info(
                "Started update polling every %s seconds for %s channel(s): %s",
                self.config.poll_interval_seconds,
                len(self.config.discord_channel_ids),
                ", ".join(str(item) for item in self.config.discord_channel_ids),
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
        if not self.config.discord_channel_ids or self.mlb is None:
            return

        channels = await self._announcement_channels()
        if not channels:
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
                (channel_id, channel)
                for channel_id, channel in channels
                if self.announcement_state.unseen(channel_key(key, channel_id))
            ]
            if not pending:
                continue
            matchup_annotations = await self.matchups.fetch_for_games([game])
            embeds = lineup_embeds(
                [game], target_date, self.config.time_zone, matchup_annotations
            )
            for channel_id, channel in pending:
                await self._announce(
                    channel, channel_id, key, "Orioles lineup update", embeds
                )

        for transaction in transactions:
            key = transaction_announcement_key(transaction)
            embeds = transaction_embeds([transaction], target_date)
            for channel_id, channel in channels:
                if self.announcement_state.unseen(channel_key(key, channel_id)):
                    await self._announce(
                        channel,
                        channel_id,
                        key,
                        "Orioles roster transaction",
                        embeds,
                    )

    async def _announcement_channels(
        self,
    ) -> list[tuple[int, discord.abc.Messageable]]:
        channels: list[tuple[int, discord.abc.Messageable]] = []
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
            channels.append((channel_id, channel))
        return channels

    async def _announce(
        self,
        channel: discord.abc.Messageable,
        channel_id: int,
        key: str,
        content: str,
        embeds: list[discord.Embed],
    ) -> None:
        """Post to one channel, marking it sent only for that channel."""
        try:
            await channel.send(content=content, embeds=embeds)
        except discord.DiscordException as exc:
            LOGGER.warning("Could not post to channel %s: %s", channel_id, exc)
            return
        self.announcement_state.mark(channel_key(key, channel_id))

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
