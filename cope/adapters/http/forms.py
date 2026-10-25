from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query as QueryParameter, Request

from cope.application import ApplicationError, CommandRequest, Principal, execute_command
from cope.application.contracts import QueryRequest
from cope.application.queries import execute_query
from cope.application.identity import browser_principal
from cope.control.api import application_result, database
from cope.domains.forms.storage import issue_claim
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
    from cope.web.app import _admin_session_role, _admin_token, _csrf_token_valid, _manager_token, _request_is_secure_or_local

    if not _request_is_secure_or_local(request):
        raise HTTPException(403, "Forms require HTTPS")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        require_origin(request)
    supplied = request.headers.get("authorization", "")
    if supplied:
        scheme, _, token = supplied.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "A valid bearer credential is required")
        try:
            return authenticate_bearer(connection, token)
        except ApplicationError as error:
            raise HTTPException(error.status, error.payload()) from error
    role = _admin_session_role(request)
    if role:
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            token = _admin_token(request) if role == "admin" else _manager_token(request)
            if not token or not _csrf_token_valid(request, token, request.headers.get("x-csrf-token", "")):
                raise HTTPException(403, "CSRF validation failed")
        return browser_principal(role)
    return Principal("public", ())


def register_form_routes(app):
    router = APIRouter()

    @router.get("/datetime-options")
    def datetime_options(local_date: str, local_time: str, timezone: str,
                         identity=Depends(form_principal), connection=Depends(database)):
        return application_result(connection, identity, execute_query, QueryRequest(query="forms.datetime_options.v1",
                                  input={"local_date": local_date, "local_time": local_time, "timezone": timezone}))

    @router.get("/responses")
    def responses(limit: int = QueryParameter(default=30, ge=1, le=100), cursor: str | None = None,
                  form_id: str | None = None, identity=Depends(form_principal), connection=Depends(database)):
        request = QueryRequest(query="forms.responses.list.v1", input={"limit": limit, "cursor": cursor, "form_id": form_id})
        return application_result(connection, identity, execute_query, request)

    @router.get("/responses/{response_id}")
    def response(response_id: str, identity=Depends(form_principal), connection=Depends(database)):
        return application_result(connection, identity, execute_query, QueryRequest(query="forms.responses.detail.v1", target=response_id))

    @router.post("/commands")
    def command(payload: CommandRequest, identity=Depends(form_principal), connection=Depends(database)):
        if payload.command not in {"forms.submit.v1", "forms.amend.v1", "forms.withdraw.v1"}:
            raise HTTPException(422, "This endpoint accepts form submission and amendment commands")
        return application_result(connection, identity, execute_command, payload)

    @router.post("/{slug}/claims")
    def claim(slug: str, request: Request, identity=Depends(form_principal), connection=Depends(database)):
        from cope.infrastructure.postgres.application import run_application

        try:
            return run_application(connection, identity, lambda raw, actor: issue_claim(raw, slug, client_address(request)))
        except ApplicationError as error:
            raise HTTPException(error.status, error.payload()) from error

    @router.get("/{slug}")
    def public_form(slug: str, identity=Depends(form_principal), connection=Depends(database)):
        return application_result(connection, identity, execute_query, QueryRequest(query="forms.public.v1", input={"slug": slug}))

    app.include_router(router, prefix="/api/forms", tags=["Forms"])
