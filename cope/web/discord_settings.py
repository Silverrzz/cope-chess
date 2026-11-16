import json
import re
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse


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
        enabled = payload.get("enabled")
        token = payload.get("token") or None
        remove = payload.get("remove_token", False)
        if guild_id is not None and (
            not isinstance(guild_id, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", guild_id)
            or int(guild_id) >= 2**64
        ):
            raise HTTPException(status_code=400, detail="Enter a valid Discord server ID.")
        if token is not None and (
            not isinstance(token, str) or not re.fullmatch(r"[!-~]{16,512}", token)
        ):
            raise HTTPException(status_code=400, detail="Enter a valid Discord bot token.")
        if not isinstance(enabled, bool) or not isinstance(remove, bool) or (token and remove):
            raise HTTPException(status_code=400, detail="Invalid Discord settings.")

        runtime = app.state.discord_runtime
        try:
            saved = await run_in_threadpool(
                runtime.save_settings, guild_id=guild_id, enabled=enabled and not remove, token=token, remove=remove,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return JSONResponse(runtime.status(saved), headers={"Cache-Control": "no-store"})
