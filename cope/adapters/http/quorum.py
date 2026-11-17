import base64
import binascii

from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_pem_public_key
from fastapi import Depends, HTTPException, Query, Request
from pydantic import Field
from fastapi.responses import JSONResponse

from cope.adapters.http.authentication import admin_session_role, request_is_secure_or_local
from cope.application import ApplicationError
from cope.application.contracts import Input
from cope.adapters.http.forms import require_origin
from cope.control.api import database
from cope.db import get_platform_settings
from cope.discord_bot.accounts import DISCORD_API, decrypt, discord_json, request_account, require_guild_role, snowflake
from cope.domains.quorum import Setup, connect_witness, read_setup, save_setup, witness_keys


def require_access(request, connection):
    if not request_is_secure_or_local(request):
        raise HTTPException(403, "Quorum requires HTTPS.")
    if get_platform_settings(connection).privatise_platform and not admin_session_role(request):
        raise HTTPException(401, "Sign in to access Quorum on this private platform.")


def require_admin(request):
    if admin_session_role(request) != "admin":
        raise HTTPException(403, "Quorum setup requires an administrator.")


def discord_connection(request):
    runtime = request.app.state.discord_runtime
    settings = runtime.read_settings()
    guild = settings["guild_id"]
    if not snowflake(guild) or not settings["encrypted_token"]:
        raise HTTPException(503, "Connect a Discord server and bot in Settings first.")
    try:
        return guild, decrypt(runtime, settings["encrypted_token"])
    except ApplicationError as error:
        raise HTTPException(503, "The configured Discord bot token is unavailable.") from error


def discord_get(guild, token, path, *, expect_list=True):
    try:
        return discord_json(f"{DISCORD_API}/guilds/{guild}/{path}", authorization="Bot " + token, expect_list=expect_list)
    except HTTPError as error:
        if error.code == 429:
            raise HTTPException(429, "Discord is rate limiting searches. Try again shortly.") from error
        if error.code in {401, 403}:
            raise HTTPException(503, "The configured bot cannot access this Discord server directory.") from error
        raise HTTPException(502, "Discord could not return the server directory.") from error
    except (URLError, TimeoutError, ValueError) as error:
        raise HTTPException(502, "Discord’s server directory is temporarily unavailable.") from error


def guild_user(member):
    if not isinstance(member, dict) or member.get("pending"):
        return None
    user = member.get("user")
    if (not isinstance(user, dict) or user.get("bot") or not snowflake(user.get("id"))
            or not isinstance(user.get("username"), str)):
        return None
    label = member.get("nick") or user.get("global_name") or user["username"]
    return {"id": user["id"], "username": user["username"], "display_name": str(label)[:100]}


class WitnessRegistration(Input):
    public_key: str = Field(min_length=32, max_length=250)
    signature: str = Field(min_length=88, max_length=88)


def witness_nominee(request, connection):
    require_access(request, connection)
    account = request_account(request, connection)
    setup, _ = read_setup(connection)
    if not account or account.id not in setup.witness_user_ids:
        raise HTTPException(403, "Connect the Discord account nominated as a witness.")
    return account


def witness_statement(request, account_id):
    origin = request.app.state.discord_runtime.origin
    if not origin:
        raise HTTPException(503, "Configure Cope’s public origin before registering a witness key.")
    return f"cope-quorum-witness/1\n{origin}\n{account_id}\n"


