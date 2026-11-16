import asyncio
import logging
import sqlite3
from datetime import UTC, datetime

import aiohttp
import discord
import psycopg
from discord import app_commands
from discord.ext import tasks

from .messages import ALERTS, DEFAULT_ALERTS, embed_for, safe
from .store import Batch, Store, Target


LOG = logging.getLogger(__name__)
DELIVERY_TIMEOUT = 20
NONCE_RETRY_WINDOW = 105


class FollowMenu(discord.ui.View):
    def __init__(
        self, bot: "CopeBot", user_id: int, target: Target, kinds: list[str], rating_list_id: int | None,
    ) -> None:
        super().__init__(timeout=180)
        self.bot = bot
        self.user_id = user_id
        self.target = target
        self.rating_list_id = rating_list_id
        self.select = discord.ui.Select(
            placeholder="Choose which alerts to receive",
            min_values=1,
            max_values=len(ALERTS),
            options=[
                discord.SelectOption(value=kind, label=label, default=kind in kinds)
                for kind, label in ALERTS.items()
            ],
        )
        self.select.callback = self.save
        self.add_item(self.select)
        save_button = discord.ui.Button(label="Save preselected alerts", style=discord.ButtonStyle.primary)
        save_button.callback = self.save_defaults
        self.add_item(save_button)
        self.kinds = kinds
        self.save_lock = asyncio.Lock()
        self.saved = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def save(self, interaction: discord.Interaction) -> None:
        await self.save_selection(interaction, self.select.values)

    async def save_defaults(self, interaction: discord.Interaction) -> None:
        await self.save_selection(interaction, self.kinds)

    async def save_selection(self, interaction: discord.Interaction, selected: list[str]) -> None:
        await interaction.response.defer()
        async with self.save_lock:
            if self.saved:
                return
            kinds = [kind for kind in ALERTS if kind in selected]
            if not kinds:
                return
            state = await asyncio.to_thread(self.bot.store.subscriber, self.user_id)
            blocked = state["dm_blocked"] if state else not await self.bot.confirm_dm(self.user_id)
            saved = await asyncio.to_thread(
                self.bot.store.save_follow, self.user_id, self.target, kinds, self.rating_list_id, blocked,
            )
            if not saved:
                await interaction.edit_original_response(
                    content="This selection is unavailable or you have reached the 50-follow limit.", view=None,
                )
                self.stop()
                return
            status = ""
            if blocked:
                status = "\nEnable DMs from the bot, then run `/cope resume`."
            elif state and state["paused"]:
                status = "\nYour alerts are paused. Run `/cope resume` to turn them on."
            await interaction.edit_original_response(
                content=f'Following **{safe(self.target.label)}** for {", ".join(ALERTS[kind] for kind in kinds)}.{status}',
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            self.saved = True
            self.stop()

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        LOG.error("Follow preferences could not be saved", exc_info=error)
        await interaction.followup.send("Could not save the follow. Please try again.", ephemeral=True)


class CopeCommands(app_commands.Group, name="cope", description="Follow engines on Cope"):
    def __init__(self, bot: "CopeBot") -> None:
        super().__init__()
        self.bot = bot

    async def choices(self, method, *args) -> list[app_commands.Choice[str]]:
        try:
            rows = await asyncio.wait_for(asyncio.to_thread(method, *args), timeout=2)
        except (TimeoutError, psycopg.Error, sqlite3.Error):
            return []
        return [app_commands.Choice(name=row["label"][:100], value=row["key"]) for row in rows]

    @app_commands.command(description="Follow an engine or edit its alerts")
    @app_commands.describe(
        engine="Pick a family for all versions, or pick one version",
        rating_list="Limit rating alerts to one list, or pick All rating lists",
    )
    async def follow(self, interaction: discord.Interaction, engine: str, rating_list: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        target = await asyncio.to_thread(self.bot.store.resolve, engine)
        if target is None:
            public = await asyncio.to_thread(self.bot.store.catalog_is_public)
            message = (
                "Choose an engine from the Cope suggestions." if public
                else "Engine follows are available when the Cope site is public."
            )
            await interaction.edit_original_response(content=message)
            return
        subscriptions = await asyncio.to_thread(self.bot.store.follows, interaction.user.id)
        current = next((
            row for row in subscriptions
            if (row["family_id"], row["version_id"]) == (target.family_id, target.version_id)
        ), None)
        kinds = current["kinds"] if current else DEFAULT_ALERTS
        list_id = current["rating_list_id"] if current else None
        list_label = (current["rating_list"] or f'List #{list_id}') if list_id is not None else "All rating lists"
        if rating_list == "all":
            list_id = None
            list_label = "All rating lists"
        elif rating_list:
            selected = await asyncio.to_thread(self.bot.store.rating_list, rating_list)
            if selected is None:
                await interaction.edit_original_response(content="Choose a rating list from the Cope suggestions.")
                return
            list_id, list_label = selected["id"], selected["name"]
        scope = "This version" if target.version_id else "All versions, including future releases"
        view = FollowMenu(self.bot, interaction.user.id, target, kinds, list_id)
        await interaction.edit_original_response(
            content=f"**{safe(target.label)}**\n{scope} · Ratings from {safe(list_label)}\nChoose the alerts to receive by DM.",
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @follow.autocomplete("engine")
    async def find_engine(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self.choices(self.bot.store.choices, current)

    @follow.autocomplete("rating_list")
    async def find_list(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        choices = await self.choices(self.bot.store.rating_choices, current)
        if not current or "all rating lists".startswith(current.lower().strip()):
            choices = [app_commands.Choice(name="All rating lists", value="all"), *choices[:24]]
        return choices

    @app_commands.command(description="View your followed engines and DM status")
    async def follows(self, interaction: discord.Interaction, page: app_commands.Range[int, 1, 10] = 1) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        follows = await asyncio.to_thread(self.bot.store.follows, user_id)
        if not follows:
            await interaction.edit_original_response(content="You have no follows. Use `/cope follow` to add an engine.")
            return
        state = await asyncio.to_thread(self.bot.store.subscriber, user_id)
        status = "DMs blocked" if state["dm_blocked"] else "Paused" if state["paused"] else "Active"
        pages = (len(follows) + 4) // 5
        page = min(page, pages)
        lines = [f"**Your follows** · {status} · Page {page}/{pages}"]
        for row in follows[(page - 1) * 5:page * 5]:
            label = row["label"] or (
                f'Engine #{row["family_id"]} / version #{row["version_id"]}'
                if row["version_id"] else f'Engine #{row["family_id"]}'
            )
            ratings = f' · {safe(row["rating_list"], 45)}' if row["rating_list"] else ""
            alerts = ", ".join(ALERTS[kind] for kind in row["kinds"])
            scope = "" if row["version_id"] else " · All versions"
            lines.append(f'**{safe(label, 55)}**{scope}\n{alerts}{ratings}')
        lines.append("Edit with `/cope follow` or remove with `/cope unfollow`.")
        await interaction.edit_original_response(content="\n\n".join(lines), allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(description="Remove alerts for an engine or version")
    async def unfollow(self, interaction: discord.Interaction, engine: str) -> None:
        await interaction.response.defer(ephemeral=True)
        removed = await asyncio.to_thread(self.bot.store.unfollow, interaction.user.id, engine)
        await interaction.edit_original_response(content="Follow removed." if removed else "Choose one of your follows.")

    @unfollow.autocomplete("engine")
    async def find_follow(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self.choices(self.bot.store.choices, current, interaction.user.id)

    @app_commands.command(description="Pause all Cope notification DMs")
    async def pause(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        paused = await asyncio.to_thread(self.bot.store.pause, interaction.user.id)
        await interaction.edit_original_response(content="Alerts paused." if paused else "You have no follows.")

    @app_commands.command(description="Resume alerts or recheck your DMs")
    async def resume(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id
        state = await asyncio.to_thread(self.bot.store.subscriber, user_id)
        if not state:
            message = "Use `/cope follow` to add an engine first."
        elif not (state["paused"] or state["dm_blocked"]):
            message = "Your alerts are already active."
        elif await self.bot.confirm_dm(user_id):
            await asyncio.to_thread(self.bot.store.resume, user_id)
            message = "Alerts are active again."
        else:
            message = "Discord blocked the confirmation DM. Enable DMs from the bot and try again."
        await interaction.edit_original_response(content=message)

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        LOG.error("Cope slash command failed", exc_info=error)
        message = "Cope could not complete the command. Please try again."
        if interaction.response.is_done():
            await interaction.edit_original_response(content=message)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class CopeBot(discord.Client):
    def __init__(self, store: Store, origin: str, guild_id: int | None) -> None:
        super().__init__(intents=discord.Intents.none(), allowed_mentions=discord.AllowedMentions.none(),
                         max_ratelimit_timeout=30)
        self.store = store
        self.origin = origin
        self.guild_id = guild_id
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(CopeCommands(self))

    async def setup_hook(self) -> None:
        await asyncio.to_thread(self.store.check_schema)
        if self.guild_id:
            guild = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self.project_facts.start()
        self.send_alerts.start()
        self.prune_facts.start()

    async def close(self) -> None:
        self.project_facts.cancel()
        self.send_alerts.cancel()
        self.prune_facts.cancel()
        await super().close()

    async def confirm_dm(self, user_id: int) -> bool:
        try:
            async with asyncio.timeout(DELIVERY_TIMEOUT):
                channel = await self.create_dm(discord.Object(id=user_id))
                await channel.send(
                    "Cope can send your opted-in engine alerts here. Use `/cope follows` to manage them.",
                )
        except (discord.Forbidden, discord.NotFound):
            return False
        return True

    @tasks.loop(seconds=1)
    async def project_facts(self) -> None:
        try:
            await asyncio.to_thread(self.store.project)
        except (psycopg.Error, sqlite3.Error):
            LOG.exception("Could not queue Cope alerts")
            await asyncio.sleep(5)

    @tasks.loop(hours=1)
    async def prune_facts(self) -> None:
        try:
            await asyncio.to_thread(self.store.prune)
        except (psycopg.Error, sqlite3.Error):
            LOG.exception("Could not remove expired Cope alerts")

    @tasks.loop(seconds=1)
    async def send_alerts(self) -> None:
        await self.wait_until_ready()
        try:
            for _ in range(10):
                batch = await asyncio.to_thread(self.store.claim)
                if batch is None:
                    break
                await self.deliver(batch)
        except (psycopg.Error, sqlite3.Error):
            LOG.exception("Could not process Cope deliveries")
            await asyncio.sleep(5)

    async def deliver(self, batch: Batch) -> None:
        facts = await asyncio.to_thread(self.store.eligible, batch)
        ids = [fact["id"] for fact in facts]
        if not ids:
            await asyncio.to_thread(self.store.finish, batch, ids, "suppressed")
            return
        age = (datetime.now(UTC) - batch.first_attempt_at).total_seconds()
        if batch.attempts > 3 or age + DELIVERY_TIMEOUT > NONCE_RETRY_WINDOW:
            await asyncio.to_thread(self.store.finish, batch, ids, "uncertain")
            return

        outcome = "failed"
        message_id = None
        retry = 0.0
        try:
            async with asyncio.timeout(DELIVERY_TIMEOUT):
                channel = await self.create_dm(discord.Object(id=batch.user_id))
                facts = await asyncio.to_thread(self.store.eligible, batch)
                ids = [fact["id"] for fact in facts]
                if ids:
                    message = await channel.send(
                        embeds=[embed_for(fact, self.origin) for fact in facts],
                        nonce=batch.nonce,
                    )
                    message_id = str(message.id)
                    outcome = "sent"
        except discord.NotFound:
            outcome = "blocked"
        except discord.Forbidden as error:
            if error.code == 50007:
                outcome = "blocked"
            else:
                LOG.warning("Discord denied a delivery with code %s", error.code)
        except discord.RateLimited as error:
            retry = max(1.0, error.retry_after)
        except discord.HTTPException as error:
            if error.status >= 500:
                outcome = "uncertain"
                retry = 5.0 * batch.attempts
            else:
                LOG.warning("Discord rejected a delivery with HTTP %s and code %s", error.status, error.code)
        except (TimeoutError, aiohttp.ClientError, OSError):
            outcome = "uncertain"
            retry = 5.0 * batch.attempts
        except (KeyError, TypeError, ValueError):
            LOG.exception("Could not render a Cope notification")
        age = (datetime.now(UTC) - batch.first_attempt_at).total_seconds()
        if batch.attempts >= 3 or age + retry + DELIVERY_TIMEOUT > NONCE_RETRY_WINDOW:
            retry = 0.0
        await asyncio.to_thread(self.store.finish, batch, ids, outcome, message_id, retry)
