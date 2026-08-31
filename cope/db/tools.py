from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


TERMINAL_ITEM_STATUSES = {"supported", "unsupported", "failed"}


@dataclass(frozen=True, slots=True)
class ToolJobRecord:
    id: int
    job_key: str
    tool_name: str
    status: str
    input: dict[str, Any]
    worker_id: int | None
    worker_label: str | None
    required_threads: int
    required_hash_mb: int
    total_items: int
    completed_items: int
    progress_current: int
    progress_total: int
    progress_detail: str
    attempt: int
    error: str
    created_at: str
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True, slots=True)
class ToolJobItemRecord:
    id: int
    job_id: int
    engine_version_id: int
    engine_name: str
    engine_version: str
    position: int
    status: str
    result: dict[str, Any]
    error: str
    started_at: str | None
    finished_at: str | None


def create_tool_job(
    connection: sqlite3.Connection,
    *,
    tool_name: str,
    input_data: dict[str, Any],
    engine_version_ids: Iterable[int],
    required_threads: int = 1,
    required_hash_mb: int = 1,
) -> ToolJobRecord:
    engine_ids = tuple(dict.fromkeys(int(value) for value in engine_version_ids))
    if not engine_ids:
        raise ValueError("select at least one engine")
    if required_threads <= 0 or required_hash_mb <= 0:
        raise ValueError("tool job resources must be positive")
    existing = {
        int(row["id"])
        for row in connection.execute(
            f"SELECT id FROM engine_versions WHERE id IN ({', '.join('?' for _ in engine_ids)})",
            engine_ids,
        )
    }
    missing = [engine_id for engine_id in engine_ids if engine_id not in existing]
    if missing:
        raise ValueError("one or more selected engine versions no longer exist")
    now = _utc_now()
    puzzles = input_data.get("puzzles")
    progress_total = len(engine_ids) * len(puzzles) if isinstance(puzzles, list) else len(engine_ids)
    cursor = connection.execute(
        """
        INSERT INTO tool_jobs (
          job_key, tool_name, status, input, required_threads, required_hash_mb,
          total_items, completed_items, progress_total,
          attempt, error, created_at
        ) VALUES (?, ?, 'queued', ?, ?, ?, ?, 0, ?, 0, '', ?)
        """,
        (
            secrets.token_urlsafe(24),
            tool_name,
            json.dumps(input_data),
            required_threads,
            required_hash_mb,
            len(engine_ids),
            progress_total,
            now,
        ),
    )
    job_id = cursor.lastrowid
    connection.executemany(
        """
        INSERT INTO tool_job_items (job_id, engine_version_id, position, status)
        VALUES (?, ?, ?, 'pending')
        """,
        ((job_id, engine_id, position) for position, engine_id in enumerate(engine_ids)),
    )
    job = get_tool_job(connection, job_id)
    if job is None:
        raise RuntimeError("tool job was not created")
    return job


def get_tool_job(connection: sqlite3.Connection, job_id: int) -> ToolJobRecord | None:
    row = connection.execute(
        """
        SELECT job.*, worker.label AS worker_label
        FROM tool_jobs job
        LEFT JOIN workers worker ON worker.id = job.worker_id
        WHERE job.id = ?
        """,
        (job_id,),
    ).fetchone()
    return None if row is None else _job_from_row(row)


def list_tool_jobs(
    connection: sqlite3.Connection,
    *,
    tool_name: str | None = None,
    limit: int = 30,
) -> tuple[ToolJobRecord, ...]:
    parameters: list[Any] = []
    where = ""
    if tool_name is not None:
        where = "WHERE job.tool_name = ?"
        parameters.append(tool_name)
    parameters.append(max(1, min(limit, 200)))
    return tuple(
        _job_from_row(row)
        for row in connection.execute(
            f"""
            SELECT job.*, worker.label AS worker_label
            FROM tool_jobs job
            LEFT JOIN workers worker ON worker.id = job.worker_id
            {where}
            ORDER BY job.created_at DESC, job.id DESC
            LIMIT ?
            """,
            parameters,
        )
    )


def list_tool_job_items(
    connection: sqlite3.Connection,
    job_id: int,
    *,
    pending_only: bool = False,
) -> tuple[ToolJobItemRecord, ...]:
    pending = "AND item.status IN ('pending', 'running')" if pending_only else ""
    return tuple(
        _item_from_row(row)
        for row in connection.execute(
            f"""
            SELECT item.*, engine.name AS engine_name, version.version AS engine_version
            FROM tool_job_items item
            JOIN engine_versions version ON version.id = item.engine_version_id
            JOIN engines engine ON engine.id = version.engine_id
            WHERE item.job_id = ? {pending}
            ORDER BY item.position
            """,
            (job_id,),
        )
    )