def register_quorum_routes(app):
    @app.get("/api/quorum")
    def public_directory(request: Request, connection=Depends(database)):
        if not request_is_secure_or_local(request):
            raise HTTPException(403, "Quorum requires HTTPS.")
        setup, _ = read_setup(connection)
        return {
            "ballots": [],
            "eligibility_check_available": setup.enrollment_role_id is not None,
        }

    @app.get("/api/quorum/eligibility")
    def check_eligibility(request: Request, connection=Depends(database)):
        require_access(request, connection)
        account = request_account(request, connection)
        if not account:
            raise HTTPException(401, "Connect your Discord account to check eligibility.")
        setup, _ = read_setup(connection)
        if not setup.enrollment_role_id:
            raise HTTPException(409, "An enrollment role has not been configured.")
        runtime = request.app.state.discord_runtime
        settings = runtime.read_settings()
        connection.commit()
        if setup.enrollment_role_id == settings["guild_id"]:
            raise HTTPException(503, "Quorum requires a specific role other than @everyone.")
        try:
            require_guild_role(runtime, settings, account.id, setup.enrollment_role_id)
        except ApplicationError as error:
            message = ("Your Discord account does not have the Quorum enrollment role."
                       if error.status == 403 else "Quorum’s Discord role check is temporarily unavailable.")
            raise HTTPException(error.status, message) from error
        current = request_account(request, connection)
        latest, _ = read_setup(connection)
        if not current or current.id != account.id:
            raise HTTPException(401, "Your Discord session changed. Sign in again.")
        if latest.enrollment_role_id != setup.enrollment_role_id or runtime.read_settings()["guild_id"] != settings["guild_id"]:
            raise HTTPException(409, "Quorum’s enrollment settings changed. Check eligibility again.")
        return JSONResponse({"eligible": True}, headers={"Cache-Control": "no-store"})

    @app.get("/api/admin/quorum")
    def get_setup(request: Request, connection=Depends(database)):
        require_admin(request)
        return read_setup(connection)[0]

    @app.get("/api/quorum/witness")
    def witness_status(request: Request, connection=Depends(database)):
        account = witness_nominee(request, connection)
        key = witness_keys(connection).get(account.id)
        return JSONResponse({"statement": witness_statement(request, account.id), "public_key": key},
                            headers={"Cache-Control": "no-store"})

    @app.post("/api/quorum/witness")
    def register_witness(payload: WitnessRegistration, request: Request, connection=Depends(database)):
        require_origin(request)
        account = witness_nominee(request, connection)
        guild, token = discord_connection(request)
        connection.commit()
        member = discord_get(guild, token, f"members/{account.id}", expect_list=False)
        if not guild_user(member) or member["user"]["id"] != account.id:
            raise HTTPException(403, "The nominee must be a member of the connected Discord server.")
        if witness_nominee(request, connection).id != account.id or request.app.state.discord_runtime.read_settings()["guild_id"] != guild:
            raise HTTPException(409, "The connected account or Discord server changed. Retry registration.")
        try:
            key = load_pem_public_key(payload.public_key.encode("ascii"))
            if not isinstance(key, Ed25519PublicKey):
                raise ValueError("Expected an Ed25519 key")
            signature = base64.b64decode(payload.signature, validate=True)
            key.verify(signature, witness_statement(request, account.id).encode("utf-8"))
        except (ValueError, binascii.Error, InvalidSignature) as error:
            raise HTTPException(422, "The signature does not match the Ed25519 public key and enrollment statement.") from error
        public_key = base64.b64encode(key.public_bytes(Encoding.Raw, PublicFormat.Raw)).decode("ascii")
        try:
            connect_witness(connection, account.id, public_key)
        except ApplicationError as error:
            raise HTTPException(error.status, error.message) from error
        return {"public_key": public_key}

    @app.get("/api/admin/quorum/witnesses")
    def registered_witnesses(request: Request, connection=Depends(database)):
        require_admin(request)
        setup, _ = read_setup(connection)
        keys = witness_keys(connection)
        return {"registered": [user for user in setup.witness_user_ids if user in keys]}

    @app.get("/api/admin/quorum/discord-roles")
    def discord_roles(request: Request):
        require_admin(request)
        guild, token = discord_connection(request)
        roles = discord_get(guild, token, "roles")
        if not isinstance(roles, list):
            raise HTTPException(502, "Discord returned an invalid role list.")
        return {"roles": [{"id": role["id"], "name": role["name"]} for role in reversed(roles)
                          if isinstance(role, dict) and snowflake(role.get("id"))
                          and role["id"] != guild and isinstance(role.get("name"), str)]}

    @app.get("/api/admin/quorum/discord-users")
    def discord_users(request: Request, q: str = Query(min_length=2, max_length=64)):
        require_admin(request)
        guild, token = discord_connection(request)
        members = discord_get(guild, token, "members/search?" + urlencode({"query": q, "limit": 20}))
        if not isinstance(members, list):
            raise HTTPException(502, "Discord returned an invalid member list.")
        users = []
        for member in members:
            user = guild_user(member)
            if user:
                users.append(user)
        return {"users": users}

    @app.put("/api/admin/quorum")
    def update_setup(payload: Setup, request: Request, connection=Depends(database)):
        require_admin(request)
        guild = request.app.state.discord_runtime.read_settings()["guild_id"]
        if payload.enrollment_role_id and payload.enrollment_role_id == guild:
            raise HTTPException(422, "Select a specific Discord role other than @everyone.")
        updated = save_setup(connection, payload)
        if updated is None:
            raise HTTPException(409, "Quorum setup changed. Reload before saving.")
        return updated
