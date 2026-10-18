from __future__ import annotations

from datetime import UTC, datetime


def replace_queues(connection, payload):
    state = connection.execute("SELECT revision FROM control_queue_state WHERE id = 1 FOR UPDATE").fetchone()
    if state["revision"] != payload.revision:
        raise ValueError("Engine queues changed; reload before saving")
    acknowledged = {row["id"]: row for row in connection.execute("SELECT * FROM engine_acknowledged_queue FOR UPDATE")}
    waiting = {row["id"]: row for row in connection.execute("SELECT * FROM engine_waiting_test_queue FOR UPDATE")}
    keep_acknowledged = set()
    keep_waiting = set()
    for position, item in enumerate(payload.acknowledged):
        current = acknowledged.get(item.id) if item.id is not None else None
        if item.id is not None and current is None:
            raise ValueError("A recognised engine was removed; reload before saving")
        if current:
            if current["id"] in keep_acknowledged:
                raise ValueError("Queue entry IDs must be unique")
            if current["intake_operation_id"] and (item.name != current["name"] or item.version != current["version"]):
                operation = connection.execute("SELECT status FROM control_operations WHERE id = ?", (current["intake_operation_id"],)).fetchone()
                if operation and operation["status"] not in {"cancelled", "failed", "succeeded"}:
                    raise ValueError("Cancel or finish the reserved engine intake before editing its identity")
                connection.execute("UPDATE engine_acknowledged_queue SET intake_operation_id = NULL, engine_version_id = NULL WHERE id = ?", (item.id,))
            connection.execute("UPDATE engine_acknowledged_queue SET name = ?, version = ?, position = ? WHERE id = ?", (item.name, item.version, position, item.id))
            keep_acknowledged.add(item.id)
        else:
            row = connection.execute(
                "INSERT INTO engine_acknowledged_queue(name, version, position, submitted_at) VALUES (?, ?, ?, clock_timestamp()) RETURNING id",
                (item.name, item.version, position),
            ).fetchone()
            keep_acknowledged.add(row["id"])
    for position, item in enumerate(payload.waiting_for_test):
        current = waiting.get(item.id) if item.id is not None else None
        if item.id is not None and current is None:
            raise ValueError("A testing plan was removed; reload before saving")
        if current:
            if current["id"] in keep_waiting:
                raise ValueError("Queue entry IDs must be unique")
            if item.engine_version_id != current["engine_version_id"] or item.rating_list_id != current["rating_list_id"]:
                raise ValueError("Remove and recreate a plan to change its engine or rating list")
            connection.execute("UPDATE engine_waiting_test_queue SET position = ? WHERE id = ?", (position, item.id))
            keep_waiting.add(item.id)
        else:
            row = connection.execute(
                """INSERT INTO engine_waiting_test_queue(engine_version_id, rating_list_id, position, submitted_at)
                   VALUES (?, ?, ?, clock_timestamp()) RETURNING id""",
                (item.engine_version_id, item.rating_list_id, position),
            ).fetchone()
            keep_waiting.add(row["id"])
    for row in acknowledged.values():
        if row["id"] not in keep_acknowledged:
            if row["intake_operation_id"]:
                operation = connection.execute("SELECT status FROM control_operations WHERE id = ?", (row["intake_operation_id"],)).fetchone()
                if operation and operation["status"] not in {"succeeded", "cancelled", "failed"}:
                    raise ValueError("Cancel the active intake before removing a reserved submission")
            connection.execute("DELETE FROM engine_acknowledged_queue WHERE id = ?", (row["id"],))
    for row in waiting.values():
        if row["id"] not in keep_waiting:
            if row["operation_id"] or row["tournament_id"]:
                raise ValueError("Managed testing plans cannot be deleted from the manual queue editor")
            connection.execute("DELETE FROM engine_waiting_test_queue WHERE id = ?", (row["id"],))
    connection.execute("UPDATE control_queue_state SET revision = revision + 1 WHERE id = 1")


def dispatch_tournaments(connection, tournaments):
    priorities = {}
    for row in connection.execute(
        "SELECT tournament_id, priority_group, duration_ms, submitted_at, position, id FROM engine_waiting_test_queue WHERE tournament_id IS NOT NULL"
    ):
        priorities[row["tournament_id"]] = (
            row["priority_group"], row["duration_ms"], row["submitted_at"] or datetime.min.replace(tzinfo=UTC), row["position"], row["id"],
        )
    return sorted(tournaments, key=lambda item: priorities.get(item.id, (-1, 0, datetime.min.replace(tzinfo=UTC), 0, -item.id)))
