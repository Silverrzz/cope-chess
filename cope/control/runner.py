from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass

from cope.db import connect_database, touch_service_heartbeat
from cope.version import app_version

from .operations import audit, dump, validate_grant
from .registry import Waiting, catalog


LOG = logging.getLogger("cope.control")


@dataclass
class Context:
    database_url: str
    connection: object
    operation: dict
    step_key: str
    lease_owner: str

    @property
    def actor(self):
        return self.operation["actor"]

    def result(self, key: str):
        row = self.connection.execute(
            "SELECT result FROM control_steps WHERE operation_id = ? AND key = ? AND status = 'succeeded'",
            (self.operation["id"], key),
        ).fetchone()
        if row is None:
            raise ValueError(f"Dependency {key} has no successful result")
        return json.loads(row["result"])

    def authorize(self, scope: str):
        connection = connect_database(self.database_url)
        try:
            row = connection.execute(
                "SELECT * FROM control_operations WHERE id = ? AND lease_owner = ? AND lease_until > clock_timestamp()",
                (self.operation["id"], self.lease_owner),
            ).fetchone()
            if row is None or row["cancel_requested"]:
                raise PermissionError("Operation was cancelled or its execution lease was lost")
            validate_grant(connection, row).require(scope)
        finally:
            connection.close()


def _heartbeat(database_url, operation_id, owner, stop):
    while not stop.wait(15):
        connection = connect_database(database_url)
        try:
            connection.execute(
                "UPDATE control_operations SET lease_until = clock_timestamp() + interval '90 seconds' WHERE id = ? AND lease_owner = ?",
                (operation_id, owner),
            )
            connection.commit()
        except Exception:
            LOG.exception("could not renew operation lease %s", operation_id)
        finally:
            connection.close()


def _guard(connection, operation_id, owner):
    row = connection.execute(
        "SELECT * FROM control_operations WHERE id = ? AND lease_owner = ? AND lease_until > clock_timestamp() FOR UPDATE",
        (operation_id, owner),
    ).fetchone()
    if row is None:
        raise PermissionError("Execution lease was lost")
    if row["cancel_requested"]:
        raise PermissionError("Operation was cancelled")
    return row


def _claim(connection, owner):
    row = connection.execute(
        """SELECT operation.* FROM control_operations operation
           WHERE operation.status IN ('pending', 'running', 'waiting')
             AND NOT operation.cancel_requested
             AND (operation.lease_until IS NULL OR operation.lease_until < clock_timestamp())
             AND EXISTS (SELECT 1 FROM control_steps step WHERE step.operation_id = operation.id
               AND step.status IN ('pending', 'running', 'waiting') AND step.available_at <= clock_timestamp()
               AND NOT EXISTS (SELECT 1 FROM control_steps dependency
                 WHERE dependency.operation_id = operation.id
                   AND dependency.key IN (SELECT json_array_elements_text(step.dependencies::json))
                   AND dependency.status != 'succeeded'))
           ORDER BY operation.updated_at, operation.created_at
           FOR UPDATE OF operation SKIP LOCKED LIMIT 1""",
    ).fetchone()
    if row is None:
        return None
    connection.execute(
        "UPDATE control_operations SET status = 'running', lease_owner = ?, lease_until = clock_timestamp() + interval '90 seconds', updated_at = clock_timestamp() WHERE id = ?",
        (owner, row["id"]),
    )
    step = connection.execute(
        """SELECT * FROM control_steps step WHERE operation_id = ?
           AND status IN ('pending', 'running', 'waiting') AND available_at <= clock_timestamp()
           AND NOT EXISTS (SELECT 1 FROM control_steps dependency
             WHERE dependency.operation_id = step.operation_id
               AND dependency.key IN (SELECT json_array_elements_text(step.dependencies::json))
               AND dependency.status != 'succeeded')
           ORDER BY position LIMIT 1""",
        (row["id"],),
    ).fetchone()
    connection.execute(
        "UPDATE control_steps SET status = 'running', attempts = attempts + 1, started_at = clock_timestamp(), error = '' WHERE operation_id = ? AND key = ?",
        (row["id"], step["key"]),
    )
    if step["status"] != "waiting":
        audit(connection, row["actor"], "step.started", {"key": step["key"], "action": step["action"]}, row["id"])
    return dict(row), dict(step)


def _finish_operation(connection, operation_id, owner):
    rows = list(connection.execute("SELECT status, dependencies, error FROM control_steps WHERE operation_id = ? ORDER BY position", (operation_id,)))
    failed = any(row["status"] == "failed" for row in rows)
    states = {row["status"] for row in rows}
    if states == {"succeeded"}:
        status = "succeeded"
    elif failed:
        keys = {row["key"]: row["status"] for row in connection.execute("SELECT key, status FROM control_steps WHERE operation_id = ?", (operation_id,))}
        runnable = any(row["status"] in {"pending", "waiting"} and all(keys[key] == "succeeded" for key in json.loads(row["dependencies"])) for row in rows)
        status = "waiting" if runnable else "failed"
    else:
        status = "waiting" if "waiting" in states else "pending"
    connection.execute(
        """UPDATE control_operations SET status = ?, lease_owner = NULL, lease_until = NULL, updated_at = clock_timestamp(),
           error = ?
           WHERE id = ? AND lease_owner = ? AND NOT cancel_requested""",
        (status, next((row["error"] for row in rows if row["status"] in {"failed", "waiting"} and row["error"]), ""), operation_id, owner),
    )


