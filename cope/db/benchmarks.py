from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cope.core.models import EngineSpec, HardwareInfo


@dataclass(frozen=True, slots=True)
class BenchmarkerRecord:
    id: int
    label: str
    token_hash: str | None
    token_expires_at: str | None
    status: str
    session_id: str | None
    app_commit: str | None
    protocol_version: int | None
    machine_id: str | None
    hardware_key: str | None
    hw: HardwareInfo | None
    created_at: str
    last_seen: str | None


@dataclass(frozen=True, slots=True)
class BenchmarkerToken:
    benchmarker_id: int
    token: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class BenchmarkJobRecord:
    id: int
    job_key: str
    benchmarker_id: int | None
    engine_version_id: int | None
    engine: EngineSpec
    engine_name: str
    engine_version: str
    build_hash: str
    hardware_key: str
    status: str
    attempt: int
    scheduled_at: str
    started_at: str | None
    finished_at: str | None
    next_retry_at: str | None
    error: str
    output: str


@dataclass(frozen=True, slots=True)
class EngineBenchmarkRecord:
    id: int
    job_id: int
    engine_version_id: int | None
    engine_name: str
    engine_version: str
    build_hash: str
    hardware_key: str
    nps: int
    elapsed_ms: int
    output: str
    recorded_at: str


@dataclass(frozen=True, slots=True)
class EngineBenchmarkJobViewRecord:
    """A benchmark job together with its execution and benchmarker details."""

    job: BenchmarkJobRecord
    benchmarker_label: str | None
    benchmarker_status: str | None
    hardware: HardwareInfo | None
    result: EngineBenchmarkRecord | None


@dataclass(frozen=True, slots=True)
class GameBenchmarkReferenceRecord:
    hardware_key: str
    engine_nps: dict[int, int]


@dataclass(frozen=True, slots=True)
class GameHardwareScoreRecord:
    game_id: int
    assignment_id: int
    worker_id: int | None
    engine_version_id: int
    color: str
    benchmark_hardware_key: str
    benchmark_nps: int
    worker_nps: int
    hardware_score: float
    elapsed_ms: int
    recorded_at: str


def mint_benchmarker_token(
    connection: sqlite3.Connection,
    *,
    label: str,
    ttl_seconds: int = 7200,
) -> BenchmarkerToken:
    now = _utc_now()
    cursor = connection.execute(
        """
        INSERT INTO benchmarkers (label, status, created_at)
        VALUES (?, 'minted', ?)
        """,
        (label, now),
    )
    benchmarker_id = int(cursor.lastrowid)
    token = secrets.token_urlsafe(32)
    expires_at = (
        datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    ).isoformat(timespec="seconds")
    connection.execute(
        """
        UPDATE benchmarkers
        SET token_hash = ?, token_expires_at = ?
        WHERE id = ?
        """,
        (_hash_token(token), expires_at, benchmarker_id),
    )
    return BenchmarkerToken(
        benchmarker_id=benchmarker_id,
        token=token,
        expires_at=expires_at,
    )


def get_benchmarker_by_token(
    connection: sqlite3.Connection,
    token: str,
) -> BenchmarkerRecord | None:
    row = connection.execute(
        "SELECT * FROM benchmarkers WHERE token_hash = ?",
        (_hash_token(token),),
    ).fetchone()
    return None if row is None else _benchmarker_from_row(row)


