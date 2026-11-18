from urllib.error import HTTPError, URLError
from uuid import UUID

from fastapi import Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse

from cope.adapters.http.authentication import admin_session_role, request_is_secure_or_local
from cope.application import ApplicationError
from cope.adapters.http.forms import require_origin
from cope.control.api import database
from cope.version import app_version
from cope.discord_bot.accounts import COOKIE, DISCORD_API, decrypt, discord_json, request_account, require_guild_role, snowflake
from cope.domains import quorum_elections as elections
from cope.domains import quorum_publications as published
from cope.domains.quorum import Setup, WitnessUpdate, read_setup, save_setup


def require_secure(request):
    if not request_is_secure_or_local(request):
        raise HTTPException(403, "Quorum requires HTTPS.")


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


def discord_get(guild, token, path):
    try:
        return discord_json(f"{DISCORD_API}/guilds/{guild}/{path}", authorization="Bot " + token, expect_list=True)
    except HTTPError as error:
        if error.code == 429:
            raise HTTPException(429, "Discord is rate limiting searches. Try again shortly.") from error
        if error.code in {401, 403}:
            raise HTTPException(503, "The configured bot cannot access this Discord server directory.") from error
        raise HTTPException(502, "Discord could not return the server directory.") from error
    except (URLError, TimeoutError, ValueError) as error:
        raise HTTPException(502, "Discord’s server directory is temporarily unavailable.") from error


def check_role(request, connection):
    require_secure(request)
    account = request_account(request, connection)
    if not account:
        raise HTTPException(401, "Connect your Discord account to check eligibility.")
    setup = read_setup(connection)
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
    latest = read_setup(connection)
    if not current or current.id != account.id:
        raise HTTPException(401, "Your Discord session changed. Sign in again.")
    if latest.enrollment_role_id != setup.enrollment_role_id or runtime.read_settings()["guild_id"] != settings["guild_id"]:
        raise HTTPException(409, "Quorum’s enrollment settings changed. Check eligibility again.")
    return account, setup.enrollment_role_id


def election_call(operation, *args):
    try:
        return operation(*args)
    except ApplicationError as error:
        raise HTTPException(error.status, error.message) from error


