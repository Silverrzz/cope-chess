from __future__ import annotations

import ipaddress
import os
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import Query as QueryParameter
from pydantic import Field

from cope.adapters.http.browser_identity import request_browser_principal
from cope.application import (
    ApplicationError,
    CommandRequest,
    Principal,
    execute_command,
)
from cope.application.contracts import Input, QueryRequest
from cope.application.queries import execute_query
from cope.control.api import application_result, database
from cope.db import get_platform_settings
from cope.discord_bot.accounts import request_account, require_guild_role
from cope.domains.forms import participation, public_results
from cope.domains.forms.application import SubmitResponse
from cope.domains.forms.resources import chess_position, engine_resource, game_embed, rating_embed, resource_choices
from cope.infrastructure.postgres.credentials import authenticate_bearer

TRUSTED_PROXIES = tuple(ipaddress.ip_network(value.strip()) for value in os.environ.get(
    "COPE_FORMS_TRUSTED_PROXY_CIDRS", "127.0.0.1/32,::1/128").split(",") if value.strip())


def client_address(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    try:
        current = ipaddress.ip_address(peer)
        forwarded = request.headers.get("x-forwarded-for", "")
        if len(forwarded) > 1024:
            return peer
        hops = forwarded.split(",") if forwarded else []
        if len(hops) > 10:
            return peer
        for hop in reversed(hops):
            if not any(current in network for network in TRUSTED_PROXIES):
                break
            current = ipaddress.ip_address(hop.strip())
        return str(current)
    except ValueError:
        return peer


def require_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site form writes are not permitted")
    if origin:
        expected = "https://" + os.environ["COPE_DOMAIN"] if os.environ.get("COPE_DOMAIN") else str(request.base_url).rstrip("/")
        actual, wanted = urlsplit(origin), urlsplit(expected)
        if (actual.scheme, actual.netloc) != (wanted.scheme, wanted.netloc):
            raise HTTPException(403, "The form request origin is not permitted")


def form_principal(request: Request, connection=Depends(database)):
    from cope.adapters.http.authentication import (
        admin_session_role,
        admin_token,
        csrf_token_valid,
        manager_token,
        request_is_secure_or_local,
    )

    if not request_is_secure_or_local(request):
        raise HTTPException(403, "Forms require HTTPS")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        require_origin(request)
    supplied = request.headers.get("authorization", "")
    if supplied:
        scheme, _, token = supplied.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "A valid bearer credential is required")
        try:
            identity = authenticate_bearer(connection, token)
            if not isinstance(identity, Principal):
                identity = Principal(identity.actor, tuple(identity.scopes), identity.token_id)
            if identity.credential_kind == "form_claim" and get_platform_settings(connection).privatise_platform:
                raise HTTPException(401, "Sign in to access forms on this private platform")
            return identity
        except ApplicationError as error:
            raise HTTPException(error.status, error.payload()) from error
    role = admin_session_role(request)
    if request.headers.get("x-cope-discord") == "1" and (role or not get_platform_settings(connection).privatise_platform):
        profile = request_account(request, connection)
        if profile:
            return profile.principal
    if role:
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            token = admin_token(request) if role == "admin" else manager_token(request)
            if not token or not csrf_token_valid(request, token, request.headers.get("x-csrf-token", "")):
                raise HTTPException(403, "CSRF validation failed")
        try:
            return request_browser_principal(request)
        except ApplicationError as error:
            raise HTTPException(error.status,error.payload()) from error
    if get_platform_settings(connection).privatise_platform:
        raise HTTPException(401, "Sign in to access forms on this private platform")
    profile = request_account(request, connection)
    return profile.principal if profile else Principal("public", ())


class PositionInput(Input):
    fen: str = Field(default="", max_length=120)
    pgn: str = Field(default="", max_length=32000)


