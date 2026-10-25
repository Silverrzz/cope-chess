from __future__ import annotations

import hashlib
import json
import uuid

from pydantic import ValidationError

from .contracts import CommandContext, CommandReceipt, CommandRequest
from .errors import ApplicationError
from .identity import Principal, current_principal
from .legacy_resources import commands as legacy_commands


def command_catalog() -> dict:
    from cope.domains.forms.application import commands as form_commands

    providers = (legacy_commands(), form_commands())
    result = {}
    for provider in providers:
        for command in provider:
            if command.name in result:
                raise RuntimeError(f"Duplicate application command: {command.name}")
            result[command.name] = command
    return result


COMMANDS = command_catalog()


def list_commands(principal: Principal) -> list[dict]:
    return [
        {"name": item.name, "type": item.type_key, "scope": item.scope, "version_channel": item.version_channel,
         "requires_target": item.requires_target, "requires_version": item.requires_version,
         "input_schema": item.input_model.model_json_schema(), "output_schema": item.output_model.model_json_schema()}
        for item in COMMANDS.values() if "*" in principal.scopes or item.scope in principal.scopes
    ]


def get_command(name: str):
    command = COMMANDS.get(name)
    if command is None:
        raise ApplicationError("unknown_command", "Unknown application command.", 404)
    return command


def describe_target(connection, principal: Principal, command_name: str, target: str) -> dict:
    command = get_command(command_name)
    principal = current_principal(connection, principal)
    principal.require(command.scope)
    if not command.requires_target:
        raise ApplicationError("target_not_applicable", "This creation command has no existing target.", 422)
    resolved = command.resolve(connection, principal, target, lock=False)
    return command.describe(connection, principal, resolved)


def execute_command(connection, principal: Principal, request: CommandRequest) -> dict:
    command = get_command(request.command)
    principal = current_principal(connection, principal)
    principal.require(command.scope)
    if command.requires_target != (request.target is not None):
        raise ApplicationError("target_required" if command.requires_target else "target_not_applicable", "Check this command's target contract.", 422)
    if command.requires_version != (request.expected_version is not None):
        raise ApplicationError("version_required" if command.requires_version else "version_not_applicable", "Check this command's version contract.", 422)
    try:
        payload = command.input_model.model_validate(request.input)
    except ValidationError as exc:
        errors = [{"path": list(error["loc"]), "code": error["type"], "message": error["msg"]}
                  for error in exc.errors(include_input=False, include_context=False, include_url=False)]
        raise ApplicationError("invalid_input", "Check the supplied fields.", 422, fields=errors) from exc
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (json.dumps([principal.actor, request.idempotency_key]),))
    target = command.resolve(connection, principal, request.target, lock=True) if command.requires_target else None
    canonical = {"command": command.name, "target": target.object_id if target else None,
                 "expected_version": request.expected_version, "input": payload.model_dump(mode="json")}
    try:
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        raise ApplicationError("invalid_input", "The command must contain supported JSON values.", 422) from exc
    if len(encoded) > 2097152:
        raise ApplicationError("command_too_large", "This command exceeds its input limit.", 413)
    fingerprint = hashlib.sha256(encoded).hexdigest()
    existing = connection.execute(
        "SELECT request_hash, result FROM application_command_receipts WHERE actor = %s AND idempotency_key = %s",
        (principal.actor, request.idempotency_key),
    ).fetchone()
    principal = current_principal(connection, principal)
    principal.require(command.scope)
    if existing is not None:
        if existing["request_hash"] != fingerprint:
            raise ApplicationError("idempotency_conflict", "This idempotency key was used for another request.")
        return command.project(connection, principal, existing["result"])
    if command.requires_version and target.version != request.expected_version:
        raise ApplicationError("revision_conflict", "The resource changed. Refresh before applying this edit.",
                               current_version=target.version, version_channel=command.version_channel)
    outcome = command.handle(CommandContext(connection, principal, target, request), payload)
    output = command.output_model.model_validate(outcome.output).model_dump(mode="json")
    receipt_id = str(uuid.uuid4())
    result = CommandReceipt(receipt_id=receipt_id, status="completed", command=command.name, object_id=outcome.object_id,
                            legacy_id=outcome.legacy_id, version_channel=command.version_channel,
                            basis_version=request.expected_version, version=outcome.version, output=output).model_dump(mode="json")
    connection.execute(
        """INSERT INTO application_command_receipts(id, actor, idempotency_key, command, request_hash, object_id, result)
           VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)""",
        (receipt_id, principal.actor, request.idempotency_key, command.name, fingerprint, outcome.object_id, json.dumps(result)),
    )
    event = {key: result[key] for key in ("receipt_id", "command", "object_id", "status", "version_channel", "version")}
    connection.execute(
        "INSERT INTO application_outbox(object_id, receipt_id, kind, payload) VALUES (%s, %s, %s, %s::jsonb)",
        (outcome.object_id, receipt_id, "command.completed", json.dumps(event)),
    )
    return command.project(connection, principal, result)


def execute_compatibility(connection, principal: Principal, command_name: str, target: str, fields: dict, key: str) -> dict:
    command = get_command(command_name)
    principal = current_principal(connection, principal)
    principal.require(command.scope)
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (json.dumps([principal.actor, key]),))
    resolved = command.resolve(connection, principal, target, lock=True)
    prior = connection.execute("SELECT result FROM application_command_receipts WHERE actor = %s AND idempotency_key = %s", (principal.actor, key)).fetchone()
    version = resolved.version if prior is None else prior["result"]["basis_version"]
    return execute_command(connection, principal, CommandRequest(command=command_name, target=target,
                           expected_version=version, idempotency_key=key, input=fields))