def claim_tool_job(
    connection: sqlite3.Connection,
    *,
    worker_id: int,
    capacity_threads: int,
    capacity_hash_mb: int,
) -> ToolJobRecord | None:
    row = connection.execute(
        """
        SELECT job.id FROM tool_jobs job
        WHERE job.status = 'queued'
          AND job.required_threads <= ?
          AND job.required_hash_mb <= ?
          AND NOT EXISTS (
            SELECT 1
            FROM tool_job_items item
            JOIN engine_versions version ON version.id = item.engine_version_id
            WHERE item.job_id = job.id
              AND version.distribution = 'worker_local'
              AND NOT EXISTS (
                SELECT 1
                FROM worker_engine_discoveries discovery
                WHERE discovery.worker_id = ?
                  AND discovery.local_key = version.worker_local_key
              )
          )
        ORDER BY job.created_at, job.id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
        """,
        (capacity_threads, capacity_hash_mb, worker_id),
    ).fetchone()
    if row is None:
        return None
    now = _utc_now()
    connection.execute(
        """
        UPDATE tool_jobs
        SET status = 'running', worker_id = ?, attempt = attempt + 1,
            started_at = ?, finished_at = NULL, error = ''
        WHERE id = ?
        """,
        (worker_id, now, row["id"]),
    )
    connection.execute(
        """
        UPDATE tool_job_items
        SET status = 'pending', started_at = NULL
        WHERE job_id = ? AND status = 'running'
        """,
        (row["id"],),
    )
    return get_tool_job(connection, int(row["id"]))


def start_tool_job_item(
    connection: sqlite3.Connection,
    *,
    job_id: int,
    job_key: str,
    worker_id: int,
    engine_version_id: int,
) -> None:
    _validate_running_job(connection, job_id, job_key, worker_id)
    cursor = connection.execute(
        """
        UPDATE tool_job_items
        SET status = 'running', started_at = COALESCE(started_at, ?), error = ''
        WHERE job_id = ? AND engine_version_id = ? AND status IN ('pending', 'running')
        """,
        (_utc_now(), job_id, engine_version_id),
    )
    if cursor.rowcount != 1:
        raise ValueError("tool job item is not active")


def record_tool_job_progress(
    connection: sqlite3.Connection,
    *,
    job_id: int,
    job_key: str,
    worker_id: int,
    current: int,
    total: int,
    detail: str,
) -> None:
    _validate_running_job(connection, job_id, job_key, worker_id)
    if current < 0 or total <= 0 or current > total:
        raise ValueError("invalid tool job progress")
    connection.execute(
        """
        UPDATE tool_jobs
        SET progress_current = ?, progress_total = ?, progress_detail = ?
        WHERE id = ?
        """,
        (current, total, detail[-4000:], job_id),
    )


def finish_tool_job_item(
    connection: sqlite3.Connection,
    *,
    job_id: int,
    job_key: str,
    worker_id: int,
    engine_version_id: int,
    status: str,
    result: dict[str, Any],
    error: str = "",
) -> None:
    if status not in TERMINAL_ITEM_STATUSES:
        raise ValueError("invalid tool job item status")
    _validate_running_job(connection, job_id, job_key, worker_id)
    cursor = connection.execute(
        """
        UPDATE tool_job_items
        SET status = ?, result = ?, error = ?, finished_at = ?
        WHERE job_id = ? AND engine_version_id = ?
          AND status IN ('pending', 'running')
        """,
        (status, json.dumps(result), error, _utc_now(), job_id, engine_version_id),
    )
    if cursor.rowcount != 1:
        raise ValueError("tool job item is not active")
    _refresh_completed_count(connection, job_id)


def complete_tool_job(
    connection: sqlite3.Connection,
    *,
    job_id: int,
    job_key: str,
    worker_id: int,
) -> None:
    job = _validate_running_job(connection, job_id, job_key, worker_id)
    _refresh_completed_count(connection, job_id)
    current = get_tool_job(connection, job_id)
    if current is None or current.completed_items != job.total_items:
        raise ValueError("tool job completed before every engine reported")
    connection.execute(
        """
        UPDATE tool_jobs SET status = 'completed', finished_at = ?, error = ''
        WHERE id = ?
        """,
        (_utc_now(), job_id),
    )