def run_once(database_url: str) -> bool:
    owner = str(uuid.uuid4())
    connection = connect_database(database_url)
    stop = threading.Event()
    heartbeat = None
    claimed = None
    try:
        claimed = _claim(connection, owner)
        connection.commit()
        if claimed is None:
            return False
        operation, step = claimed
        action = catalog()[step["action"]]
        payload = action.input_model.model_validate_json(step["input"])
        heartbeat = threading.Thread(target=_heartbeat, args=(database_url, operation["id"], owner, stop), daemon=True)
        heartbeat.start()
        context = Context(database_url, connection, operation, step["key"], owner)
        context.authorize(action.scope)
        if not action.external:
            current = _guard(connection, operation["id"], owner)
            validate_grant(connection, current).require(action.scope)
        result = action.handler(context, payload)
        retry_after = result.pop("_retry_after", None)
        failure = result.pop("_failed", "")
        step_status = "waiting" if retry_after is not None else "failed" if failure else "succeeded"
        if action.external:
            current = connection.execute(
                "SELECT * FROM control_operations WHERE id = ? AND lease_owner = ? FOR UPDATE",
                (operation["id"], owner),
            ).fetchone()
            if current is None:
                raise PermissionError("Execution lease was replaced")
        connection.execute(
            "UPDATE control_steps SET status = ?, result = ?, error = ?, finished_at = clock_timestamp(), available_at = clock_timestamp() + (? * interval '1 second') WHERE operation_id = ? AND key = ?",
            (step_status, dump(result), failure, retry_after or 0, operation["id"], step["key"]),
        )
        if failure:
            connection.execute("UPDATE control_operations SET error = ? WHERE id = ?", (failure, operation["id"]))
        if step["result"] != dump(result) or step["status"] != step_status:
            audit(connection, context.actor, f"step.{step_status}", {"key": step["key"], "result": result}, operation["id"])
        _finish_operation(connection, operation["id"], owner)
        connection.commit()
        return True
    except Exception as error:
        connection.rollback()
        if claimed is None:
            raise
        operation, step = claimed
        current = connection.execute(
            "SELECT * FROM control_operations WHERE id = ? AND lease_owner = ? FOR UPDATE",
            (operation["id"], owner),
        ).fetchone()
        if current is not None:
            cancelled = current["cancel_requested"]
            status = "cancelled" if cancelled else "waiting" if isinstance(error, Waiting) else "failed"
            detail = str(error)[:4000]
            delay = error.retry_after if isinstance(error, Waiting) else 0
            connection.execute(
                "UPDATE control_steps SET status = ?, error = ?, available_at = clock_timestamp() + (? * interval '1 second') WHERE operation_id = ? AND key = ?",
                (status, detail, delay, operation["id"], step["key"]),
            )
            connection.execute("UPDATE control_operations SET error = ? WHERE id = ?", (detail, operation["id"]))
            previous = connection.execute(
                "SELECT detail FROM control_events WHERE operation_id = ? AND kind = ? ORDER BY id DESC LIMIT 1",
                (operation["id"], f"step.{status}"),
            ).fetchone()
            event = {"key": step["key"], "error": detail}
            if previous is None or previous["detail"] != dump(event):
                audit(connection, operation["actor"], f"step.{status}", event, operation["id"])
            if cancelled:
                connection.execute("UPDATE control_operations SET lease_owner = NULL, lease_until = NULL WHERE id = ?", (operation["id"],))
            else:
                _finish_operation(connection, operation["id"], owner)
            connection.commit()
        if not isinstance(error, Waiting):
            LOG.warning("operation %s step %s: %s", operation["id"], step["key"], error)
        return True
    finally:
        stop.set()
        if heartbeat:
            heartbeat.join(timeout=2)
        connection.close()


def run_control_runner(database_url: str, poll_interval: float = 2):
    from cope.web.app import create_app

    create_app(database_url, worker_server_url=os.environ.get("COPE_WORKER_SERVER_URL"), benchmarker_server_url=os.environ.get("COPE_BENCHMARKER_SERVER_URL"))
    wake = threading.Event()
    last_heartbeat = 0.0
    while True:
        try:
            from time import monotonic

            if monotonic() - last_heartbeat >= 15:
                connection = connect_database(database_url)
                try:
                    touch_service_heartbeat(connection, "control-runner", app_version())
                    connection.commit()
                finally:
                    connection.close()
                last_heartbeat = monotonic()
            if run_once(database_url):
                continue
        except Exception:
            LOG.exception("control runner cycle failed")
        wake.wait(max(0.2, poll_interval))
