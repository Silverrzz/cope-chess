from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from cope.adapters.http.authentication import admin_session_role, request_is_secure_or_local
from cope.application import ApplicationError
from cope.control.api import database
from cope.db import get_platform_settings
from cope.discord_bot.accounts import request_account, require_guild_role
from cope.domains.quorum import Setup, read_setup, save_setup


def require_access(request, connection):
    if not request_is_secure_or_local(request):
        raise HTTPException(403, "Quorum requires HTTPS.")
    if get_platform_settings(connection).privatise_platform and not admin_session_role(request):
        raise HTTPException(401, "Sign in to access Quorum on this private platform.")


def require_admin(request):
    if admin_session_role(request) != "admin":
        raise HTTPException(403, "Quorum setup requires an administrator.")


def register_quorum_routes(app):
    @app.get("/api/quorum")
    def public_status(request: Request, connection=Depends(database)):
        require_access(request, connection)
        setup, _ = read_setup(connection)
        return {
            "status": "setup",
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

    @app.get("/api/admin/quorum/discord-users")
    def discord_users(request: Request, q: str = Query(min_length=2, max_length=64), connection=Depends(database)):
        require_admin(request)
        users = connection.execute(
            """SELECT id, username, display_name FROM discord_accounts
               WHERE strpos(lower(username), lower(?)) > 0 OR strpos(lower(display_name), lower(?)) > 0
               ORDER BY lower(username), id LIMIT 20""",
            (q, q),
        ).fetchall()
        return {"users": [dict(user) for user in users]}

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
