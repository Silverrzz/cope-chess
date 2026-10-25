from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import Field, model_validator

from .registry import Input, catalog


def dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class Principal:
    actor: str
    scopes: tuple[str, ...]
    token_id: str | None = None

    def require(self, scope: str) -> None:
        if "*" not in self.scopes and scope not in self.scopes:
            raise PermissionError(f"Missing control scope: {scope}")

    def can_access(self, operation: dict) -> bool:
        return "*" in self.scopes or self.actor == operation["actor"]


class Step(Input):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    action: str = Field(min_length=1, max_length=100)
    input: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, max_length=500)


class Plan(Input):
    title: str = Field(min_length=1, max_length=200)
    steps: list[Step] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_graph(self):
        seen = set()
        for step in self.steps:
            if step.key in seen or any(key not in seen for key in step.depends_on):
                raise ValueError("Step keys must be unique and dependencies must precede their step")
            seen.add(step.key)
        return self


def audit(connection, actor: str, kind: str, detail: dict, operation_id: str | None = None):
    connection.execute(
        "INSERT INTO control_events(operation_id, actor, kind, detail) VALUES (?, ?, ?, ?)",
        (operation_id, actor, kind, dump(detail)),
    )


def mint_token(connection, principal: Principal, label: str, scopes: list[str], ttl_days: int):
    principal.require("*")
    known = {item.scope for item in catalog().values()} | {
        "operations:read", "operations:write", "intake:write", "presets:write", "*",
    }
    from cope.application.commands import COMMANDS
    from cope.application.queries import QUERIES

    known |= {item.scope for item in COMMANDS.values()} | {item.scope for item in QUERIES.values() if item.scope}
    if not scopes or set(scopes) - known:
        raise ValueError("Choose valid, nonempty control scopes")
    token = "cope_ctl_" + secrets.token_urlsafe(40)
    token_id = str(uuid.uuid4())
    connection.execute(
        "INSERT INTO control_tokens(id, label, token_hash, scopes, expires_at) VALUES (?, ?, ?, ?, ?)",
        (token_id, label, hashlib.sha256(token.encode()).hexdigest(), dump(sorted(set(scopes))),
         datetime.now(UTC) + timedelta(days=ttl_days)),
    )
    audit(connection, principal.actor, "token.created", {"id": token_id, "label": label, "scopes": scopes})
    return {"id": token_id, "token": token}


def authenticate(connection, token: str) -> Principal:
    row = connection.execute(
        "SELECT id, scopes FROM control_tokens WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > clock_timestamp()",
        (hashlib.sha256(token.encode()).hexdigest(),),
    ).fetchone()
    if row is None:
        raise PermissionError("Invalid, expired or revoked control token")
    return Principal(f"token:{row['id']}", tuple(json.loads(row["scopes"])), row["id"])


def validate_grant(connection, operation):
    principal = Principal(operation["actor"], tuple(json.loads(operation["scopes"])), operation["token_id"])
    if principal.token_id:
        row = connection.execute(
            "SELECT scopes FROM control_tokens WHERE id = ? AND revoked_at IS NULL AND expires_at > clock_timestamp()",
            (principal.token_id,),
        ).fetchone()
        if row is None:
            raise PermissionError("Initiating control credential expired or was revoked")
        current = set(json.loads(row["scopes"]))
        scopes = principal.scopes if "*" in current else tuple(set(principal.scopes) & current)
        principal = Principal(principal.actor, scopes, principal.token_id)
    return principal


