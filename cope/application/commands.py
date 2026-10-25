from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from .errors import ApplicationError
from .identity import Principal


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Metadata(Input):
    name: str = Field(min_length=1, max_length=80)
    author: str = Field(max_length=120)


class Rename(Input):
    name: str = Field(min_length=1, max_length=160)


class Drain(Input):
    draining: StrictBool


class DrainResult(Drain):
    active_games: Literal["continue"]


class CommandReceipt(Input):
    receipt_id: str
    status: Literal["completed"]
    command: Literal["agents.set_drain.v1", "engines.update_metadata.v1", "competitions.rename.v1"]
    object_id: str
    legacy_id: int
    version_channel: Literal["content_revision", "state_version"]
    basis_version: int
    version: int
    output: Metadata | Rename | DrainResult


class CommandRequest(Input):
    command: Literal["agents.set_drain.v1", "engines.update_metadata.v1", "competitions.rename.v1"]
    target: str = Field(min_length=1, max_length=100)
    expected_version: int = Field(strict=True, gt=0)
    idempotency_key: str = Field(min_length=1, max_length=160)
    input: dict[str, Any]


@dataclass(frozen=True)
class Command:
    name: str
    type_key: str
    table: str
    namespace: str
    scope: str
    version_channel: str
    input_model: type[BaseModel]


COMMANDS = {
    item.name: item for item in (
        Command("agents.set_drain.v1", "agent", "workers", "legacy.worker", "workers:write", "state_version", Drain),
        Command("engines.update_metadata.v1", "engine", "engines", "legacy.engine", "engines:write", "content_revision", Metadata),
        Command("competitions.rename.v1", "competition", "tournaments", "legacy.tournament", "tournaments:write", "content_revision", Rename),
    )
}


def list_commands(principal: Principal) -> list[dict]:
    return [
        {"name": item.name, "type": item.type_key, "scope": item.scope,
         "version_channel": item.version_channel, "input_schema": item.input_model.model_json_schema()}
        for item in COMMANDS.values() if "*" in principal.scopes or item.scope in principal.scopes
    ]


def current_principal(connection, principal: Principal) -> Principal:
    if principal.token_id is None:
        return principal
    if principal.actor != f"token:{principal.token_id}":
        raise ApplicationError("credential_mismatch", "The credential does not match the acting identity.", 403)
    row = connection.execute(
        """SELECT scopes FROM control_tokens
           WHERE id = %s AND revoked_at IS NULL AND expires_at > clock_timestamp() FOR SHARE""",
        (principal.token_id,),
    ).fetchone()
    if row is None:
        raise ApplicationError("credential_revoked", "The initiating credential is no longer valid.", 403)
    allowed = set(json.loads(row["scopes"]))
    effective = principal.scopes if "*" in allowed else tuple(allowed if "*" in principal.scopes else allowed.intersection(principal.scopes))
    return Principal(principal.actor, effective, principal.token_id)


def resolve_target(connection, command: Command, target: str, *, lock: bool = False) -> dict:
    try:
        object_id = uuid.UUID(target)
    except ValueError:
        if not target.isascii() or not target.isdecimal() or int(target) < 1 or len(target) > 18:
            raise ApplicationError("invalid_target", "Supply an Object UUID or a positive legacy identifier.", 422)
        clause, parameter = "id = %s", int(target)
    else:
        clause, parameter = "object_id = %s", object_id
    row = connection.execute(
        f"SELECT id, object_id FROM {command.table} WHERE {clause}" + (" FOR UPDATE" if lock else ""),
        (parameter,),
    ).fetchone()
    if row is None:
        raise ApplicationError("not_found", "The requested resource does not exist.", 404)
    return dict(row)


def describe_target(connection, principal: Principal, command_name: str, target: str) -> dict:
    command = COMMANDS.get(command_name)
    if command is None:
        raise ApplicationError("unknown_command", "Unknown application command.", 404)
    principal = current_principal(connection, principal)
    principal.require(command.scope)
    try:
        identity = uuid.UUID(target)
    except ValueError:
        if not target.isascii() or not target.isdecimal() or len(target) > 18 or int(target) < 1:
            raise ApplicationError("invalid_target", "Supply an Object UUID or a positive legacy identifier.", 422)
        predicate, value = "d.id = %s", int(target)
    else:
        predicate, value = "d.object_id = %s", identity
    fields = "d.name, d.author" if command.type_key == "engine" else "d.name" if command.type_key == "competition" else "COALESCE(s.draining, FALSE) AS draining"
    join = " LEFT JOIN control_worker_settings s ON s.worker_id = d.id" if command.type_key == "agent" else ""
    row = connection.execute(
        f"""SELECT d.id, d.object_id, o.{command.version_channel} AS version, {fields}
            FROM {command.table} d JOIN application_objects o ON o.id = d.object_id AND o.type_key = %s
            {join} WHERE {predicate}""", (command.type_key, value),
    ).fetchone()
    if row is None:
        raise ApplicationError("not_found", "The requested resource does not exist.", 404)
    values = {key: row[key] for key in command.input_model.model_fields}
    return {"object_id": str(row["object_id"]), "legacy_id": row["id"], "type": command.type_key,
            "version_channel": command.version_channel, "version": row["version"], "input": values}


