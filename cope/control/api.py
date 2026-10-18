from __future__ import annotations

import json
import hashlib
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field

from cope.db import connect_database, list_engine_records, list_git_hosts, list_rating_lists, list_opening_suites
from cope.variants import VARIANTS

from .intake import IntakeRequest, Preset, compile_intake, list_presets, queue_snapshot, save_preset
from .operations import (
    Plan, Principal, audit, authenticate, change_operation, create_operation,
    get_operation, list_operations, mint_token,
)
from .registry import Input, describe_actions


def database(request: Request):
    connection = connect_database(request.app.state.db_path)
    try:
        yield connection
    finally:
        connection.close()


def principal(request: Request, connection=Depends(database)):
    from cope.web.app import _admin_session_role, _request_is_secure_or_local

    if not _request_is_secure_or_local(request):
        raise HTTPException(403, "Control access requires HTTPS")
    supplied = request.headers.get("authorization", "")
    if supplied:
        scheme, _, token = supplied.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "A control bearer token is required", headers={"WWW-Authenticate": "Bearer"})
        try:
            return authenticate(connection, token)
        except PermissionError as error:
            raise HTTPException(401, str(error), headers={"WWW-Authenticate": "Bearer"}) from error
    if request.url.path.startswith("/api/admin/control"):
        role = _admin_session_role(request)
        if role == "admin":
            return Principal("browser:admin", ("*",))
        if role == "manager":
            return Principal("browser:manager", ("operations:read",))
    raise HTTPException(401, "A control bearer token is required", headers={"WWW-Authenticate": "Bearer"})


def perform(connection, handler, *args, **kwargs):
    try:
        result = handler(connection, *args, **kwargs)
        connection.commit()
        return result
    except PermissionError as error:
        connection.rollback()
        raise HTTPException(403, str(error)) from error
    except LookupError as error:
        connection.rollback()
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        connection.rollback()
        raise HTTPException(409, str(error)) from error


class OperationRequest(Input):
    idempotency_key: str = Field(min_length=1, max_length=160)
    preview: bool = False
    plan: Plan


class IntakeOperationRequest(Input):
    idempotency_key: str = Field(min_length=1, max_length=160)
    preview: bool = False
    intake: IntakeRequest


class TokenRequest(Input):
    label: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(min_length=1, max_length=50)
    ttl_days: int = Field(default=30, ge=1, le=365)


class OperationCommand(Input):
    command: Literal["execute", "resume", "cancel"]


class ActionRequest(Input):
    input: dict = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)


def context_payload(connection, identity):
    identity.require("operations:read")
    capabilities = {}
    for row in connection.execute("SELECT build_hash, variant, status, evidence, actor, recorded_at FROM engine_variant_capabilities ORDER BY build_hash, variant"):
        capabilities.setdefault(row["build_hash"], {})[row["variant"]] = {
            key: row[key] for key in ("status", "evidence", "actor", "recorded_at")
        }
    engines = [{"id": item.id, "engine_id": item.engine_id, "name": item.name, "version": item.version,
                "build_hash": item.build_hash, "active": item.active and item.engine_active,
                "repository_full_name": item.repository_full_name, "source_ref": item.source_ref,
                "variant_capabilities": capabilities.get(item.build_hash, {})}
               for item in list_engine_records(connection)]
    return {
        "actor": identity.actor, "scopes": identity.scopes,
        "queue": queue_snapshot(connection), "presets": list_presets(connection),
        "engines": engines,
        "variants": [{"id": item.id, "name": item.name, "rules_revision": item.rules_revision,
                      "chess960": item.chess960, "requires_openings": item.requires_openings,
                      "uci_options": item.uci_options()} for item in VARIANTS.values()],
        "rating_lists": [{"id": item.id, "name": item.name, "variant": item.variant, "rules_revision": item.rules_revision} for item in list_rating_lists(connection)],
        "opening_suites": [{"id": item.id, "name": item.name, "variant": item.variant, "rules_revision": item.rules_revision} for item in list_opening_suites(connection)],
        "worker_controls": [dict(row) for row in connection.execute("SELECT * FROM control_worker_settings ORDER BY worker_id")],
        "git_hosts": [{"id": item.id, "name": item.name, "base_url": item.base_url} for item in list_git_hosts(connection, enabled_only=True)],
        "actions": describe_actions(), "intake_schema": IntakeRequest.model_json_schema(),
        "preset_schema": Preset.model_json_schema(),
        "ordering": ["standard before DFRC", "estimated game duration at 80 moves", "original submission time", "stable legacy position"],
        "features": {"durable_operations": True, "engine_intake": True, "dfrc": True, "duck": True, "duck960": True, "oauth": False},
    }