def register_quorum_routes(app):
    @app.get("/api/quorum")
    def public_directory(request: Request, connection=Depends(database)):
        require_secure(request)
        setup = read_setup(connection)
        return {
            "ballots": elections.directory(connection),
            "eligibility_check_available": setup.enrollment_role_id is not None,
        }

    @app.get("/api/quorum/eligibility")
    def check_eligibility(request: Request, connection=Depends(database)):
        account, _ = check_role(request, connection)
        origin = request.app.state.discord_runtime.origin
        row = connection.execute(
            "SELECT commitment FROM cope_quorum.credentials WHERE account_id = ?", (account.id,),
        ).fetchone()
        commitment = row["commitment"] if row else None
        session = request.cookies[COOKIE]
        return JSONResponse({
            "eligible": True,
            "scope": elections.enrollment_scope(origin, session),
            "commitment": commitment,
            "replacement_scope": elections.enrollment_scope(origin, session, commitment) if commitment else None,
        }, headers={"Cache-Control": "no-store"})

    @app.post("/api/quorum/enroll")
    def enroll_credential(payload: elections.Enrollment, request: Request, connection=Depends(database)):
        require_origin(request)
        account, role = check_role(request, connection)
        if int(payload.commitment) >= elections.FIELD or payload.proof.message != "0":
            raise HTTPException(422, "Invalid enrollment statement.")
        origin = request.app.state.discord_runtime.origin
        scope = elections.enrollment_scope(origin, request.cookies[COOKIE], payload.replaces)
        connection.commit()
        if not election_call(elections.proof_worker, [payload.commitment], payload.proof, scope)["valid"]:
            raise HTTPException(422, "The credential possession proof is invalid.")
        if request_account(request, connection) != account:
            raise HTTPException(401, "The Discord session changed.")
        election_call(elections.enroll, connection, account.id, payload.commitment, role, payload.replaces)
        return {"commitment": payload.commitment}

    @app.get("/api/quorum/ballots/{ballot_id}")
    def ballot_package(ballot_id: UUID, request: Request, connection=Depends(database)):
        require_secure(request)
        return election_call(elections.ballot_package, connection, ballot_id)

    @app.get("/api/quorum/ballots/{ballot_id}/publications")
    def ballot_publications(ballot_id: UUID, request: Request, connection=Depends(database)):
        require_secure(request)
        return election_call(published.publications, connection, ballot_id)

    @app.get("/api/quorum/ballots/{ballot_id}/witness-reports/{public_key}")
    def witness_report(
        ballot_id: UUID,
        request: Request,
        public_key: str = Path(pattern=r"^[a-f0-9]{64}$"),
        connection=Depends(database),
    ):
        require_secure(request)
        origin = request.app.state.discord_runtime.origin
        return election_call(published.witness_report, connection, ballot_id, public_key, origin)

    @app.post("/api/quorum/ballots/{ballot_id}/witness-signatures")
    def sign_ballot(ballot_id: UUID, payload: published.WitnessSignature, request: Request, connection=Depends(database)):
        require_secure(request)
        require_origin(request)
        origin = request.app.state.discord_runtime.origin
        return election_call(published.record_signature, connection, ballot_id, origin, payload)

    @app.get("/api/quorum/ballots/{ballot_id}/disclosure")
    def disclosure_challenge(ballot_id: UUID, request: Request, connection=Depends(database)):
        require_secure(request)
        account = request_account(request, connection)
        if not account:
            raise HTTPException(401, "Connect Discord to publish your vote.")
        election_call(published.closed_evidence, connection, ballot_id)
        message = published.disclosure_message(ballot_id, account.id, request.cookies[COOKIE])
        return JSONResponse({"message": message, "username": account.username}, headers={"Cache-Control": "no-store"})

    @app.post("/api/quorum/ballots/{ballot_id}/disclosure")
    def disclose_vote(ballot_id: UUID, payload: published.Disclosure, request: Request, connection=Depends(database)):
        require_secure(request)
        require_origin(request)
        account = request_account(request, connection)
        if not account:
            raise HTTPException(401, "Connect Discord to publish your vote.")
        message = published.disclosure_message(ballot_id, account.id, request.cookies[COOKIE])
        election_call(published.validate_disclosure, connection, ballot_id, payload, message)
        current = request_account(request, connection)
        if current != account:
            raise HTTPException(401, "The Discord session changed. Preview your vote again.")
        return election_call(published.record_disclosure, connection, ballot_id, account, payload.proof.nullifier)

    @app.post("/api/quorum/verifiers/check-in")
    def verifier_checkin(payload: published.VerifierCheckin, request: Request, connection=Depends(database)):
        require_secure(request)
        require_origin(request)
        return election_call(published.record_checkin, connection, request.app.state.discord_runtime.origin, payload, app_version())

    @app.get("/api/admin/quorum/witnesses", name="admin_quorum_verifier_directory")
    def verifier_directory(request: Request, connection=Depends(database)):
        require_admin(request)
        return published.verifier_directory(connection)

    @app.put("/api/admin/quorum/witnesses", name="admin_quorum_update_witnesses")
    def update_witnesses(payload: WitnessUpdate, request: Request, connection=Depends(database)):
        require_admin(request)
        setup = read_setup(connection)
        if setup.revision != payload.revision:
            raise HTTPException(409, "Quorum settings changed. Refresh the witness list and try again.")
        updated = election_call(save_setup, connection, setup.model_copy(update={"witnesses": payload.witnesses}))
        if updated is None:
            raise HTTPException(409, "Quorum settings changed. Refresh the witness list and try again.")
        return published.verifier_directory(connection)

    @app.get("/api/admin/quorum/status", name="admin_quorum_election_status")
    def election_status(request: Request, connection=Depends(database)):
        require_admin(request)
        return elections.admin_status(connection)

    @app.post("/api/admin/quorum/publish", name="admin_quorum_publish_ballot")
    def publish_ballot(payload: elections.BallotPublication, request: Request, connection=Depends(database)):
        require_admin(request)
        return election_call(elections.publish, connection, payload.draft_id, payload.revision)

    @app.post("/api/quorum/ballots/{ballot_id}/votes")
    def cast(ballot_id: UUID, payload: elections.Proof, request: Request, connection=Depends(database)):
        require_secure(request)
        require_origin(request)
        return election_call(elections.cast_vote, connection, ballot_id, payload)

    @app.get("/api/admin/quorum", name="admin_quorum_get_setup")
    def get_setup(request: Request, connection=Depends(database)):
        require_admin(request)
        return read_setup(connection)

    @app.get("/api/admin/quorum/discord-roles", name="admin_quorum_discord_roles")
    def discord_roles(request: Request):
        require_admin(request)
        guild, token = discord_connection(request)
        roles = discord_get(guild, token, "roles")
        if not isinstance(roles, list):
            raise HTTPException(502, "Discord returned an invalid role list.")
        return {"roles": [{"id": role["id"], "name": role["name"]} for role in reversed(roles)
                          if isinstance(role, dict) and snowflake(role.get("id"))
                          and role["id"] != guild and isinstance(role.get("name"), str)]}

    @app.put("/api/admin/quorum", name="admin_quorum_update_setup")
    def update_setup(payload: Setup, request: Request, connection=Depends(database)):
        require_admin(request)
        guild = request.app.state.discord_runtime.read_settings()["guild_id"]
        if payload.enrollment_role_id and payload.enrollment_role_id == guild:
            raise HTTPException(422, "Select a specific Discord role other than @everyone.")
        updated = election_call(save_setup, connection, payload)
        if updated is None:
            raise HTTPException(409, "Quorum setup changed. Reload before saving.")
        return updated