def get_benchmarker_by_session_id(
    connection: sqlite3.Connection,
    session_id: str,
) -> BenchmarkerRecord | None:
    row = connection.execute(
        "SELECT * FROM benchmarkers WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return None if row is None else _benchmarker_from_row(row)


def benchmarker_token_is_valid(
    record: BenchmarkerRecord,
    *,
    now: datetime | None = None,
) -> bool:
    if record.status == "revoked" or record.token_expires_at is None:
        return False
    expires_at = datetime.fromisoformat(record.token_expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at > (now or datetime.now(UTC))


def register_benchmarker_connection(
    connection: sqlite3.Connection,
    *,
    benchmarker: BenchmarkerRecord,
    label: str,
    session_id: str,
    app_commit: str,
    protocol_version: int,
    machine_id: str,
    hardware_key: str,
    hw: HardwareInfo,
) -> BenchmarkerRecord:
    if benchmarker.hardware_key is not None and benchmarker.hardware_key != hardware_key:
        raise ValueError("benchmarker credential is permanently bound to different hardware")
    connection.execute(
        """
        INSERT INTO benchmark_hardware (hardware_key, machine_id, hw, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(hardware_key) DO NOTHING
        """,
        (hardware_key, machine_id, hw.model_dump_json(), _utc_now()),
    )
    connection.execute(
        """
        UPDATE benchmarkers
        SET label = ?, token_hash = NULL, token_expires_at = NULL,
            status = 'connected', session_id = ?, app_commit = ?,
            protocol_version = ?, machine_id = ?, hardware_key = ?,
            hw = ?, last_seen = ?
        WHERE id = ? AND status != 'revoked'
          AND (hardware_key IS NULL OR hardware_key = ?)
        """,
        (
            label,
            session_id,
            app_commit,
            protocol_version,
            machine_id,
            hardware_key,
            hw.model_dump_json(),
            _utc_now(),
            benchmarker.id,
            hardware_key,
        ),
    )
    current = get_benchmarker(connection, benchmarker.id)
    if current is None or current.session_id != session_id:
        raise ValueError("benchmarker registration was revoked")
    return current


def get_benchmarker(
    connection: sqlite3.Connection,
    benchmarker_id: int,
) -> BenchmarkerRecord | None:
    row = connection.execute(
        "SELECT * FROM benchmarkers WHERE id = ?",
        (benchmarker_id,),
    ).fetchone()
    return None if row is None else _benchmarker_from_row(row)


def list_benchmarkers(
    connection: sqlite3.Connection,
) -> tuple[BenchmarkerRecord, ...]:
    rows = connection.execute(
        "SELECT * FROM benchmarkers WHERE status != 'revoked' ORDER BY id"
    )
    return tuple(_benchmarker_from_row(row) for row in rows)


def forget_benchmarker(
    connection: sqlite3.Connection,
    benchmarker_id: int,
) -> bool:
    if get_benchmarker(connection, benchmarker_id) is None:
        return False
    now = _utc_now()
    connection.execute(
        """
        UPDATE benchmark_jobs
        SET benchmarker_id = NULL, status = 'queued', scheduled_at = ?,
            started_at = NULL, finished_at = NULL, next_retry_at = NULL,
            error = '', output = ''
        WHERE benchmarker_id = ? AND status = 'running'
        """,
        (now, benchmarker_id),
    )
    connection.execute(
        "UPDATE benchmark_jobs SET benchmarker_id = NULL WHERE benchmarker_id = ?",
        (benchmarker_id,),
    )
    connection.execute("DELETE FROM benchmarkers WHERE id = ?", (benchmarker_id,))
    return True


def update_benchmarker_status(
    connection: sqlite3.Connection,
    benchmarker_id: int,
    status: str,
    *,
    session_id: str,
) -> bool:
    cursor = connection.execute(
        """
        UPDATE benchmarkers
        SET status = ?, last_seen = ?
        WHERE id = ? AND session_id = ? AND status != 'revoked'
        """,
        (status, _utc_now(), benchmarker_id, session_id),
    )
    return cursor.rowcount > 0


def disconnect_benchmarker(
    connection: sqlite3.Connection,
    benchmarker_id: int,
    *,
    session_id: str,
) -> bool:
    cursor = connection.execute(
        """
        UPDATE benchmarkers
        SET status = 'offline', last_seen = ?
        WHERE id = ? AND session_id = ? AND status != 'revoked'
        """,
        (_utc_now(), benchmarker_id, session_id),
    )
    return cursor.rowcount > 0


def reset_benchmark_service_state(
    connection: sqlite3.Connection,
    *,
    retry_seconds: int,
) -> None:
    retry_at = (
        datetime.now(UTC) + timedelta(seconds=retry_seconds)
    ).isoformat(timespec="seconds")
    connection.execute(
        """
        UPDATE benchmark_jobs
        SET status = 'failed', finished_at = ?, next_retry_at = ?,
            error = 'benchmark server restarted'
        WHERE status = 'running'
        """,
        (_utc_now(), retry_at),
    )
    connection.execute(
        """
        UPDATE benchmarkers
        SET status = 'offline', last_seen = ?
        WHERE status IN ('connected', 'busy')
        """,
        (_utc_now(),),
    )


def schedule_benchmark_jobs(
    connection: sqlite3.Connection,
    *,
    hardware_key: str,
    engines: tuple[EngineSpec, ...],
) -> int:
    scheduled = 0
    now = _utc_now()
    for engine in engines:
        cursor = connection.execute(
            """
            INSERT INTO benchmark_jobs (
              job_key, engine_version_id, engine_spec, engine_name,
              engine_version, build_hash, hardware_key, status, scheduled_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)
            ON CONFLICT(build_hash, hardware_key) DO NOTHING
            """,
            (
                secrets.token_urlsafe(24),
                engine.engine_id,
                engine.model_dump_json(),
                engine.name,
                engine.version,
                engine.build_hash,
                hardware_key,
                now,
            ),
        )
        scheduled += max(cursor.rowcount, 0)
    return scheduled


def claim_benchmark_job(
    connection: sqlite3.Connection,
    *,
    benchmarker_id: int,
    hardware_key: str,
) -> BenchmarkJobRecord | None:
    now = _utc_now()
    row = connection.execute(
        """
        SELECT *
        FROM benchmark_jobs
        WHERE hardware_key = ?
          AND (
            status = 'queued'
            OR (status = 'failed' AND next_retry_at <= ?)
          )
        ORDER BY scheduled_at, id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
        """,
        (hardware_key, now),
    ).fetchone()
    if row is None:
        return None
    connection.execute(
        """
        UPDATE benchmark_jobs
        SET benchmarker_id = ?, status = 'running', attempt = attempt + 1,
            started_at = ?, finished_at = NULL, next_retry_at = NULL, error = '',
            output = ''
        WHERE id = ?
        """,
        (benchmarker_id, now, row["id"]),
    )
    current = connection.execute(
        "SELECT * FROM benchmark_jobs WHERE id = ?",
        (row["id"],),
    ).fetchone()
    return None if current is None else _benchmark_job_from_row(current)


def record_benchmark_progress(
    connection: sqlite3.Connection,
    *,
    job: BenchmarkJobRecord,
    benchmarker_id: int,
    stage: str,
    substage: str,
    status: str,
    detail: str,
) -> None:
    current = _validated_running_job(connection, job, benchmarker_id)
    entry = f"[{_utc_now()}] {stage}/{substage} {status}\n{detail.strip()}"
    output = "\n\n".join(part for part in (current.output.rstrip(), entry) if part)[-64_000:]
    connection.execute(
        "UPDATE benchmark_jobs SET output = ? WHERE id = ?",
        (output, current.id),
    )


def complete_benchmark_job(
    connection: sqlite3.Connection,
    *,
    job: BenchmarkJobRecord,
    benchmarker_id: int,
    nps: int,
    elapsed_ms: int,
    output: str,
) -> None:
    current = _validated_running_job(connection, job, benchmarker_id)
    now = _utc_now()
    recorded_output = "\n\n".join(
        part for part in (current.output.rstrip(), output.strip()) if part
    )[-64_000:]
    connection.execute(
        """
        INSERT INTO engine_benchmarks (
          job_id, engine_version_id, engine_name, engine_version, build_hash,
          hardware_key, nps, elapsed_ms, output, recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(build_hash, hardware_key) DO NOTHING
        """,
        (
            current.id,
            current.engine_version_id,
            current.engine_name,
            current.engine_version,
            current.build_hash,
            current.hardware_key,
            nps,
            elapsed_ms,
            recorded_output,
            now,
        ),
    )
    connection.execute(
        """
        UPDATE benchmark_jobs
        SET status = 'succeeded', finished_at = ?, next_retry_at = NULL, error = ''
        WHERE id = ?
        """,
        (now, current.id),
    )


def fail_benchmark_job(
    connection: sqlite3.Connection,
    *,
    job: BenchmarkJobRecord,
    benchmarker_id: int,
    error: str,
    output: str = "",
    retry_seconds: int,
) -> None:
    current = _validated_running_job(connection, job, benchmarker_id)
    now = _utc_now()
    recorded_output = "\n\n".join(
        part for part in (current.output.rstrip(), output.strip()) if part
    )[-64_000:]
    retry_at = (
        datetime.now(UTC) + timedelta(seconds=retry_seconds)
    ).isoformat(timespec="seconds")
    connection.execute(
        """
        UPDATE benchmark_jobs
        SET status = 'failed', finished_at = ?, next_retry_at = ?, error = ?, output = ?
        WHERE id = ?
        """,
        (now, retry_at, error[-8000:], recorded_output, current.id),
    )


def list_engine_benchmarks(
    connection: sqlite3.Connection,
    *,
    engine_version_id: int | None = None,
    hardware_key: str | None = None,
) -> tuple[EngineBenchmarkRecord, ...]:
    clauses: list[str] = []
    parameters: list[object] = []
    if engine_version_id is not None:
        clauses.append("engine_version_id = ?")
        parameters.append(engine_version_id)
    if hardware_key is not None:
        clauses.append("hardware_key = ?")
        parameters.append(hardware_key)
    where = "" if not clauses else "WHERE " + " AND ".join(clauses)
    rows = connection.execute(
        f"""
        SELECT *
        FROM engine_benchmarks
        {where}
        ORDER BY recorded_at DESC, id DESC
        """,
        parameters,
    )
    return tuple(_engine_benchmark_from_row(row) for row in rows)


def engine_build_is_benchmarked(
    connection: sqlite3.Connection,
    *,
    engine_version_id: int,
    build_hash: str,
) -> bool:
    """Return whether this exact version build has a successful bench result."""
    # Benchmark results are canonical for an artifact, not for the database row
    # that happened to schedule them.  Multiple registered versions can point at
    # the same repository/ref/Dockerfile and therefore share a build hash.
    del engine_version_id
    row = connection.execute(
        """
        SELECT 1 FROM engine_benchmarks
        WHERE build_hash = ?
        LIMIT 1
        """,
        (build_hash,),
    ).fetchone()
    return row is not None


def list_engine_benchmark_jobs(
    connection: sqlite3.Connection,
    *,
    engine_version_id: int,
) -> tuple[EngineBenchmarkJobViewRecord, ...]:
    """Return all attempts for an engine version, newest first.

    Keeping jobs whose build hash is no longer current is intentional: an admin
    needs to be able to see why an earlier Dockerfile revision failed.
    """
    rows = connection.execute(
        """
        SELECT job.*, benchmarker.label AS benchmarker_label,
               benchmarker.status AS benchmarker_status,
               hardware.hw AS hardware_hw,
               result.id AS result_id, result.job_id AS result_job_id,
               result.engine_version_id AS result_engine_version_id,
               result.engine_name AS result_engine_name,
               result.engine_version AS result_engine_version,
               result.build_hash AS result_build_hash,
               result.hardware_key AS result_hardware_key,
               result.nps AS result_nps, result.elapsed_ms AS result_elapsed_ms,
               result.output AS result_output, result.recorded_at AS result_recorded_at
        FROM benchmark_jobs job
        LEFT JOIN benchmarkers benchmarker ON benchmarker.id = job.benchmarker_id
        LEFT JOIN benchmark_hardware hardware ON hardware.hardware_key = job.hardware_key
        LEFT JOIN engine_benchmarks result ON result.job_id = job.id
        WHERE job.engine_version_id = ?
           OR job.build_hash = (
             SELECT build_hash FROM engine_versions WHERE id = ?
           )
        ORDER BY job.scheduled_at DESC, job.id DESC
        """,
        (engine_version_id, engine_version_id),
    )
    result: list[EngineBenchmarkJobViewRecord] = []
    for row in rows:
        job = _benchmark_job_from_row(row)
        benchmark = None
        if row["result_id"] is not None:
            benchmark = EngineBenchmarkRecord(
                id=int(row["result_id"]), job_id=int(row["result_job_id"]),
                engine_version_id=row["result_engine_version_id"], engine_name=str(row["result_engine_name"]),
                engine_version=str(row["result_engine_version"]), build_hash=str(row["result_build_hash"]),
                hardware_key=str(row["result_hardware_key"]), nps=int(row["result_nps"]),
                elapsed_ms=int(row["result_elapsed_ms"]), output=str(row["result_output"]),
                recorded_at=str(row["result_recorded_at"]),
            )
        result.append(EngineBenchmarkJobViewRecord(
            job=job, benchmarker_label=row["benchmarker_label"], benchmarker_status=row["benchmarker_status"],
            hardware=None if not row["hardware_hw"] else HardwareInfo.model_validate_json(row["hardware_hw"]),
            result=benchmark,
        ))
    return tuple(result)


def forget_engine_benchmarks(
    connection: sqlite3.Connection,
    *,
    engine: EngineSpec,
) -> int:
    running = connection.execute(
        """
        SELECT 1 FROM benchmark_jobs
        WHERE build_hash = ? AND status = 'running'
        LIMIT 1
        """,
        (engine.build_hash,),
    ).fetchone()
    if running is not None:
        raise ValueError("A benchmark for this build is currently running.")
    jobs = connection.execute(
        "SELECT id FROM benchmark_jobs WHERE build_hash = ?",
        (engine.build_hash,),
    ).fetchall()
    connection.execute(
        "DELETE FROM engine_benchmarks WHERE build_hash = ?",
        (engine.build_hash,),
    )
    connection.execute(
        "DELETE FROM benchmark_jobs WHERE build_hash = ?",
        (engine.build_hash,),
    )
    return len(jobs)


def reschedule_engine_benchmarks(
    connection: sqlite3.Connection,
    *,
    engine: EngineSpec,
) -> int:
    """Queue a fresh run of an engine build on every known benchmark profile.

    A build has one canonical result per hardware profile. Rescheduling replaces
    that result rather than fabricating multiple competing references for the
    same build/profile pair.
    """
    now = _utc_now()
    running = connection.execute(
        """
        SELECT 1 FROM benchmark_jobs
        WHERE build_hash = ? AND status = 'running'
        LIMIT 1
        """,
        (engine.build_hash,),
    ).fetchone()
    if running is not None:
        raise ValueError("A benchmark for this build is already running.")
    # A requested re-run invalidates the current reference immediately. The
    # version becomes available again automatically when this build succeeds.
    jobs = connection.execute(
        """
        SELECT id FROM benchmark_jobs
        WHERE build_hash = ?
        """,
        (engine.build_hash,),
    ).fetchall()
    job_ids = tuple(int(row["id"]) for row in jobs)
    if job_ids:
        placeholders = ", ".join("?" for _ in job_ids)
        connection.execute(
            f"DELETE FROM engine_benchmarks WHERE job_id IN ({placeholders})",
            job_ids,
        )
        connection.execute(
            f"""
            UPDATE benchmark_jobs
            SET benchmarker_id = NULL, status = 'queued', scheduled_at = ?,
                started_at = NULL, finished_at = NULL, next_retry_at = NULL,
                error = '', output = ''
            WHERE id IN ({placeholders})
            """,
            (now, *job_ids),
        )
    hardware_rows = connection.execute(
        """
        SELECT DISTINCT hardware_key FROM benchmarkers
        WHERE hardware_key IS NOT NULL AND status IN ('connected', 'busy')
        """
    )
    scheduled = len(job_ids)
    for row in hardware_rows:
        scheduled += schedule_benchmark_jobs(
            connection,
            hardware_key=str(row["hardware_key"]),
            engines=(engine,),
        )
    return scheduled


def get_common_benchmark_reference(
    connection: sqlite3.Connection,
    engines: tuple[EngineSpec, ...],
) -> GameBenchmarkReferenceRecord | None:
    if not engines:
        return None
    build_hashes = tuple(sorted({engine.build_hash for engine in engines}))
    placeholders = ", ".join("?" for _ in build_hashes)
    row = connection.execute(
        f"""
        SELECT benchmark.hardware_key
        FROM engine_benchmarks benchmark
        JOIN benchmark_hardware hardware
          ON hardware.hardware_key = benchmark.hardware_key
        WHERE benchmark.build_hash IN ({placeholders})
        GROUP BY benchmark.hardware_key
        HAVING COUNT(DISTINCT benchmark.build_hash) = ?
        ORDER BY MIN(hardware.created_at), benchmark.hardware_key
        LIMIT 1
        """,
        (*build_hashes, len(build_hashes)),
    ).fetchone()
    if row is None:
        return None
    hardware_key = str(row["hardware_key"])
    rows = connection.execute(
        f"""
        SELECT build_hash, nps
        FROM engine_benchmarks
        WHERE hardware_key = ?
          AND build_hash IN ({placeholders})
        """,
        (hardware_key, *build_hashes),
    )
    nps_by_build = {
        str(item["build_hash"]): int(item["nps"])
        for item in rows
    }
    if set(nps_by_build) != set(build_hashes):
        return None
    engine_nps = {
        engine.engine_id: nps_by_build[engine.build_hash]
        for engine in engines
    }
    return GameBenchmarkReferenceRecord(
        hardware_key=hardware_key,
        engine_nps=engine_nps,
    )


def record_game_hardware_score(
    connection: sqlite3.Connection,
    *,
    game_id: int,
    assignment_id: int,
    worker_id: int,
    engine_version_id: int,
    color: str,
    benchmark_hardware_key: str,
    benchmark_nps: int,
    worker_nps: int,
    hardware_score: float,
    elapsed_ms: int,
) -> None:
    connection.execute(
        """
        INSERT INTO game_hardware_scores (
          game_id, assignment_id, worker_id, engine_version_id, color,
          benchmark_hardware_key, benchmark_nps, worker_nps, hardware_score,
          elapsed_ms, recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_id, engine_version_id) DO UPDATE SET
          assignment_id = excluded.assignment_id,
          worker_id = excluded.worker_id,
          color = excluded.color,
          benchmark_hardware_key = excluded.benchmark_hardware_key,
          benchmark_nps = excluded.benchmark_nps,
          worker_nps = excluded.worker_nps,
          hardware_score = excluded.hardware_score,
          elapsed_ms = excluded.elapsed_ms,
          recorded_at = excluded.recorded_at
        """,
        (
            game_id,
            assignment_id,
            worker_id,
            engine_version_id,
            color,
            benchmark_hardware_key,
            benchmark_nps,
            worker_nps,
            hardware_score,
            elapsed_ms,
            _utc_now(),
        ),
    )


def list_game_hardware_scores(
    connection: sqlite3.Connection,
    game_id: int,
) -> tuple[GameHardwareScoreRecord, ...]:
    rows = connection.execute(
        """
        SELECT *
        FROM game_hardware_scores
        WHERE game_id = ?
        ORDER BY CASE color WHEN 'white' THEN 0 ELSE 1 END
        """,
        (game_id,),
    )
    return tuple(
        GameHardwareScoreRecord(
            game_id=int(row["game_id"]),
            assignment_id=int(row["assignment_id"]),
            worker_id=row["worker_id"],
            engine_version_id=int(row["engine_version_id"]),
            color=str(row["color"]),
            benchmark_hardware_key=str(row["benchmark_hardware_key"]),
            benchmark_nps=int(row["benchmark_nps"]),
            worker_nps=int(row["worker_nps"]),
            hardware_score=float(row["hardware_score"]),
            elapsed_ms=int(row["elapsed_ms"]),
            recorded_at=str(row["recorded_at"]),
        )
        for row in rows
    )


def _validated_running_job(
    connection: sqlite3.Connection,
    job: BenchmarkJobRecord,
    benchmarker_id: int,
) -> BenchmarkJobRecord:
    row = connection.execute(
        """
        SELECT *
        FROM benchmark_jobs
        WHERE id = ? AND job_key = ? AND benchmarker_id = ? AND status = 'running'
        FOR UPDATE
        """,
        (job.id, job.job_key, benchmarker_id),
    ).fetchone()
    if row is None:
        raise ValueError("benchmark job is no longer active")
    return _benchmark_job_from_row(row)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _benchmarker_from_row(row) -> BenchmarkerRecord:
    return BenchmarkerRecord(
        id=int(row["id"]),
        label=str(row["label"]),
        token_hash=row["token_hash"],
        token_expires_at=row["token_expires_at"],
        status=str(row["status"]),
        session_id=row["session_id"],
        app_commit=row["app_commit"],
        protocol_version=row["protocol_version"],
        machine_id=row["machine_id"],
        hardware_key=row["hardware_key"],
        hw=None if not row["hw"] else HardwareInfo.model_validate_json(row["hw"]),
        created_at=str(row["created_at"]),
        last_seen=row["last_seen"],
    )


def _benchmark_job_from_row(row) -> BenchmarkJobRecord:
    return BenchmarkJobRecord(
        id=int(row["id"]),
        job_key=str(row["job_key"]),
        benchmarker_id=row["benchmarker_id"],
        engine_version_id=row["engine_version_id"],
        engine=EngineSpec.model_validate_json(row["engine_spec"]),
        engine_name=str(row["engine_name"]),
        engine_version=str(row["engine_version"]),
        build_hash=str(row["build_hash"]),
        hardware_key=str(row["hardware_key"]),
        status=str(row["status"]),
        attempt=int(row["attempt"]),
        scheduled_at=str(row["scheduled_at"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        next_retry_at=row["next_retry_at"],
        error=str(row["error"]),
        output=str(row["output"] or ""),
    )


def _engine_benchmark_from_row(row) -> EngineBenchmarkRecord:
    return EngineBenchmarkRecord(
        id=int(row["id"]),
        job_id=int(row["job_id"]),
        engine_version_id=row["engine_version_id"],
        engine_name=str(row["engine_name"]),
        engine_version=str(row["engine_version"]),
        build_hash=str(row["build_hash"]),
        hardware_key=str(row["hardware_key"]),
        nps=int(row["nps"]),
        elapsed_ms=int(row["elapsed_ms"]),
        output=str(row["output"]),
        recorded_at=str(row["recorded_at"]),
    )