def submit_intake(connection, identity, request):
    identity.require("intake:write")
    fingerprint = hashlib.sha256(request.intake.model_dump_json().encode()).hexdigest()
    existing = connection.execute("SELECT id, source_request_hash FROM control_operations WHERE actor = ? AND idempotency_key = ?", (identity.actor, request.idempotency_key)).fetchone()
    if existing is not None:
        if existing["source_request_hash"] != fingerprint:
            raise ValueError("Idempotency key was already used for a different request")
        return get_operation(connection, identity, existing["id"])
    plan = compile_intake(connection, request.intake)
    result = create_operation(connection, identity, plan, request.idempotency_key, preview=request.preview)
    connection.execute("UPDATE control_operations SET source_request_hash = ? WHERE id = ?", (fingerprint, result["id"]))
    return result


def register_control_routes(app):
    from .platform import register_platform_actions

    register_platform_actions(app)
    router = APIRouter()

    @router.get("/actions")
    def actions(identity=Depends(principal), connection=Depends(database)):
        def read(connection):
            identity.require("operations:read")
            return describe_actions()
        return perform(connection, read)

    @router.post("/actions/{action_name}")
    def invoke_action(action_name: str, payload: ActionRequest, request: Request, identity=Depends(principal), connection=Depends(database)):
        from .platform import execute_read
        from .registry import catalog
        from .operations import Step

        def execute(connection):
            registered = catalog().get(action_name)
            if registered is None:
                raise LookupError("Control action does not exist")
            identity.require(registered.scope)
            inputs = registered.input_model.model_validate(payload.input)
            if registered.read_only:
                return execute_read(connection, identity, str(request.app.state.db_path), action_name, inputs)
            if payload.idempotency_key is None:
                raise ValueError("An idempotency key is required for writes")
            plan = Plan(title=registered.title, steps=[Step(key="apply", action=action_name, input=inputs.model_dump(mode="json"))])
            return create_operation(connection, identity, plan, payload.idempotency_key)
        return perform(connection, execute)

    @router.get("/context")
    def context(identity=Depends(principal), connection=Depends(database)):
        return perform(connection, context_payload, identity)

    @router.get("/repository")
    def repository(identity=Depends(principal), connection=Depends(database)):
        from .repository import repository_head

        def read(connection):
            identity.require("operations:read")
            return repository_head(connection)
        return perform(connection, read)

    @router.get("/operations")
    def operations(limit: int = Query(default=50, ge=1, le=200), identity=Depends(principal), connection=Depends(database)):
        return perform(connection, list_operations, identity, limit)

    @router.post("/operations", status_code=202)
    def create(payload: OperationRequest, identity=Depends(principal), connection=Depends(database)):
        return perform(connection, create_operation, identity, payload.plan, payload.idempotency_key, preview=payload.preview)

    @router.get("/operations/{operation_id}")
    def operation(operation_id: str, after: int = Query(default=0, ge=0), identity=Depends(principal), connection=Depends(database)):
        return perform(connection, get_operation, identity, operation_id, after=after)

    @router.post("/operations/{operation_id}/commands")
    def command(operation_id: str, payload: OperationCommand, identity=Depends(principal), connection=Depends(database)):
        return perform(connection, change_operation, identity, operation_id, payload.command)

    @router.post("/intake", status_code=202)
    def intake(payload: IntakeOperationRequest, identity=Depends(principal), connection=Depends(database)):
        return perform(connection, submit_intake, identity, payload)

    @router.post("/presets", status_code=201)
    def preset(payload: Preset, identity=Depends(principal), connection=Depends(database)):
        return perform(connection, save_preset, identity, payload)

    @router.get("/tokens")
    def tokens(identity=Depends(principal), connection=Depends(database)):
        def read(connection):
            identity.require("*")
            return [{**dict(row), "scopes": json.loads(row["scopes"])} for row in connection.execute(
                "SELECT id, label, scopes, created_at, expires_at, revoked_at FROM control_tokens ORDER BY created_at DESC"
            )]
        return perform(connection, read)

    @router.post("/tokens", status_code=201)
    def token(payload: TokenRequest, identity=Depends(principal), connection=Depends(database)):
        return perform(connection, mint_token, identity, payload.label, payload.scopes, payload.ttl_days)

    @router.delete("/tokens/{token_id}")
    def revoke(token_id: str, identity=Depends(principal), connection=Depends(database)):
        def update(connection):
            identity.require("*")
            changed = connection.execute("UPDATE control_tokens SET revoked_at = clock_timestamp() WHERE id = ? AND revoked_at IS NULL", (token_id,)).rowcount
            audit(connection, identity.actor, "token.revoked", {"id": token_id})
            return {"revoked": bool(changed)}
        return perform(connection, update)

    app.include_router(router, prefix="/api/control", tags=["Cope Control"])
    app.include_router(router, prefix="/api/admin/control", include_in_schema=False)