def register_form_routes(app):
    router = APIRouter()

    @router.get("/resources/search")
    def search_resources(kind: Literal["tournament", "game", "rating_list", "engine"],
                         q: str = QueryParameter(default="", max_length=100),
                         tournament: int | None = QueryParameter(default=None, ge=1, le=999999999999999),
                         identity=Depends(form_principal), connection=Depends(database)):
        try:
            return {"items": resource_choices(connection, kind, q, tournament)}
        except ApplicationError as error:
            raise HTTPException(error.status, error.payload()) from error

    @router.get("/resources/engine/{engine_id}")
    def engine(engine_id: int, identity=Depends(form_principal), connection=Depends(database)):
        if not 1 <= engine_id <= 999999999999999:
            raise HTTPException(422, "Choose a valid engine.")
        try:
            return engine_resource(connection, engine_id)
        except ApplicationError as error:
            raise HTTPException(error.status, error.payload()) from error

    @router.post("/resources/position")
    def position(payload: PositionInput, identity=Depends(form_principal)):
        try:
            return chess_position(payload.fen, payload.pgn)
        except ApplicationError as error:
            raise HTTPException(error.status, error.payload()) from error

    @router.get("/resources/game/{tournament_id}/{game_id}")
    def embedded_game(tournament_id: int, game_id: int, identity=Depends(form_principal), connection=Depends(database)):
        try:
            if not 1 <= tournament_id <= 999999999999999 or not 1 <= game_id <= 999999999999999:
                raise ApplicationError("invalid_resource", "Choose a valid game.", 422)
            return game_embed(connection, tournament_id, game_id)
        except ApplicationError as error:
            raise HTTPException(error.status, error.payload()) from error

    @router.get("/resources/rating-list/{rating_list_id}")
    def embedded_rating_list(rating_list_id: int, identity=Depends(form_principal), connection=Depends(database)):
        try:
            if not 1 <= rating_list_id <= 999999999999999:
                raise ApplicationError("invalid_resource", "Choose a valid rating list.", 422)
            return rating_embed(connection, rating_list_id)
        except ApplicationError as error:
            raise HTTPException(error.status, error.payload()) from error

    @router.get("/datetime-options")
    def datetime_options(local_date: str, local_time: str, timezone: str,
                         identity=Depends(form_principal), connection=Depends(database)):
        return application_result(connection, identity, execute_query, QueryRequest(query="forms.datetime_options.v1",
                                  input={"local_date": local_date, "local_time": local_time, "timezone": timezone}))

    @router.get("/responses")
    def responses(limit: int = QueryParameter(default=30, ge=1, le=100), cursor: str | None = None,
                  form_id: str | None = None, show_closed: bool = False,
                  identity=Depends(form_principal), connection=Depends(database)):
        request = QueryRequest(query="forms.responses.list.v1", input={"limit": limit, "cursor": cursor, "form_id": form_id,
                                                                      "show_closed": show_closed})
        return application_result(connection, identity, execute_query, request)

    @router.get("/responses/collections")
    def response_collections(limit: int = QueryParameter(default=30, ge=1, le=100), cursor: str | None = None,
                             show_closed: bool = False, identity=Depends(form_principal), connection=Depends(database)):
        request = QueryRequest(query="forms.responses.collections.v1", input={"limit": limit, "cursor": cursor,
                                                                             "show_closed": show_closed})
        return application_result(connection, identity, execute_query, request)

    @router.get("/responses/mine")
    def my_submissions(limit: int = QueryParameter(default=30, ge=1, le=100), cursor: str | None = None,
                       form_id: str | None = None, show_closed: bool = False,
                       identity=Depends(form_principal), connection=Depends(database)):
        request = QueryRequest(query="forms.responses.mine.v1", input={"limit": limit, "cursor": cursor, "form_id": form_id,
                                                                      "show_closed": show_closed})
        return application_result(connection, identity, execute_query, request)

    @router.get("/responses/{response_id}")
    def response(response_id: str, identity=Depends(form_principal), connection=Depends(database)):
        return application_result(connection, identity, execute_query, QueryRequest(query="forms.responses.detail.v1", target=response_id))

    @router.post("/commands")
    def command(payload: CommandRequest, identity=Depends(form_principal), connection=Depends(database)):
        if payload.command not in {"forms.amend.v1", "forms.withdraw.v1"}:
            raise HTTPException(422, "This endpoint accepts form amendments and withdrawals.")
        return application_result(connection, identity, execute_command, payload)

    @router.get("/directory/list")
    def directory(limit: int = QueryParameter(default=24, ge=1, le=50), offset: int = QueryParameter(default=0, ge=0),
                  show_closed: bool = False, identity=Depends(form_principal), connection=Depends(database)):
        return application_result(connection, identity, public_results.directory, limit, offset, show_closed)

    @router.get("/{slug}/results")
    def results(slug: str, cursor: str | None = None, limit: int = QueryParameter(default=10, ge=1, le=20),
                identity=Depends(form_principal), connection=Depends(database)):
        return application_result(connection, identity, public_results.results, slug, cursor, limit)

    @router.post("/{slug}/submit")
    def submit(slug: str, payload: SubmitResponse, request: Request, identity=Depends(form_principal), connection=Depends(database)):
        profile = request_account(request, connection)
        if not profile:
            raise HTTPException(401, "Sign in with Discord to submit this form.")
        try:
            _, _, definition = participation.submission_release(connection.native_connection(), slug, payload.release_id)
            role = definition.discord_role_id
            guild = None
            connection.commit()
            if role:
                runtime = request.app.state.discord_runtime
                settings = runtime.read_settings()
                guild = settings["guild_id"]
                require_guild_role(runtime, settings, profile.id, role)
            current = request_account(request, connection)
            if not current or current.id != profile.id:
                raise ApplicationError("authentication_required", "Sign in with Discord to submit this form.", 401)
            return application_result(connection, current.principal, lambda raw, actor: participation.submit(
                raw, current, slug, payload, role, guild,
            ))
        except ApplicationError as error:
            connection.rollback()
            raise HTTPException(error.status, error.payload()) from error

    @router.get("/{slug}")
    def public_form(slug: str, request: Request, identity=Depends(form_principal), connection=Depends(database)):
        actor = request_account(request, connection)
        query = QueryRequest(query="forms.public.v1", input={"slug": slug})

        def view(raw, principal):
            result = execute_query(raw, principal, query)
            if actor:
                submitted, response_id = participation.participation(raw, result["data"]["id"], actor.id)
                result["data"].update(submitted=submitted, my_response_id=response_id)
            return result

        return application_result(connection, actor.principal if actor else identity, view)

    app.include_router(router, prefix="/api/forms", tags=["Forms"])