def create_operation(connection, principal: Principal, plan: Plan, key: str, *, preview=False):
    principal.require("operations:write")
    actions = catalog()
    normalized = plan.model_dump(mode="json")
    for step in normalized["steps"]:
        registered = actions.get(step["action"])
        if registered is None:
            raise ValueError(f"Unknown control action: {step['action']}")
        principal.require(registered.scope)
        step["input"] = registered.input_model.model_validate(step["input"]).model_dump(mode="json")
        for field in ("registration_step", "publish_step", "pull_step", "queue_step"):
            dependency = step["input"].get(field)
            if dependency and dependency not in step["depends_on"]:
                raise ValueError(f"{field} must be a direct dependency")
    request_hash = hashlib.sha256(dump(normalized).encode()).hexdigest()
    operation_id = str(uuid.uuid4())
    inserted = connection.execute(
        """INSERT INTO control_operations
           (id, actor, token_id, scopes, idempotency_key, request_hash, title, plan, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(actor, idempotency_key) DO NOTHING
           RETURNING id""",
        (operation_id, principal.actor, principal.token_id, dump(principal.scopes), key,
         request_hash, plan.title, dump(normalized), "planned" if preview else "pending"),
    ).fetchone()
    if inserted is None:
        existing = connection.execute(
            "SELECT id, request_hash FROM control_operations WHERE actor = ? AND idempotency_key = ?",
            (principal.actor, key),
        ).fetchone()
        if existing["request_hash"] != request_hash:
            raise ValueError("Idempotency key was already used for a different plan")
        return get_operation(connection, principal, existing["id"])
    for position, step in enumerate(normalized["steps"]):
        connection.execute(
            """INSERT INTO control_steps(operation_id, key, position, action, input, dependencies)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (operation_id, step["key"], position, step["action"], dump(step["input"]), dump(step["depends_on"])),
        )
    audit(connection, principal.actor, "operation.created", {"title": plan.title, "preview": preview}, operation_id)
    return get_operation(connection, principal, operation_id)


def get_operation(connection, principal: Principal, operation_id: str, *, after=0):
    principal.require("operations:read")
    row = connection.execute("SELECT * FROM control_operations WHERE id = ?", (operation_id,)).fetchone()
    if row is None:
        raise LookupError("Operation not found")
    if not principal.can_access(row):
        raise PermissionError("Operation belongs to a different principal")
    result = dict(row)
    for field in ("scopes", "plan"):
        result[field] = json.loads(result[field])
    for field in ("lease_owner", "lease_until", "request_hash", "source_request_hash", "idempotency_key", "token_id"):
        result.pop(field, None)
    result["steps"] = []
    for step in connection.execute("SELECT * FROM control_steps WHERE operation_id = ? ORDER BY position", (operation_id,)):
        item = dict(step)
        for field in ("input", "dependencies", "result"):
            item[field] = json.loads(item[field]) if item[field] is not None else None
        result["steps"].append(item)
    result["events"] = [
        {**dict(event), "detail": json.loads(event["detail"])}
        for event in connection.execute(
            "SELECT * FROM control_events WHERE operation_id = ? AND id > ? ORDER BY id LIMIT 500",
            (operation_id, after),
        )
    ]
    return result


def list_operations(connection, principal: Principal, limit=50):
    principal.require("operations:read")
    condition, params = ("", ()) if "*" in principal.scopes else ("WHERE actor = ?", (principal.actor,))
    return [dict(row) for row in connection.execute(
        f"SELECT id, title, actor, status, created_at, updated_at, error FROM control_operations {condition} ORDER BY created_at DESC LIMIT ?",
        (*params, limit),
    )]


def change_operation(connection, principal: Principal, operation_id: str, command: str):
    principal.require("operations:write")
    row = connection.execute("SELECT * FROM control_operations WHERE id = ? FOR UPDATE", (operation_id,)).fetchone()
    if row is None:
        raise LookupError("Operation not found")
    if not principal.can_access(row):
        raise PermissionError("Operation belongs to a different principal")
    if command == "cancel":
        if row["status"] in {"succeeded", "cancelled"}:
            return get_operation(connection, principal, operation_id)
        connection.execute(
            "UPDATE control_operations SET cancel_requested = TRUE, status = 'cancelled', updated_at = clock_timestamp() WHERE id = ?",
            (operation_id,),
        )
        connection.execute(
            "UPDATE control_steps SET status = 'cancelled' WHERE operation_id = ? AND status IN ('pending', 'waiting')",
            (operation_id,),
        )
        connection.execute(
            "UPDATE engine_waiting_test_queue SET status = 'cancelled', reason = 'Intake operation cancelled' WHERE operation_id = ? AND tournament_id IS NULL",
            (operation_id,),
        )
    elif command in {"execute", "resume"}:
        if row["status"] not in {"planned", "failed", "waiting"}:
            raise ValueError("Only planned, failed or waiting operations can be executed or resumed")
        if row["lease_until"] and row["lease_until"] > datetime.now(UTC):
            raise ValueError("Operation is still executing; wait for its current step")
        grant = validate_grant(connection, row)
        for step in json.loads(row["plan"])["steps"]:
            principal.require(catalog()[step["action"]].scope)
            grant.require(catalog()[step["action"]].scope)
        connection.execute(
            "UPDATE control_steps SET status = 'pending', error = '', available_at = clock_timestamp() WHERE operation_id = ? AND status IN ('failed', 'waiting', 'running')",
            (operation_id,),
        )
        connection.execute(
            "UPDATE control_operations SET status = 'pending', error = '', updated_at = clock_timestamp() WHERE id = ?",
            (operation_id,),
        )
    else:
        raise ValueError("Unknown operation command")
    audit(connection, principal.actor, f"operation.{command}", {}, operation_id)
    return get_operation(connection, principal, operation_id)