def execute_command(connection, principal: Principal, request: CommandRequest) -> dict:
    command = COMMANDS[request.command]
    principal = current_principal(connection, principal)
    principal.require(command.scope)
    try:
        payload = command.input_model.model_validate(request.input)
    except ValidationError as exc:
        errors = [{"path": list(error["loc"]), "code": error["type"], "message": error["msg"]}
                  for error in exc.errors(include_input=False, include_context=False, include_url=False)]
        raise ApplicationError("invalid_input", "Check the supplied fields.", 422, fields=errors) from exc
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (json.dumps([principal.actor, request.idempotency_key]),),
    )
    row = resolve_target(connection, command, request.target, lock=True)
    canonical = {"command": command.name, "target": str(row["object_id"]),
                 "expected_version": request.expected_version, "input": payload.model_dump(mode="json")}
    fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    existing = connection.execute(
        "SELECT request_hash, result FROM application_command_receipts WHERE actor = %s AND idempotency_key = %s",
        (principal.actor, request.idempotency_key),
    ).fetchone()
    principal = current_principal(connection, principal)
    principal.require(command.scope)
    if existing is not None:
        if existing["request_hash"] != fingerprint:
            raise ApplicationError("idempotency_conflict", "This idempotency key was used for another request.")
        return existing["result"]
    current = connection.execute(
        f"SELECT {command.version_channel} AS version FROM application_objects WHERE id = %s AND type_key = %s FOR UPDATE",
        (row["object_id"], command.type_key),
    ).fetchone()
    if current is None:
        raise ApplicationError("identity_mismatch", "The resource identity is inconsistent.")
    if current["version"] != request.expected_version:
        raise ApplicationError("revision_conflict", "The resource changed. Refresh before applying this edit.",
                               current_version=current["version"], version_channel=command.version_channel)
    if command.name == "engines.update_metadata.v1":
        connection.execute("UPDATE engines SET name = %s, author = %s WHERE id = %s", (payload.name, payload.author, row["id"]))
        output = {"name": payload.name, "author": payload.author}
    elif command.name == "competitions.rename.v1":
        connection.execute("UPDATE tournaments SET name = %s WHERE id = %s", (payload.name, row["id"]))
        output = {"name": payload.name}
    else:
        connection.execute(
            """INSERT INTO control_worker_settings(worker_id, draining, changed_by) VALUES (%s, %s, %s)
               ON CONFLICT(worker_id) DO UPDATE SET draining = EXCLUDED.draining,
               changed_by = EXCLUDED.changed_by, changed_at = clock_timestamp()""",
            (row["id"], payload.draining, principal.actor),
        )
        output = {"draining": payload.draining, "active_games": "continue"}
    updated = connection.execute(
        f"SELECT {command.version_channel} AS version FROM application_objects WHERE id = %s", (row["object_id"],)
    ).fetchone()
    receipt_id = str(uuid.uuid4())
    result = {"receipt_id": receipt_id, "status": "completed", "command": command.name,
              "object_id": str(row["object_id"]), "legacy_id": row["id"],
              "version_channel": command.version_channel, "basis_version": request.expected_version,
              "version": updated["version"], "output": output}
    connection.execute(
        """INSERT INTO application_command_receipts(id, actor, idempotency_key, command, request_hash, object_id, result)
           VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)""",
        (receipt_id, principal.actor, request.idempotency_key, command.name, fingerprint, row["object_id"], json.dumps(result)),
    )
    connection.execute(
        "INSERT INTO application_outbox(object_id, receipt_id, kind, payload) VALUES (%s, %s, %s, %s::jsonb)",
        (row["object_id"], receipt_id, "command.completed", json.dumps(result)),
    )
    return result


def execute_compatibility(connection, principal: Principal, command_name: str, target: str, fields: dict, key: str) -> dict:
    command = COMMANDS[command_name]
    principal = current_principal(connection, principal)
    principal.require(command.scope)
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (json.dumps([principal.actor, key]),))
    row = resolve_target(connection, command, target, lock=True)
    prior = connection.execute(
        "SELECT result FROM application_command_receipts WHERE actor = %s AND idempotency_key = %s",
        (principal.actor, key),
    ).fetchone()
    if prior is None:
        version = connection.execute(
            f"SELECT {command.version_channel} AS version FROM application_objects WHERE id = %s FOR UPDATE",
            (row["object_id"],),
        ).fetchone()["version"]
    else:
        version = prior["result"]["basis_version"]
    return execute_command(connection, principal, CommandRequest(
        command=command_name, target=target, expected_version=version, idempotency_key=key, input=fields,
    ))