def fail_tool_job(
    connection: sqlite3.Connection,
    *,
    job_id: int,
    job_key: str,
    worker_id: int,
    error: str,
) -> None:
    _validate_running_job(connection, job_id, job_key, worker_id)
    connection.execute(
        """
        UPDATE tool_jobs SET status = 'failed', finished_at = ?, error = ?
        WHERE id = ?
        """,
        (_utc_now(), error[-8000:], job_id),
    )


def cancel_tool_job(connection: sqlite3.Connection, job_id: int) -> bool:
    cursor = connection.execute(
        """
        UPDATE tool_jobs
        SET status = 'cancelled', finished_at = ?,
            error = 'Cancelled by an administrator.'
        WHERE id = ? AND status IN ('queued', 'running')
        """,
        (_utc_now(), job_id),
    )
    return cursor.rowcount == 1


def release_worker_tool_jobs(connection: sqlite3.Connection, worker_id: int) -> int:
    rows = connection.execute(
        "SELECT id FROM tool_jobs WHERE worker_id = ? AND status = 'running'",
        (worker_id,),
    ).fetchall()
    for row in rows:
        connection.execute(
            """
            UPDATE tool_job_items SET status = 'pending', started_at = NULL
            WHERE job_id = ? AND status = 'running'
            """,
            (row["id"],),
        )
    cursor = connection.execute(
        """
        UPDATE tool_jobs
        SET status = 'queued', worker_id = NULL, started_at = NULL,
            error = 'worker disconnected; job returned to queue'
        WHERE worker_id = ? AND status = 'running'
        """,
        (worker_id,),
    )
    return max(cursor.rowcount, 0)


def reset_tool_jobs(connection: sqlite3.Connection) -> int:
    connection.execute(
        "UPDATE tool_job_items SET status = 'pending', started_at = NULL WHERE status = 'running'"
    )
    cursor = connection.execute(
        """
        UPDATE tool_jobs
        SET status = 'queued', worker_id = NULL, started_at = NULL,
            error = 'worker server restarted; job returned to queue'
        WHERE status = 'running'
        """
    )
    return max(cursor.rowcount, 0)


def _validate_running_job(
    connection: sqlite3.Connection,
    job_id: int,
    job_key: str,
    worker_id: int,
) -> ToolJobRecord:
    job = get_tool_job(connection, job_id)
    if job is None:
        raise ValueError("tool job not found")
    if job.job_key != job_key or job.worker_id != worker_id or job.status != "running":
        raise ValueError("tool job is no longer assigned to this worker")
    return job


def _refresh_completed_count(connection: sqlite3.Connection, job_id: int) -> None:
    row = connection.execute(
        """
        SELECT COUNT(*) AS count FROM tool_job_items
        WHERE job_id = ? AND status IN ('supported', 'unsupported', 'failed')
        """,
        (job_id,),
    ).fetchone()
    connection.execute(
        "UPDATE tool_jobs SET completed_items = ? WHERE id = ?",
        (0 if row is None else int(row["count"]), job_id),
    )


def _job_from_row(row) -> ToolJobRecord:
    return ToolJobRecord(
        id=int(row["id"]),
        job_key=str(row["job_key"]),
        tool_name=str(row["tool_name"]),
        status=str(row["status"]),
        input=json.loads(row["input"] or "{}"),
        worker_id=None if row["worker_id"] is None else int(row["worker_id"]),
        worker_label=row["worker_label"],
        required_threads=int(row["required_threads"]),
        required_hash_mb=int(row["required_hash_mb"]),
        total_items=int(row["total_items"]),
        completed_items=int(row["completed_items"]),
        progress_current=int(row["progress_current"]),
        progress_total=int(row["progress_total"]),
        progress_detail=str(row["progress_detail"] or ""),
        attempt=int(row["attempt"]),
        error=str(row["error"] or ""),
        created_at=str(row["created_at"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _item_from_row(row) -> ToolJobItemRecord:
    return ToolJobItemRecord(
        id=int(row["id"]),
        job_id=int(row["job_id"]),
        engine_version_id=int(row["engine_version_id"]),
        engine_name=str(row["engine_name"]),
        engine_version=str(row["engine_version"]),
        position=int(row["position"]),
        status=str(row["status"]),
        result=json.loads(row["result"] or "{}"),
        error=str(row["error"] or ""),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
