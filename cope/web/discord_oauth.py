from __future__ import annotations

import base64
import hashlib
import re
import secrets
from urllib.parse import unquote, urlencode, urlsplit

from fastapi import Depends, HTTPException, Request
from starlette.responses import JSONResponse, RedirectResponse

from cope.adapters.http.authentication import request_is_secure_or_local
from cope.adapters.http.forms import require_origin
from cope.application.errors import ApplicationError
from cope.control.api import database
from cope.discord_bot.accounts import (
    CALLBACK_PATH, COOKIE, FLOW_COOKIE, SESSION_SECONDS, digest, exchange_code,
    new_session, oauth_ready, read_oauth, request_account,
)

FLOW_SECONDS = 600


def destination(value: str) -> str:
    if len(value) > 1500 or not value.startswith("/") or re.search(r"[\x00-\x20\\]", value):
        return "/"
    decoded = unquote(value)
    if decoded.startswith(("//", "/\\")) or urlsplit(value).netloc or value.startswith("/api/"):
        return "/"
    return value


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def register_discord_oauth(app) -> None:
    @app.get("/api/discord/session")
    def session(request: Request, connection=Depends(database)):
        runtime = request.app.state.discord_runtime
        profile = request_account(request, connection)
        return JSONResponse({
            "enabled": oauth_ready(read_oauth(connection), runtime),
            "account": {"username": profile.username, "display_name": profile.display_name} if profile else None,
        }, headers={"Cache-Control": "no-store"})

    @app.get("/api/discord/oauth/start")
    def start(request: Request, next: str = "/", connection=Depends(database)):
        from cope.web.app import _rate_limited

        if not request_is_secure_or_local(request):
            raise HTTPException(403, "Discord sign-in requires HTTPS.")
        if _rate_limited(request, "discord-oauth", limit=20, window_s=60):
            raise HTTPException(429, "Too many sign-in requests. Please try again shortly.")
        runtime = request.app.state.discord_runtime
        settings = read_oauth(connection)
        if not oauth_ready(settings, runtime):
            raise HTTPException(503, "Discord sign-in is unavailable.")
        state, browser, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        connection.execute("DELETE FROM discord_oauth_flows WHERE expires_at <= clock_timestamp()")
        connection.execute(
            """INSERT INTO discord_oauth_flows(state_hash, browser_hash, verifier, destination, generation, expires_at)
               VALUES (?, ?, ?, ?, ?, clock_timestamp() + make_interval(secs => ?))""",
            (digest(state), digest(browser), verifier, destination(next), settings["oauth_generation"], FLOW_SECONDS),
        )
        connection.commit()
        params = urlencode({
            "client_id": settings["oauth_client_id"], "redirect_uri": runtime.origin + CALLBACK_PATH,
            "response_type": "code", "scope": "identify", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
        })
        response = redirect("https://discord.com/oauth2/authorize?" + params)
        response.set_cookie(FLOW_COOKIE, browser, max_age=FLOW_SECONDS, httponly=True, secure=True,
                            samesite="lax", path=CALLBACK_PATH)
        return response

    @app.get(CALLBACK_PATH)
    def callback(request: Request, state: str = "", code: str = "", connection=Depends(database)):
        runtime = request.app.state.discord_runtime
        browser = request.cookies.get(FLOW_COOKIE, "")
        if not request_is_secure_or_local(request) or not all(
            re.fullmatch(r"[A-Za-z0-9_-]{43}", value) for value in (state, browser)
        ):
            raise HTTPException(400, "This Discord sign-in expired. Start again from Cope.")
        flow = connection.execute(
            """DELETE FROM discord_oauth_flows WHERE state_hash = ? AND browser_hash = ?
               AND expires_at > clock_timestamp() RETURNING destination, verifier, generation""",
            (digest(state), digest(browser)),
        ).fetchone()
        connection.commit()
        if not flow:
            raise HTTPException(400, "This Discord sign-in expired. Start again from Cope.")
        response = redirect(flow["destination"])
        response.delete_cookie(FLOW_COOKIE, path=CALLBACK_PATH, secure=True, httponly=True, samesite="lax")
        if not code:
            return response
        issuer = request.query_params.get("iss")
        if len(code) > 1024 or issuer not in {None, "https://discord.com"}:
            raise HTTPException(400, "Discord returned an invalid sign-in response.")
        settings = read_oauth(connection)
        connection.commit()
        if not oauth_ready(settings, runtime) or settings["oauth_generation"] != flow["generation"]:
            raise HTTPException(409, "Discord settings changed. Start again from Cope.")
        try:
            profile = exchange_code(runtime, settings, code, flow["verifier"])
        except ApplicationError as error:
            raise HTTPException(error.status, error.message) from error
        current = connection.execute(
            "SELECT oauth_generation FROM cope_notifications.discord_settings WHERE singleton FOR SHARE"
        ).fetchone()
        if current["oauth_generation"] != flow["generation"]:
            raise HTTPException(409, "Discord settings changed. Start again from Cope.")
        if previous := request.cookies.get(COOKIE):
            connection.execute("DELETE FROM discord_web_sessions WHERE token_hash = ?", (digest(previous),))
        token = new_session(connection, profile)
        connection.commit()
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=True, samesite="lax")
        return response

    @app.delete("/api/discord/session")
    def logout(request: Request, connection=Depends(database)):
        require_origin(request)
        if token := request.cookies.get(COOKIE):
            connection.execute("DELETE FROM discord_web_sessions WHERE token_hash = ?", (digest(token),))
            connection.commit()
        response = JSONResponse({"account": None}, headers={"Cache-Control": "no-store"})
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="lax")
        return response
