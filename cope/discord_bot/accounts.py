from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cryptography.fernet import InvalidToken

from cope.application.errors import ApplicationError
from cope.application.identity import Principal

COOKIE = "cope_discord_session"
FLOW_COOKIE = "cope_discord_flow"
SESSION_SECONDS = 30 * 24 * 60 * 60
SETTINGS = "cope_notifications.discord_settings"
TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_API = "https://discord.com/api/v10"
CALLBACK_PATH = "/api/discord/oauth/callback"
DISCORD_SCOPES = ("forms:submit", "forms:amend", "forms:responses:read", "artifacts:write", "artifacts:read")


@dataclass(frozen=True)
class DiscordAccount:
    id: str
    username: str
    display_name: str

    @property
    def principal(self) -> Principal:
        return Principal("discord:" + self.id, DISCORD_SCOPES, credential_kind="discord")


def snowflake(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value) is not None and int(value) < 2**64


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def account(connection, token: str) -> DiscordAccount | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return None
    row = connection.execute(
        f"""SELECT a.id, a.username, a.display_name
            FROM discord_web_sessions s JOIN discord_accounts a ON a.id = s.account_id
            WHERE s.token_hash = ? AND s.expires_at > clock_timestamp()
              AND (SELECT oauth_enabled FROM {SETTINGS} WHERE singleton)""",
        (digest(token),),
    ).fetchone()
    return DiscordAccount(**dict(row)) if row is not None else None


def request_account(request, connection) -> DiscordAccount | None:
    return account(connection, request.cookies.get(COOKIE, ""))


def read_oauth(connection) -> dict:
    return connection.execute(f"""SELECT oauth_client_id, oauth_encrypted_secret, oauth_enabled, oauth_generation
                                  FROM {SETTINGS} WHERE singleton""").fetchone()


def oauth_ready(row: dict, runtime) -> bool:
    return bool(row["oauth_enabled"] and row["oauth_client_id"] and row["oauth_encrypted_secret"]
                and runtime.origin and runtime.cipher)


def save_oauth(connection, runtime, *, client_id: str | None, secret: str | None, remove: bool, enabled: bool) -> dict:
    existing = connection.execute(f"SELECT * FROM {SETTINGS} WHERE singleton FOR UPDATE").fetchone()
    if (secret or enabled) and (not runtime.origin or not runtime.cipher):
        raise ValueError("Cope needs a configured domain and bootstrap admin secret for Discord login.")
    encrypted = None if remove else runtime.cipher.encrypt(secret.encode()) if secret else existing["oauth_encrypted_secret"]
    if client_id != existing["oauth_client_id"] and not secret:
        encrypted = None
    if enabled and (not client_id or not encrypted):
        raise ValueError("Enter the Discord OAuth client ID and client secret before enabling sign-in.")
    changed = (client_id, encrypted, enabled) != (
        existing["oauth_client_id"], existing["oauth_encrypted_secret"], existing["oauth_enabled"],
    )
    connection.execute(f"""UPDATE {SETTINGS}
                            SET oauth_client_id = ?, oauth_encrypted_secret = ?, oauth_enabled = ?,
                                oauth_generation = oauth_generation + ?
                            WHERE singleton""",
                       (client_id, encrypted, enabled, int(changed)))
    if changed:
        connection.execute("DELETE FROM discord_web_sessions")
        connection.execute("DELETE FROM discord_oauth_flows")
    return read_oauth(connection)


def decrypt(runtime, value: bytes) -> str:
    if not runtime.cipher:
        raise ApplicationError("discord_unavailable", "Discord is not configured.", 503)
    try:
        return runtime.cipher.decrypt(bytes(value)).decode()
    except (InvalidToken, UnicodeError) as exc:
        raise ApplicationError("discord_unavailable", "The saved Discord credentials need to be updated.", 503) from exc


def discord_json(url: str, *, authorization: str | None = None, body: dict | None = None) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "Cope Chess (https://github.com/Silverrzz/cope-chess)"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urlencode(body).encode()
    if authorization:
        headers["Authorization"] = authorization
    with urlopen(Request(url, headers=headers, data=data), timeout=6) as response:
        result = json.loads(response.read(1024 * 1024))
    if not isinstance(result, dict):
        raise ValueError("Discord returned an invalid response.")
    return result


def exchange_code(runtime, settings: dict, code: str, verifier: str) -> DiscordAccount:
    secret = decrypt(runtime, settings["oauth_encrypted_secret"])
    try:
        token = discord_json(TOKEN_URL, body={
            "grant_type": "authorization_code", "code": code, "client_id": settings["oauth_client_id"],
            "client_secret": secret, "redirect_uri": runtime.origin + CALLBACK_PATH,
            "code_verifier": verifier,
        })["access_token"]
        profile = discord_json(f"{DISCORD_API}/users/@me", authorization="Bearer " + token)
        user_id, username = profile["id"], profile["username"]
        name = profile.get("global_name") or username
        if not snowflake(user_id) or not isinstance(username, str) or not 1 <= len(username) <= 64:
            raise ValueError("Invalid Discord profile.")
        if not isinstance(name, str) or not 1 <= len(name) <= 64:
            name = username
        return DiscordAccount(user_id, username, name)
    except (URLError, TimeoutError, ValueError, KeyError, TypeError) as exc:
        raise ApplicationError("discord_login_failed", "Discord sign-in failed. Please try again.", 502) from exc


def require_guild_role(runtime, settings: dict, user_id: str, role: str) -> None:
    guild_id, token = settings["guild_id"], settings["encrypted_token"]
    if not guild_id or not token:
        raise ApplicationError("discord_guild_unavailable", "This form requires an active Discord server connection.", 503)
    try:
        member = discord_json(f"{DISCORD_API}/guilds/{guild_id}/members/{user_id}",
                              authorization="Bot " + decrypt(runtime, token))
    except HTTPError as exc:
        if exc.code == 404:
            raise ApplicationError("discord_role_required", "Join the Cope Discord and get the required role to submit.", 403) from exc
        raise ApplicationError("discord_guild_unavailable", "Discord role verification is temporarily unavailable.", 503) from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise ApplicationError("discord_guild_unavailable", "Discord role verification is temporarily unavailable.", 503) from exc
    roles = member.get("roles")
    if not isinstance(roles, list):
        raise ApplicationError("discord_guild_unavailable", "Discord role verification is temporarily unavailable.", 503)
    if member.get("pending") or (role != guild_id and role not in roles):
        raise ApplicationError("discord_role_required", "Your Discord account does not have the role required for this form.", 403)


def new_session(connection, profile: DiscordAccount) -> str:
    token = secrets.token_urlsafe(32)
    connection.execute(
        """INSERT INTO discord_accounts(id, username, display_name) VALUES (?, ?, ?)
           ON CONFLICT (id) DO UPDATE SET username = EXCLUDED.username, display_name = EXCLUDED.display_name""",
        (profile.id, profile.username, profile.display_name),
    )
    connection.execute("DELETE FROM discord_web_sessions WHERE expires_at <= clock_timestamp()")
    connection.execute(
        """INSERT INTO discord_web_sessions(token_hash, account_id, expires_at)
           VALUES (?, ?, clock_timestamp() + make_interval(secs => ?))""",
        (digest(token), profile.id, SESSION_SECONDS),
    )
    return token
