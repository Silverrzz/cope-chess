import asyncio
import base64
import hmac
import logging
import os
import sqlite3
from contextlib import closing
from time import monotonic
from urllib.parse import urlsplit

import discord
from cryptography.fernet import Fernet, InvalidToken

from cope.db import connect_database

from .bot import CopeBot
from .store import Store


LOG = logging.getLogger(__name__)
SETTINGS = "cope_notifications.discord_settings"


class DiscordRuntime:
    def __init__(self, database_url: str, bootstrap_token: str | None) -> None:
        self.database_url = database_url
        domain = os.getenv("COPE_DOMAIN", "").strip()
        origin = urlsplit(f"https://{domain}")
        self.origin = origin.geturl() if (
            origin.hostname and not (origin.username or origin.password or origin.path or origin.query or origin.fragment)
        ) else ""
        self.cipher = None
        if bootstrap_token:
            purpose = f"cope-discord-token-v1:{self.origin}".encode()
            self.cipher = Fernet(base64.urlsafe_b64encode(hmac.digest(bootstrap_token.encode(), purpose, "sha256")))
        self.revision = -1
        self.state = "starting"
        self.client: CopeBot | None = None
        self.session: asyncio.Task | None = None
        self.retry_at = 0.0

    def read_settings(self) -> dict:
        with closing(connect_database(self.database_url)) as connection:
            return connection.execute(f"""
                SELECT guild_id, encrypted_token, enabled, revision
                FROM {SETTINGS} WHERE singleton
            """).fetchone()

    def save_settings(self, *, guild_id: str | None, enabled: bool, token: str | None, remove: bool) -> dict:
        if (enabled or token) and (self.cipher is None or not self.origin):
            raise ValueError("Cope needs a configured domain and bootstrap admin secret to enable Discord.")
        replacement = self.cipher.encrypt(token.encode()) if token and self.cipher else None
        with closing(connect_database(self.database_url)) as connection:
            existing = connection.execute(f"SELECT * FROM {SETTINGS} WHERE singleton FOR UPDATE").fetchone()
            encrypted = None if remove else replacement or existing["encrypted_token"]
            if enabled and (not guild_id or not encrypted):
                raise ValueError("Enter a Discord server ID and bot token before enabling notifications.")
            row = connection.execute(f"""
                UPDATE {SETTINGS}
                SET guild_id = ?, enabled = ?, encrypted_token = ?, revision = revision + 1,
                    active_since = CASE WHEN guild_id IS DISTINCT FROM ? OR enabled IS DISTINCT FROM ?
                        THEN clock_timestamp() ELSE active_since END
                WHERE singleton
                RETURNING guild_id, encrypted_token, enabled, revision
            """, (guild_id, enabled, encrypted, guild_id, enabled)).fetchone()
            connection.commit()
            return row

    def status(self, row: dict) -> dict:
        if not row["encrypted_token"]:
            state = "not_configured"
        elif not row["enabled"]:
            state = "disabled"
        elif self.revision != row["revision"]:
            state = "starting"
        elif self.client is not None and self.client.is_ready():
            state = "online"
        else:
            state = self.state
        return {
            "guild_id": row["guild_id"],
            "enabled": row["enabled"],
            "token_configured": bool(row["encrypted_token"]),
            "state": state,
        }

    async def stop(self) -> None:
        if self.session is not None:
            self.session.cancel()
            await asyncio.gather(self.session, return_exceptions=True)
        self.session = None
        self.client = None

    async def run(self) -> None:
        try:
            while True:
                try:
                    await self.refresh()
                except sqlite3.Error:
                    LOG.warning("Could not read Discord notification settings")
                await asyncio.sleep(4)
        finally:
            await self.stop()

    async def refresh(self) -> None:
        row = await asyncio.to_thread(self.read_settings)
        if row["revision"] != self.revision:
            await self.stop()
            self.revision = row["revision"]
            self.retry_at = 0.0
        if not row["enabled"] or monotonic() < self.retry_at:
            return
        if self.session is not None and not self.session.done():
            return
        try:
            if self.cipher is None or not self.origin:
                raise InvalidToken
            token = self.cipher.decrypt(row["encrypted_token"]).decode()
        except InvalidToken:
            self.state = "credential_error"
            self.retry_at = float("inf")
            return
        self.state = "connecting"
        self.client = CopeBot(Store(self.database_url), self.origin, int(row["guild_id"]))
        self.session = asyncio.create_task(self.serve(self.client, token), name="discord-gateway")

    async def serve(self, client: CopeBot, token: str) -> None:
        try:
            async with client:
                await client.start(token)
        except discord.LoginFailure:
            self.state = "invalid_token"
            self.retry_at = float("inf")
        except (discord.Forbidden, discord.NotFound):
            self.state = "server_access_error"
            self.retry_at = monotonic() + 30
        except Exception as error:
            LOG.warning("Discord connection failed (%s)", type(error).__name__)
            self.state = "connection_error"
            self.retry_at = monotonic() + 30
