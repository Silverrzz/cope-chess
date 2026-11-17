import json
import re
from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from cope.control.api import database
from cope.discord_bot.accounts import CALLBACK_PATH, oauth_ready, read_oauth, save_oauth, snowflake


def oauth_status(runtime, settings: dict) -> dict:
    return {
        "client_id": settings["oauth_client_id"],
        "secret_configured": bool(settings["oauth_encrypted_secret"]),
        "enabled": settings["oauth_enabled"],
        "ready": oauth_ready(settings, runtime),
        "callback_url": runtime.origin + CALLBACK_PATH if runtime.origin else None,
    }


def register_discord_settings(app: FastAPI, session_role: Callable[[Request], str | None]) -> None:
    def require_admin(request: Request) -> None:
        if session_role(request) != "admin":
            raise HTTPException(status_code=403, detail="The admin token is required to manage Discord.")

    @app.get("/api/admin/settings/discord", include_in_schema=False)
    def get_discord_settings(request: Request):
        require_admin(request)
        runtime = app.state.discord_runtime
        return JSONResponse(runtime.status(runtime.read_settings()), headers={"Cache-Control": "no-store"})

    @app.put("/api/admin/settings/discord", include_in_schema=False)
    async def put_discord_settings(request: Request):
        require_admin(request)
        body = await request.body()
        if len(body) > 4096:
            raise HTTPException(status_code=413, detail="Discord settings are too large.")
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid Discord settings.") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Invalid Discord settings.")
        guild_id = payload.get("guild_id") or None
        channel_id = payload.get("channel_id") or None
        enabled = payload.get("enabled")
        token = payload.get("token") or None
        remove = payload.get("remove_token", False)
        for value, label in ((guild_id, "server"), (channel_id, "channel")):
            if value is not None and (
                not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value)
                or int(value) >= 2**64
            ):
                raise HTTPException(status_code=400, detail=f"Enter a valid Discord {label} ID.")
        if token is not None and (
            not isinstance(token, str) or not re.fullmatch(r"[!-~]{16,512}", token)
        ):
            raise HTTPException(status_code=400, detail="Enter a valid Discord bot token.")
        if not isinstance(enabled, bool) or not isinstance(remove, bool) or (token and remove):
            raise HTTPException(status_code=400, detail="Invalid Discord settings.")

        runtime = app.state.discord_runtime
        try:
            saved = await run_in_threadpool(
                runtime.save_settings, guild_id=guild_id, channel_id=channel_id,
                enabled=enabled and not remove, token=token, remove=remove,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return JSONResponse(runtime.status(saved), headers={"Cache-Control": "no-store"})

    @app.get("/api/admin/settings/discord/oauth", include_in_schema=False)
    def get_oauth_settings(request: Request, connection=Depends(database)):
        require_admin(request)
        return JSONResponse(oauth_status(app.state.discord_runtime, read_oauth(connection)),
                            headers={"Cache-Control": "no-store"})

    @app.put("/api/admin/settings/discord/oauth", include_in_schema=False)
    async def put_oauth_settings(request: Request, connection=Depends(database)):
        require_admin(request)
        body = await request.body()
        if len(body) > 4096:
            raise HTTPException(413, "Discord settings are too large.")
        try:
            payload = json.loads(body)
            client_id = payload["client_id"] or None
            secret = payload.get("secret") or None
            enabled = payload["enabled"]
            remove = payload.get("remove_secret", False)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise HTTPException(400, "Invalid Discord sign-in settings.") from None
        if client_id is not None and not snowflake(client_id):
            raise HTTPException(400, "Enter a valid Discord OAuth client ID.")
        if secret is not None and (not isinstance(secret, str) or not re.fullmatch(r"[!-~]{16,512}", secret)):
            raise HTTPException(400, "Enter a valid Discord OAuth client secret.")
        if not isinstance(enabled, bool) or not isinstance(remove, bool) or (secret and remove):
            raise HTTPException(400, "Invalid Discord sign-in settings.")

        def apply():
            saved = save_oauth(connection, app.state.discord_runtime, client_id=client_id, secret=secret,
                               enabled=enabled and not remove, remove=remove)
            connection.commit()
            return oauth_status(app.state.discord_runtime, saved)

        try:
            value = await run_in_threadpool(apply)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        return JSONResponse(value, headers={"Cache-Control": "no-store"})
