from __future__ import annotations

import hashlib
import json
import sqlite3
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable

from cope.engine_dockerfiles import engine_build_hash
from cope.core.models import (
    AssignmentProgress,
    EngineArtifactSpec,
    EngineSpec,
    HardwareInfo,
    OpeningLine,
    TournamentConfig,
    WorkerResourceTelemetry,
    WorkerResources,
    worker_memory_capacity_mb,
)


@dataclass(frozen=True, slots=True)
class RatingListRecord:
    id: int
    name: str
    anchor_engine_id: int | None
    anchor_elo: float
    created_at: str


@dataclass(frozen=True, slots=True)
class TournamentRecord:
    id: int
    name: str
    config: TournamentConfig
    status: str
    current_round: int
    worker_profile: str | None
    created_at: str
    scheduled_start_at: str | None
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True, slots=True)
class TournamentParticipantGameRemoval:
    game_ids: tuple[int, ...]
    pending: int
    assigned: int
    live: int
    finished: int
    abandoned: int


@dataclass(frozen=True, slots=True)
class GameRecord:
    id: int
    tournament_id: int
    round: int
    pair_index: int
    white_engine_id: int
    black_engine_id: int
    match_id: int | None
    game_number: int
    tiebreak_kind: str | None
    opening_id: int | None
    status: str
    result: str | None
    termination: str | None
    pgn: str | None
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True, slots=True)
class TournamentMatchRecord:
    id: int
    tournament_id: int
    round: int
    match_index: int
    engine1_id: int
    engine2_id: int | None
    status: str
    winner_engine_id: int | None


@dataclass(frozen=True, slots=True)
class MoveRecord:
    game_id: int
    ply: int
    uci: str
    san: str
    is_book: bool
    eval_cp: int | None
    eval_mate: int | None
    score_bound: str | None
    depth: int | None
    seldepth: int | None
    nodes: int | None
    nps: int | None
    hashfull: int | None
    pv: str | None
    info_line: str | None
    time_ms: int
    clock_after_ms: int
    engine_version_id: int | None


@dataclass(frozen=True, slots=True)
class GameAssignmentRecord:
    id: int
    game_id: int
    assignment_key: str
    worker_id: int | None
    status: str
    sent_at: str | None
    acked_at: str | None
    finished_at: str | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class GamePauseCheckpointRecord:
    game_id: int
    state: dict[str, Any]
    paused_at: str


@dataclass(frozen=True, slots=True)
class GameProgressRecord:
    id: int
    assignment_id: int
    assignment_key: str
    game_id: int
    source: str
    stage: str
    stage_label: str
    stage_order: int
    substage: str
    status: str
    detail: str
    engine_id: int | None
    engine_name: str | None
    current: int | None
    total: int | None
    metadata: dict[str, Any]
    occurred_at: str


@dataclass(frozen=True, slots=True)
class WorkerRecord:
    id: int
    label: str
    token_expires_at: str | None
    status: str
    session_id: str | None
    app_commit: str | None
    protocol_version: int | None
    machine_id: str | None
    hw: HardwareInfo | None
    core_limit: int | None
    tournament_scope: str
    last_seen: str | None

    @property
    def capacity(self) -> WorkerResources | None:
        if self.hw is None:
            return None
        return WorkerResources(
            threads=min(self.hw.logical_cores, self.core_limit or self.hw.logical_cores),
            hash_mb=worker_memory_capacity_mb(self.hw.total_ram_mb),
        )


@dataclass(frozen=True, slots=True)
class EventFixtureWorkerRecord:
    tournament_id: int
    event_id: int
    worker_id: int
    claimed_at: str


@dataclass(frozen=True, slots=True)
class WorkerFailureRecord:
    id: int
    worker_id: int | None
    worker_label: str
    machine_id: str | None
    assignment_id: int | None
    game_id: int | None
    engine_id: int | None
    engine_name: str
    stage: str
    error: str
    occurred_at: str


@dataclass(frozen=True, slots=True)
class WorkerResourceSampleRecord:
    id: int
    worker_id: int
    sampled_at: str
    cpu_percent: float
    memory_used_mb: float
    memory_total_mb: float
    memory_available_mb: float
    coordinator_cpu_cores: float
    coordinator_memory_mb: float
    engine_cpu_cores: float
    engine_memory_mb: float
    disk_used_mb: float
    disk_free_mb: float
    disk_total_mb: float


@dataclass(frozen=True, slots=True)
class EngineRecord:
    id: int
    name: str
    author: str
    active: bool


@dataclass(frozen=True, slots=True)
class EngineVersionRecord:
    id: int
    engine_id: int
    name: str
    author: str
    version: str
    git_host_id: int | None
    repository_url: str
    repository_full_name: str
    source_ref: str
    source_kind: str
    dockerfile_path: str
    dockerfile: str
    build_hash: str
    uci_options: dict[str, Any]
    artifact: EngineArtifactSpec | None
    active: bool
    benchmark_current: bool
    engine_active: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class EngineArtifactRecord:
    build_hash: str
    artifact_sha256: str
    artifact_size: int
    artifact_format: str
    entrypoint: str
    platform: str
    storage_key: str
    created_at: str


@dataclass(frozen=True, slots=True)
class GitHostRecord:
    id: int
    name: str
    provider: str
    base_url: str
    api_url: str
    access_token: str
    enabled: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class OpeningSuiteRecord:
    id: int
    name: str
    description: str
    created_at: str


@dataclass(frozen=True, slots=True)
class OpeningRecord:
    id: int
    suite_id: int
    position: int
    name: str
    start_fen: str
    moves: tuple[str, ...]
    fen: str


@dataclass(frozen=True, slots=True)
class ChatMessageRecord:
    id: int
    tournament_id: int | None
    event_id: int | None
    display_name: str
    text: str
    at: str


@dataclass(frozen=True, slots=True)
class ChatSettingsRecord:
    enabled: bool
    slowmode_seconds: int
    max_message_length: int
    allow_anonymous_names: bool
    retention_days: int


@dataclass(frozen=True, slots=True)
class WorkerToken:
    worker_id: int
    token: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class ServiceEndpointRecord:
    service: str
    host: str
    port: int
    path: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class TournamentRatingCommitRecord:
    tournament_id: int
    rating_list_id: int
    command_id: int | None
    status: str
    requested_at: str
    applied_at: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class RunnerCommandRecord:
    id: int
    command: str
    payload: dict[str, Any]
    status: str
    created_at: str
    claimed_at: str | None
    finished_at: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class DeploymentJobRecord:
    id: int
    requested_ref: str
    scope: str
    target_commit: str | None
    status: str
    requested_at: str
    started_at: str | None
    finished_at: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class DeploymentTargetRecord:
    id: int
    job_id: int
    target_kind: str
    target_id: int | None
    label: str
    repository_url: str | None
    target_commit: str | None
    current_commit: str | None
    status: str
    detail: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class DockerfilePullJobRecord:
    id: int
    requested_ref: str
    target_commit: str | None
    status: str
    files_updated: int
    requested_at: str
    started_at: str | None
    finished_at: str | None
    error: str | None


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def utc_now_datetime() -> datetime:
    return datetime.now(UTC)


def _reconcile_engine_relay_events_for_tournament(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> None:
    from .events import reconcile_engine_relay_events_for_tournament

    reconcile_engine_relay_events_for_tournament(connection, tournament_id)


def _utc_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be a valid ISO date and time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC).isoformat(timespec="seconds")


def set_service_endpoint(
    connection: sqlite3.Connection,
    *,
    service: str,
    host: str,
    port: int,
    path: str,
) -> None:
    connection.execute(
        """
        INSERT INTO service_endpoints (service, host, port, path, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(service) DO UPDATE SET
          host = excluded.host,
          port = excluded.port,
          path = excluded.path,
          updated_at = excluded.updated_at
        """,
        (service, host, port, path, utc_now()),
    )


def get_service_endpoint(
    connection: sqlite3.Connection,
    service: str,
) -> ServiceEndpointRecord | None:
    row = connection.execute(
        "SELECT * FROM service_endpoints WHERE service = ?",
        (service,),
    ).fetchone()
    if row is None:
        return None
    return ServiceEndpointRecord(
        service=row["service"],
        host=row["host"],
        port=row["port"],
        path=row["path"],
        updated_at=row["updated_at"],
    )


def touch_service_heartbeat(
    connection: sqlite3.Connection,
    service: str,
    app_commit: str,
) -> None:
    connection.execute(
        """
        INSERT INTO service_heartbeats (service, app_commit, last_seen)
        VALUES (?, ?, ?)
        ON CONFLICT(service) DO UPDATE SET
          app_commit = excluded.app_commit,
          last_seen = excluded.last_seen
        """,
        (service, app_commit, utc_now()),
    )


def list_service_heartbeats(connection: sqlite3.Connection) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "service": str(row["service"]),
            "app_version": str(row["app_commit"]),
            "last_seen": str(row["last_seen"]),
        }
        for row in connection.execute("SELECT * FROM service_heartbeats ORDER BY service")
    )


def create_engine(
    connection: sqlite3.Connection,
    *,
    name: str,
    author: str = "",
    active: bool = True,
) -> int:
    cursor = connection.execute(
        "INSERT INTO engines (name, author, active) VALUES (?, ?, ?)",
        (name, author, int(active)),
    )
    return int(cursor.lastrowid)


def update_engine(
    connection: sqlite3.Connection,
    engine_id: int,
    *,
    name: str,
    author: str = "",
    active: bool = True,
) -> None:
    connection.execute(
        "UPDATE engines SET name = ?, author = ?, active = ? WHERE id = ?",
        (name, author, int(active), engine_id),
    )


def create_engine_version(
    connection: sqlite3.Connection,
    *,
    engine_id: int,
    version: str,
    git_host_id: int | None,
    repository_url: str,
    repository_full_name: str,
    source_ref: str,
    source_kind: str,
    dockerfile_path: str,
    dockerfile: str,
    uci_options: dict[str, Any] | None = None,
    active: bool = True,
) -> int:
    build_hash = engine_build_hash(repository_url, source_ref, dockerfile)
    cursor = connection.execute(
        """INSERT INTO engine_versions
           (engine_id, version, git_host_id, repository_url, repository_full_name,
            source_ref, source_kind, dockerfile_path, dockerfile, build_hash, uci_options,
            active, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            engine_id,
            version,
            git_host_id,
            repository_url,
            repository_full_name,
            source_ref,
            source_kind,
            dockerfile_path,
            dockerfile,
            build_hash,
            _json_dump(uci_options or {}),
            int(active),
            utc_now(),
        ),
    )
    return int(cursor.lastrowid)


def update_engine_version(
    connection: sqlite3.Connection,
    version_id: int,
    *,
    version: str,
    dockerfile_path: str,
    dockerfile: str,
    uci_options: dict[str, Any],
    active: bool,
) -> None:
    current = get_engine_version_record(connection, version_id)
    if current is None:
        raise ValueError("engine version not found")
    build_hash = engine_build_hash(current.repository_url, current.source_ref, dockerfile)
    connection.execute(
        """UPDATE engine_versions
           SET version = ?, dockerfile_path = ?, dockerfile = ?, build_hash = ?,
               uci_options = ?, active = ?
           WHERE id = ?""",
        (
            version,
            dockerfile_path,
            dockerfile,
            build_hash,
            _json_dump(uci_options),
            int(active),
            version_id,
        ),
    )


def get_engine_artifact(
    connection: sqlite3.Connection,
    build_hash: str,
) -> EngineArtifactRecord | None:
    row = connection.execute(
        "SELECT * FROM engine_artifacts WHERE build_hash = ?",
        (build_hash,),
    ).fetchone()
    return None if row is None else _engine_artifact_from_row(row)


def get_engine_artifact_by_sha256(
    connection: sqlite3.Connection,
    artifact_sha256: str,
) -> EngineArtifactRecord | None:
    row = connection.execute(
        """SELECT * FROM engine_artifacts
           WHERE artifact_sha256 = ?
           ORDER BY created_at, build_hash
           LIMIT 1""",
        (artifact_sha256,),
    ).fetchone()
    return None if row is None else _engine_artifact_from_row(row)


def register_engine_artifact(
    connection: sqlite3.Connection,
    *,
    build_hash: str,
    artifact_sha256: str,
    artifact_size: int,
    artifact_format: str,
    entrypoint: str,
    platform: str,
    storage_key: str,
) -> EngineArtifactRecord:
    current = get_engine_artifact(connection, build_hash)
    values = (
        artifact_sha256,
        artifact_size,
        artifact_format,
        entrypoint,
        platform,
        storage_key,
    )
    if current is not None:
        existing = (
            current.artifact_sha256,
            current.artifact_size,
            current.artifact_format,
            current.entrypoint,
            current.platform,
            current.storage_key,
        )
        if existing != values:
            raise ValueError("a different artifact is already registered for this build")
        return current
    connection.execute(
        """INSERT INTO engine_artifacts (
             build_hash, artifact_sha256, artifact_size, artifact_format,
             entrypoint, platform, storage_key, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(build_hash) DO NOTHING""",
        (
            build_hash,
            artifact_sha256,
            artifact_size,
            artifact_format,
            entrypoint,
            platform,
            storage_key,
            utc_now(),
        ),
    )
    artifact = get_engine_artifact(connection, build_hash)
    if artifact is None:
        raise RuntimeError("engine artifact was not registered")
    registered = (
        artifact.artifact_sha256,
        artifact.artifact_size,
        artifact.artifact_format,
        artifact.entrypoint,
        artifact.platform,
        artifact.storage_key,
    )
    if registered != values:
        raise ValueError("a different artifact is already registered for this build")
    return artifact


def engine_game_count(connection: sqlite3.Connection, engine_id: int) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS count FROM games WHERE white_engine_id = ? OR black_engine_id = ?",
        (engine_id, engine_id),
    ).fetchone()
    return int(row["count"])


def delete_engine(connection: sqlite3.Connection, engine_id: int) -> None:
    row = connection.execute("SELECT COUNT(*) AS count FROM engine_versions WHERE engine_id = ?", (engine_id,)).fetchone()
    if int(row["count"]) > 0:
        raise ValueError("engine has versions; delete those versions first")
    connection.execute("DELETE FROM engines WHERE id = ?", (engine_id,))


def delete_engine_version(connection: sqlite3.Connection, version_id: int) -> None:
    if engine_game_count(connection, version_id) > 0:
        raise ValueError("engine version has recorded games and cannot be deleted")
    row = connection.execute("SELECT COUNT(*) AS count FROM participants WHERE engine_id = ?", (version_id,)).fetchone()
    if int(row["count"]) > 0:
        raise ValueError("engine version participates in tournaments and cannot be deleted")
    record = get_engine_version_record(connection, version_id)
    if record is None:
        raise ValueError("engine version not found")
    connection.execute("DELETE FROM rating_list_ratings WHERE engine_id = ?", (version_id,))
    connection.execute(
        "DELETE FROM rating_list_history WHERE engine_id = ? OR opponent_engine_id = ?",
        (version_id, version_id),
    )
    connection.execute("DELETE FROM engine_versions WHERE id = ?", (version_id,))


def list_git_hosts(
    connection: sqlite3.Connection,
    *,
    enabled_only: bool = False,
) -> tuple[GitHostRecord, ...]:
    sql = "SELECT * FROM git_hosts"
    parameters: tuple[Any, ...] = ()
    if enabled_only:
        sql += " WHERE enabled = ?"
        parameters = (1,)
    sql += " ORDER BY name"
    return tuple(_git_host_from_row(row) for row in connection.execute(sql, parameters))


def get_git_host(connection: sqlite3.Connection, host_id: int) -> GitHostRecord | None:
    row = connection.execute("SELECT * FROM git_hosts WHERE id = ?", (host_id,)).fetchone()
    return None if row is None else _git_host_from_row(row)


def create_git_host(
    connection: sqlite3.Connection,
    *,
    name: str,
    provider: str,
    base_url: str,
    api_url: str,
    access_token: str,
    enabled: bool,
) -> int:
    cursor = connection.execute(
        """INSERT INTO git_hosts
           (name, provider, base_url, api_url, access_token, enabled, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (name, provider, base_url, api_url, access_token, int(enabled), utc_now()),
    )
    return int(cursor.lastrowid)


def update_git_host(
    connection: sqlite3.Connection,
    host_id: int,
    *,
    name: str,
    provider: str,
    base_url: str,
    api_url: str,
    access_token: str | None,
    clear_access_token: bool,
    enabled: bool,
) -> None:
    if clear_access_token or access_token is not None:
        connection.execute(
            """UPDATE git_hosts SET name = ?, provider = ?, base_url = ?, api_url = ?,
               access_token = ?, enabled = ? WHERE id = ?""",
            (
                name,
                provider,
                base_url,
                api_url,
                "" if clear_access_token else access_token,
                int(enabled),
                host_id,
            ),
        )
    else:
        connection.execute(
            """UPDATE git_hosts SET name = ?, provider = ?, base_url = ?, api_url = ?,
               enabled = ? WHERE id = ?""",
            (name, provider, base_url, api_url, int(enabled), host_id),
        )


def delete_git_host(connection: sqlite3.Connection, host_id: int) -> None:
    row = connection.execute(
        "SELECT COUNT(*) AS count FROM engine_versions WHERE git_host_id = ?",
        (host_id,),
    ).fetchone()
    if row is not None and int(row["count"]):
        raise ValueError("git host is used by engine versions; disable it instead")
    connection.execute("DELETE FROM git_hosts WHERE id = ?", (host_id,))


def get_engine_record(
    connection: sqlite3.Connection,
    engine_id: int,
) -> EngineVersionRecord | None:
    return get_engine_version_record(connection, engine_id)


def get_engine_family(connection: sqlite3.Connection, engine_id: int) -> EngineRecord | None:
    row = connection.execute("SELECT * FROM engines WHERE id = ?", (engine_id,)).fetchone()
    return None if row is None else _engine_record_from_row(row)


def get_engine_version_record(connection: sqlite3.Connection, version_id: int) -> EngineVersionRecord | None:
    row = connection.execute(
        """SELECT version.*, engine.name, engine.author, engine.active AS engine_active,
                  artifact.artifact_sha256, artifact.artifact_size,
                  artifact.artifact_format, artifact.entrypoint,
                  artifact.platform, artifact.storage_key,
                  EXISTS (
                    SELECT 1 FROM engine_benchmarks benchmark
                    WHERE benchmark.build_hash = version.build_hash
                      AND benchmark.artifact_sha256 = artifact.artifact_sha256
                  ) AS benchmark_current
           FROM engine_versions version
           JOIN engines engine ON engine.id = version.engine_id
           LEFT JOIN engine_artifacts artifact ON artifact.build_hash = version.build_hash
           WHERE version.id = ?""",
        (version_id,),
    ).fetchone()
    if row is None:
        return None
    return _engine_version_from_row(row)


def list_engine_families(connection: sqlite3.Connection) -> tuple[EngineRecord, ...]:
    return tuple(
        _engine_record_from_row(row)
        for row in connection.execute("SELECT * FROM engines ORDER BY name")
    )


def list_engine_records(connection: sqlite3.Connection) -> tuple[EngineVersionRecord, ...]:
    return tuple(
        _engine_version_from_row(row)
        for row in connection.execute(
            """SELECT version.*, engine.name, engine.author, engine.active AS engine_active,
                      artifact.artifact_sha256, artifact.artifact_size,
                      artifact.artifact_format, artifact.entrypoint,
                      artifact.platform, artifact.storage_key,
                      EXISTS (
                        SELECT 1 FROM engine_benchmarks benchmark
                        WHERE benchmark.build_hash = version.build_hash
                          AND benchmark.artifact_sha256 = artifact.artifact_sha256
                      ) AS benchmark_current
               FROM engine_versions version
               JOIN engines engine ON engine.id = version.engine_id
               LEFT JOIN engine_artifacts artifact ON artifact.build_hash = version.build_hash
               ORDER BY engine.name, version.created_at DESC, version.id DESC"""
        )
    )


def list_engine_versions(connection: sqlite3.Connection, engine_id: int) -> tuple[EngineVersionRecord, ...]:
    return tuple(record for record in list_engine_records(connection) if record.engine_id == engine_id)


def get_engine(connection: sqlite3.Connection, engine_id: int) -> EngineSpec | None:
    row = connection.execute(
        """SELECT version.*, engine.name, engine.author, engine.active AS engine_active,
                  artifact.artifact_sha256, artifact.artifact_size,
                  artifact.artifact_format, artifact.entrypoint,
                  artifact.platform, artifact.storage_key
           FROM engine_versions version
           JOIN engines engine ON engine.id = version.engine_id
           LEFT JOIN engine_artifacts artifact ON artifact.build_hash = version.build_hash
           WHERE version.id = ?""",
        (engine_id,),
    ).fetchone()
    if row is None:
        return None
    return _engine_from_row(row)


def list_engines(connection: sqlite3.Connection, *, active_only: bool = False) -> tuple[EngineSpec, ...]:
    sql = """SELECT version.*, engine.name, engine.author, engine.active AS engine_active,
                    artifact.artifact_sha256, artifact.artifact_size,
                    artifact.artifact_format, artifact.entrypoint,
                    artifact.platform, artifact.storage_key
             FROM engine_versions version
             JOIN engines engine ON engine.id = version.engine_id
             LEFT JOIN engine_artifacts artifact ON artifact.build_hash = version.build_hash"""
    params: tuple[Any, ...] = ()
    if active_only:
        sql = f"""{sql} WHERE engine.active = ? AND artifact.build_hash IS NOT NULL
                 AND EXISTS (
                   SELECT 1 FROM engine_benchmarks benchmark
                   WHERE benchmark.build_hash = version.build_hash
                     AND benchmark.artifact_sha256 = artifact.artifact_sha256
                 )"""
        params = (1,)
    sql = f"{sql} ORDER BY version.id"
    return tuple(_engine_from_row(row) for row in connection.execute(sql, params))


def create_tournament(
    connection: sqlite3.Connection,
    name: str,
    config: TournamentConfig,
    *,
    status: str = "draft",
    scheduled_start_at: str | None = None,
) -> int:
    if status == "scheduled" and scheduled_start_at is None:
        raise ValueError("scheduled tournaments require a start time")
    if scheduled_start_at is not None:
        scheduled_start_at = _utc_timestamp(scheduled_start_at)
    created_at = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO tournaments (name, config, status, created_at, scheduled_start_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            name,
            config.model_dump_json(),
            status,
            created_at,
            scheduled_start_at,
        ),
    )
    tournament_id = int(cursor.lastrowid)

    connection.executemany(
        """
        INSERT INTO participants (tournament_id, engine_id, seed)
        VALUES (?, ?, ?)
        """,
        (
            (tournament_id, engine_id, seed)
            for seed, engine_id in enumerate(config.participants, start=1)
        ),
    )
    return tournament_id


def get_tournament(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> TournamentRecord | None:
    row = connection.execute(
        "SELECT * FROM tournaments WHERE id = ?",
        (tournament_id,),
    ).fetchone()
    if row is None:
        return None
    return _tournament_from_row(row)


def lock_tournament(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> TournamentRecord | None:
    row = connection.execute(
        "SELECT * FROM tournaments WHERE id = ? FOR UPDATE",
        (tournament_id,),
    ).fetchone()
    if row is None:
        return None
    return _tournament_from_row(row)


def list_tournaments(connection: sqlite3.Connection) -> tuple[TournamentRecord, ...]:
    return tuple(
        _tournament_from_row(row)
        for row in connection.execute("SELECT * FROM tournaments ORDER BY id DESC")
    )


def list_tournaments_by_ids(
    connection: sqlite3.Connection,
    tournament_ids: Iterable[int],
) -> tuple[TournamentRecord, ...]:
    selected = tuple(dict.fromkeys(int(value) for value in tournament_ids))
    if not selected:
        return ()
    placeholders = ", ".join("?" for _ in selected)
    return tuple(
        _tournament_from_row(row)
        for row in connection.execute(
            f"SELECT * FROM tournaments WHERE id IN ({placeholders}) ORDER BY id",
            selected,
        )
    )


def set_tournament_status(
    connection: sqlite3.Connection,
    tournament_id: int,
    status: str,
) -> None:
    now = utc_now()
    started_at_sql = ", started_at = COALESCE(started_at, ?)" if status == "running" else ""
    finished_at_sql = ", finished_at = ?" if status in {"finished", "aborted"} else ""
    params: list[Any] = [status]
    if status == "running":
        params.append(now)
    if status in {"finished", "aborted"}:
        params.append(now)
    params.append(tournament_id)

    cursor = connection.execute(
        f"UPDATE tournaments SET status = ?{started_at_sql}{finished_at_sql} WHERE id = ?",
        params,
    )
    if status == "aborted" and cursor.rowcount > 0:
        _abandon_tournament_games(connection, tournament_id, now)
    if cursor.rowcount > 0:
        _reconcile_engine_relay_events_for_tournament(connection, tournament_id)


def restore_tournament(connection: sqlite3.Connection, tournament_id: int) -> None:
    tournament = lock_tournament(connection, tournament_id)
    if tournament is None:
        raise ValueError("tournament does not exist")
    if tournament.status != "aborted":
        raise ValueError("only an aborted tournament can be restored")
    if any(
        commit.status in {"pending", "claimed", "applied"}
        for commit in list_tournament_rating_commits(connection, tournament_id)
    ):
        raise ValueError("uncommit the tournament ratings before restoring it")
    game_ids = tuple(
        int(row["id"])
        for row in connection.execute(
            "SELECT id FROM games WHERE tournament_id = ? AND status = 'abandoned'",
            (tournament_id,),
        )
    )
    _reset_games_for_replay(
        connection,
        game_ids,
        include_abandoned=True,
        reason="tournament restored after abort",
    )
    connection.execute(
        """
        UPDATE tournaments
        SET status = 'paused', started_at = COALESCE(started_at, ?), finished_at = NULL
        WHERE id = ?
        """,
        (utc_now(), tournament_id),
    )
    _reconcile_engine_relay_events_for_tournament(connection, tournament_id)


def set_tournament_current_round_at_least(
    connection: sqlite3.Connection,
    tournament_id: int,
    round_number: int,
) -> None:
    connection.execute(
        """
        UPDATE tournaments
        SET current_round = ?
        WHERE id = ? AND current_round < ?
        """,
        (round_number, tournament_id, round_number),
    )


def list_due_scheduled_tournaments(
    connection: sqlite3.Connection,
    now: str,
) -> tuple[TournamentRecord, ...]:
    return tuple(
        _tournament_from_row(row)
        for row in connection.execute(
            """
            SELECT * FROM tournaments
            WHERE status = 'scheduled'
              AND scheduled_start_at <= ?
            ORDER BY scheduled_start_at, id
            FOR UPDATE SKIP LOCKED
            """,
            (now,),
        )
    )


def schedule_tournament(
    connection: sqlite3.Connection,
    tournament_id: int,
    scheduled_start_at: str,
) -> TournamentRecord:
    scheduled_start_at = _utc_timestamp(scheduled_start_at)
    cursor = connection.execute(
        """
        UPDATE tournaments
        SET status = 'scheduled', scheduled_start_at = ?, finished_at = NULL
        WHERE id = ? AND status IN ('draft', 'scheduled') AND started_at IS NULL
        """,
        (scheduled_start_at, tournament_id),
    )
    if cursor.rowcount == 0:
        raise ValueError("only a draft or scheduled tournament can be scheduled")
    tournament = get_tournament(connection, tournament_id)
    if tournament is None:
        raise ValueError("tournament does not exist")
    _reconcile_engine_relay_events_for_tournament(connection, tournament_id)
    return tournament


def unschedule_tournament(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> TournamentRecord:
    tournament = lock_tournament(connection, tournament_id)
    if tournament is None:
        raise ValueError("tournament does not exist")
    if tournament.status != "scheduled" or tournament.started_at is not None:
        raise ValueError("only an unstarted scheduled tournament can be returned to draft")
    connection.execute("DELETE FROM games WHERE tournament_id = ?", (tournament_id,))
    connection.execute("DELETE FROM tournament_matches WHERE tournament_id = ?", (tournament_id,))
    connection.execute(
        """
        UPDATE tournaments
        SET status = 'draft', scheduled_start_at = NULL, current_round = 0
        WHERE id = ?
        """,
        (tournament_id,),
    )
    current = get_tournament(connection, tournament_id)
    if current is None:
        raise ValueError("tournament does not exist")
    _reconcile_engine_relay_events_for_tournament(connection, tournament_id)
    return current


def set_tournament_concurrency(
    connection: sqlite3.Connection,
    tournament_id: int,
    concurrency: int,
) -> None:
    tournament = get_tournament(connection, tournament_id)
    if tournament is None:
        raise ValueError("tournament does not exist")
    config = TournamentConfig.model_validate(
        {
            **tournament.config.model_dump(mode="json"),
            "concurrency": concurrency,
        }
    )
    connection.execute(
        "UPDATE tournaments SET config = ? WHERE id = ?",
        (config.model_dump_json(), tournament_id),
    )


def update_tournament_name(
    connection: sqlite3.Connection,
    tournament_id: int,
    name: str,
) -> None:
    connection.execute(
        "UPDATE tournaments SET name = ? WHERE id = ?",
        (name, tournament_id),
    )


def claim_tournament_worker_profile(
    connection: sqlite3.Connection,
    tournament_id: int,
    worker_profile: str,
) -> bool:
    cursor = connection.execute(
        """
        UPDATE tournaments
        SET worker_profile = COALESCE(worker_profile, ?)
        WHERE id = ?
          AND (worker_profile IS NULL OR worker_profile = ?)
        """,
        (worker_profile, tournament_id, worker_profile),
    )
    return cursor.rowcount > 0


def _abandon_tournament_games(
    connection: sqlite3.Connection,
    tournament_id: int,
    now: str,
) -> None:
    reason = "tournament aborted"
    connection.execute(
        """
        UPDATE game_assignments AS assignment
        SET status = 'abandoned',
            finished_at = COALESCE(assignment.finished_at, ?),
            last_error = COALESCE(assignment.last_error, ?)
        FROM games AS game
        WHERE assignment.game_id = game.id
          AND assignment.status IN ('assigned', 'acked', 'live')
          AND game.tournament_id = ?
          AND game.status != 'finished'
        """,
        (now, reason, tournament_id),
    )
    connection.execute(
        """
        UPDATE games
        SET status = 'abandoned',
            termination = COALESCE(termination, ?),
            finished_at = COALESCE(finished_at, ?)
        WHERE tournament_id = ?
          AND status != 'finished'
        """,
        (reason, now, tournament_id),
    )


def update_tournament(
    connection: sqlite3.Connection,
    tournament_id: int,
    *,
    name: str,
    config: TournamentConfig,
) -> None:
    """Update a tournament's name, config, and participant list."""
    relay = connection.execute(
        """
        SELECT id
        FROM engine_relay_fixtures
        WHERE tournament_id = ?
        """,
        (tournament_id,),
    ).fetchone()
    if relay is not None:
        if config.rated:
            raise ValueError("engine relay tournaments are always unrated")
        anchors = [
            int(row["anchor_engine_id"])
            for row in connection.execute(
                """
                SELECT anchor_engine_id
                FROM engine_relay_fixture_teams
                WHERE fixture_id = ?
                ORDER BY position, team_id
                """,
                (relay["id"],),
            )
        ]
        if config.participants != anchors:
            raise ValueError("engine relay tournament anchors cannot be changed")
        if config.format != "round_robin" or config.time_control.category != "movenodes":
            raise ValueError("engine relay tournaments require round-robin move-node execution")
    connection.execute(
        """
        UPDATE tournaments
        SET name = ?, config = ?
        WHERE id = ?
        """,
        (
            name,
            config.model_dump_json(),
            tournament_id,
        ),
    )
    connection.execute("DELETE FROM participants WHERE tournament_id = ?", (tournament_id,))
    connection.executemany(
        """
        INSERT INTO participants (tournament_id, engine_id, seed)
        VALUES (?, ?, ?)
        """,
        (
            (tournament_id, engine_id, seed)
            for seed, engine_id in enumerate(config.participants, start=1)
        ),
    )


def set_tournament_participants(
    connection: sqlite3.Connection,
    tournament: TournamentRecord,
    participants: list[int],
    *,
    gauntlet_hero_engine_id: int | None = None,
) -> TournamentRecord:
    if connection.execute(
        "SELECT 1 FROM engine_relay_fixtures WHERE tournament_id = ?",
        (tournament.id,),
    ).fetchone() is not None and participants != tournament.config.participants:
        raise ValueError("engine relay tournament anchors cannot be changed")
    config_data = tournament.config.model_dump(mode="json")
    config_data["participants"] = participants
    if gauntlet_hero_engine_id is not None:
        config_data["format_options"] = {
            **config_data["format_options"],
            "hero_engine_id": gauntlet_hero_engine_id,
        }
    config = TournamentConfig.model_validate(config_data)
    connection.execute(
        "UPDATE tournaments SET config = ? WHERE id = ?",
        (config.model_dump_json(), tournament.id),
    )
    connection.execute(
        "DELETE FROM participants WHERE tournament_id = ?",
        (tournament.id,),
    )
    connection.executemany(
        """
        INSERT INTO participants (tournament_id, engine_id, seed)
        VALUES (?, ?, ?)
        """,
        (
            (tournament.id, engine_id, seed)
            for seed, engine_id in enumerate(participants, start=1)
        ),
    )
    updated = get_tournament(connection, tournament.id)
    if updated is None:
        raise ValueError("tournament does not exist")
    return updated


def delete_tournament(connection: sqlite3.Connection, tournament_id: int) -> None:
    """Delete a tournament and its games, moves, and participants (cascade)."""
    rating_commits = list_tournament_rating_commits(connection, tournament_id)
    for rating_commit in rating_commits:
        if rating_commit.status == "applied":
            raise ValueError("tournament results are already part of the ratings")
        if rating_commit.status in {"pending", "claimed"}:
            raise ValueError("tournament has a rating commit in progress")
    event_ids = tuple(
        int(row["event_id"])
        for row in connection.execute(
            "SELECT event_id FROM engine_relay_fixtures WHERE tournament_id = ?",
            (tournament_id,),
        )
    )
    connection.execute("DELETE FROM tournaments WHERE id = ?", (tournament_id,))
    if event_ids:
        from .events import reconcile_engine_relay_event

        for event_id in event_ids:
            reconcile_engine_relay_event(connection, event_id)


def create_game(
    connection: sqlite3.Connection,
    *,
    tournament_id: int,
    round: int,
    pair_index: int,
    white_engine_id: int,
    black_engine_id: int,
    match_id: int | None = None,
    game_number: int = 1,
    tiebreak_kind: str | None = None,
    opening_id: int | None = None,
    status: str = "pending",
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO games (
          tournament_id, round, pair_index, white_engine_id, black_engine_id,
          match_id, game_number, tiebreak_kind, opening_id, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tournament_id,
            round,
            pair_index,
            white_engine_id,
            black_engine_id,
            match_id,
            game_number,
            tiebreak_kind,
            opening_id,
            status,
        ),
    )
    return int(cursor.lastrowid)


def create_tournament_match(
    connection: sqlite3.Connection,
    *,
    tournament_id: int,
    round: int,
    match_index: int,
    engine1_id: int,
    engine2_id: int | None,
    status: str = "pending",
    winner_engine_id: int | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO tournament_matches (
          tournament_id, round, match_index, engine1_id, engine2_id, status,
          winner_engine_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tournament_id,
            round,
            match_index,
            engine1_id,
            engine2_id,
            status,
            winner_engine_id,
        ),
    )
    return int(cursor.lastrowid)


def list_tournament_matches(
    connection: sqlite3.Connection,
    tournament_id: int,
    *,
    round: int | None = None,
) -> tuple[TournamentMatchRecord, ...]:
    if round is None:
        rows = connection.execute(
            """
            SELECT * FROM tournament_matches
            WHERE tournament_id = ?
            ORDER BY round, match_index, id
            """,
            (tournament_id,),
        )
    else:
        rows = connection.execute(
            """
            SELECT * FROM tournament_matches
            WHERE tournament_id = ? AND round = ?
            ORDER BY match_index, id
            """,
            (tournament_id, round),
        )
    return tuple(_tournament_match_from_row(row) for row in rows)


def finish_tournament_match(
    connection: sqlite3.Connection,
    match_id: int,
    *,
    winner_engine_id: int | None,
) -> None:
    connection.execute(
        """
        UPDATE tournament_matches
        SET status = 'finished', winner_engine_id = ?
        WHERE id = ? AND status = 'pending'
        """,
        (winner_engine_id, match_id),
    )


def get_game(connection: sqlite3.Connection, game_id: int) -> GameRecord | None:
    row = connection.execute(
        "SELECT * FROM games WHERE id = ?",
        (game_id,),
    ).fetchone()
    if row is None:
        return None
    return _game_from_row(row)


def list_games(
    connection: sqlite3.Connection,
    tournament_id: int,
    *,
    status: str | None = None,
    include_pgn: bool = False,
) -> tuple[GameRecord, ...]:
    columns = (
        "*"
        if include_pgn
        else """
        id, tournament_id, round, pair_index, white_engine_id, black_engine_id,
        match_id, game_number, tiebreak_kind, opening_id, status, result,
        termination, NULL::text AS pgn, white_hw, black_hw, started_at, finished_at
        """
    )
    if status is None:
        rows = connection.execute(
            f"""
            SELECT {columns} FROM games
            WHERE tournament_id = ?
            ORDER BY round, pair_index, id
            """,
            (tournament_id,),
        )
    else:
        rows = connection.execute(
            f"""
            SELECT {columns} FROM games
            WHERE tournament_id = ? AND status = ?
            ORDER BY round, pair_index, id
            """,
            (tournament_id, status),
        )
    return tuple(_game_from_row(row) for row in rows)


def list_games_for_tournaments(
    connection: sqlite3.Connection,
    tournament_ids: Iterable[int],
) -> tuple[GameRecord, ...]:
    selected = tuple(dict.fromkeys(int(value) for value in tournament_ids))
    if not selected:
        return ()
    placeholders = ", ".join("?" for _ in selected)
    rows = connection.execute(
        f"""
        SELECT
          id, tournament_id, round, pair_index, white_engine_id, black_engine_id,
          match_id, game_number, tiebreak_kind, opening_id, status, result,
          termination, NULL::text AS pgn, started_at, finished_at
        FROM games
        WHERE tournament_id IN ({placeholders})
        ORDER BY tournament_id, round, pair_index, id
        """,
        selected,
    )
    return tuple(_game_from_row(row) for row in rows)


_WIN_TERMINATIONS = (
    "checkmate",
    "timeout",
    "illegal move",
    "engine error",
    "variant end",
)
_DRAW_TERMINATIONS = (
    "stalemate",
    "insufficient material",
    "seventy-five moves",
    "fivefold repetition",
    "fifty moves",
    "threefold repetition",
    "variant end",
)


def _game_result_filter(
    result_types: Iterable[str] | None,
) -> tuple[str, tuple[str, ...]]:
    requested = tuple(dict.fromkeys(result_types or ()))
    if not requested:
        return "", ()

    termination = "LOWER(COALESCE(termination, ''))"
    decisive = "result IN ('1-0', '0-1')"
    draw = "result = '1/2-1/2'"
    win_known = " OR ".join(
        [f"{termination} = ?" for _ in _WIN_TERMINATIONS]
        + [f"{termination} LIKE ?", f"{termination} LIKE ?"]
    )
    draw_known = " OR ".join(
        [f"{termination} = ?" for _ in _DRAW_TERMINATIONS]
        + [f"{termination} LIKE ?", f"{termination} LIKE ?"]
    )
    filters: dict[str, tuple[str, tuple[str, ...]]] = {
        "win_checkmate": (f"{decisive} AND {termination} = ?", ("checkmate",)),
        "win_adjudication": (f"{decisive} AND {termination} LIKE ?", ("win adjudication%",)),
        "win_max_moves": (f"{decisive} AND {termination} LIKE ?", ("max moves%",)),
        "win_timeout": (f"{decisive} AND {termination} = ?", ("timeout",)),
        "win_illegal_move": (f"{decisive} AND {termination} = ?", ("illegal move",)),
        "win_engine_error": (f"{decisive} AND {termination} = ?", ("engine error",)),
        "win_variant_end": (f"{decisive} AND {termination} = ?", ("variant end",)),
        "win_other": (
            f"{decisive} AND NOT ({win_known})",
            _WIN_TERMINATIONS + ("win adjudication%", "max moves%"),
        ),
        "draw_stalemate": (f"{draw} AND {termination} = ?", ("stalemate",)),
        "draw_insufficient_material": (f"{draw} AND {termination} = ?", ("insufficient material",)),
        "draw_adjudication": (f"{draw} AND {termination} LIKE ?", ("draw adjudication%",)),
        "draw_max_moves": (f"{draw} AND {termination} LIKE ?", ("max moves%",)),
        "draw_threefold_repetition": (f"{draw} AND {termination} = ?", ("threefold repetition",)),
        "draw_fivefold_repetition": (f"{draw} AND {termination} = ?", ("fivefold repetition",)),
        "draw_fifty_moves": (f"{draw} AND {termination} = ?", ("fifty moves",)),
        "draw_seventyfive_moves": (f"{draw} AND {termination} = ?", ("seventy-five moves",)),
        "draw_variant_end": (f"{draw} AND {termination} = ?", ("variant end",)),
        "draw_other": (
            f"{draw} AND NOT ({draw_known})",
            _DRAW_TERMINATIONS + ("draw adjudication%", "max moves%"),
        ),
    }
    selected = [filters[value] for value in requested if value in filters]
    if not selected:
        return " AND 1 = 0", ()
    sql = " AND (" + " OR ".join(f"({condition})" for condition, _ in selected) + ")"
    parameters = tuple(parameter for _, values in selected for parameter in values)
    return sql, parameters


def count_games(
    connection: sqlite3.Connection,
    tournament_id: int,
    *,
    status: str | None = None,
    result_types: Iterable[str] | None = None,
) -> int:
    conditions = "tournament_id = ?"
    parameters: list[Any] = [tournament_id]
    if status is not None:
        conditions += " AND status = ?"
        parameters.append(status)
    result_sql, result_parameters = _game_result_filter(result_types)
    conditions += result_sql
    parameters.extend(result_parameters)
    row = connection.execute(
        f"SELECT COUNT(*) AS count FROM games WHERE {conditions}",
        tuple(parameters),
    ).fetchone()
    return int(row["count"])


def list_games_page(
    connection: sqlite3.Connection,
    tournament_id: int,
    *,
    page: int,
    page_size: int,
    status: str | None = None,
    result_types: Iterable[str] | None = None,
) -> tuple[GameRecord, ...]:
    columns = """
      id, tournament_id, round, pair_index, white_engine_id, black_engine_id,
      match_id, game_number, tiebreak_kind, opening_id, status, result,
      termination, NULL::text AS pgn, white_hw, black_hw, started_at, finished_at
    """
    offset = (page - 1) * page_size
    conditions = "tournament_id = ?"
    parameters: list[Any] = [tournament_id]
    if status is not None:
        conditions += " AND status = ?"
        parameters.append(status)
    result_sql, result_parameters = _game_result_filter(result_types)
    conditions += result_sql
    parameters.extend(result_parameters)
    parameters.extend((page_size, offset))
    rows = connection.execute(
        f"""
        SELECT {columns} FROM games
        WHERE {conditions}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        tuple(parameters),
    )
    return tuple(_game_from_row(row) for row in rows)


def replay_game(
    connection: sqlite3.Connection,
    tournament_id: int,
    game_id: int,
) -> bool:
    row = connection.execute(
        "SELECT * FROM games WHERE id = ? AND tournament_id = ? FOR UPDATE",
        (game_id, tournament_id),
    ).fetchone()
    if row is None:
        raise ValueError("game does not belong to this tournament")
    game = _game_from_row(row)
    if game.status != "finished" or game.result is None:
        raise ValueError("only completed games can be replayed")
    _ensure_games_are_not_committed(connection, (game.id,))
    _delete_system_chat_events(
        connection,
        tournament_id,
        (f"game.{game.id}.finished", "tournament.finished"),
    )
    connection.execute("DELETE FROM moves WHERE game_id = ?", (game.id,))
    connection.execute("DELETE FROM game_hardware_scores WHERE game_id = ?", (game.id,))
    connection.execute(
        """
        UPDATE games
        SET status = 'pending',
            result = NULL,
            termination = NULL,
            pgn = NULL,
            white_hw = NULL,
            black_hw = NULL,
            started_at = NULL,
            finished_at = NULL
        WHERE id = ?
        """,
        (game.id,),
    )
    reopened = connection.execute(
        """
        UPDATE tournaments
        SET status = 'running', finished_at = NULL
        WHERE id = ? AND status IN ('finished', 'aborted')
        """,
        (tournament_id,),
    ).rowcount > 0
    if reopened:
        _reconcile_engine_relay_events_for_tournament(connection, tournament_id)
    return reopened


def invalidate_game_pair(
    connection: sqlite3.Connection,
    tournament_id: int,
    game_id: int,
) -> tuple[int, ...]:
    row = connection.execute(
        "SELECT * FROM games WHERE id = ? AND tournament_id = ? FOR UPDATE",
        (game_id, tournament_id),
    ).fetchone()
    if row is None:
        raise ValueError("game does not belong to this tournament")
    game = _game_from_row(row)
    first_game_number = game.game_number if game.game_number % 2 else game.game_number - 1
    game_numbers = (first_game_number, first_game_number + 1)
    if game.match_id is not None:
        rows = connection.execute(
            """
            SELECT id FROM games
            WHERE tournament_id = ? AND match_id = ?
              AND game_number IN (?, ?)
            FOR UPDATE
            """,
            (tournament_id, game.match_id, *game_numbers),
        )
    else:
        rows = connection.execute(
            """
            SELECT id FROM games
            WHERE tournament_id = ? AND match_id IS NULL
              AND game_number IN (?, ?)
              AND (
                (white_engine_id = ? AND black_engine_id = ?)
                OR (white_engine_id = ? AND black_engine_id = ?)
              )
            FOR UPDATE
            """,
            (
                tournament_id,
                *game_numbers,
                game.white_engine_id,
                game.black_engine_id,
                game.black_engine_id,
                game.white_engine_id,
            ),
        )
    game_ids = tuple(sorted(int(item["id"]) for item in rows))
    if game.id not in game_ids:
        game_ids = tuple(sorted((*game_ids, game.id)))
    _ensure_games_are_not_committed(connection, game_ids)
    _delete_system_chat_events(
        connection,
        tournament_id,
        (*tuple(f"game.{item}.finished" for item in game_ids), "tournament.finished"),
    )
    placeholders = ", ".join("?" for _ in game_ids)
    connection.execute(f"DELETE FROM games WHERE id IN ({placeholders})", game_ids)
    return game_ids


def invalidate_tournament_participant_games(
    connection: sqlite3.Connection,
    tournament_id: int,
    engine_id: int,
) -> TournamentParticipantGameRemoval:
    rows = connection.execute(
        """
        SELECT id, status FROM games
        WHERE tournament_id = ?
          AND (white_engine_id = ? OR black_engine_id = ?)
        FOR UPDATE
        """,
        (tournament_id, engine_id, engine_id),
    ).fetchall()
    game_ids = tuple(sorted(int(row["id"]) for row in rows))
    counts = {
        "pending": 0,
        "assigned": 0,
        "live": 0,
        "finished": 0,
        "abandoned": 0,
    }
    for row in rows:
        counts[str(row["status"])] += 1
    if game_ids:
        _ensure_games_are_not_committed(connection, game_ids)
        _delete_system_chat_events(
            connection,
            tournament_id,
            (*tuple(f"game.{item}.finished" for item in game_ids), "tournament.finished"),
        )
        placeholders = ", ".join("?" for _ in game_ids)
        connection.execute(f"DELETE FROM games WHERE id IN ({placeholders})", game_ids)
    return TournamentParticipantGameRemoval(
        game_ids=game_ids,
        pending=counts["pending"],
        assigned=counts["assigned"],
        live=counts["live"],
        finished=counts["finished"],
        abandoned=counts["abandoned"],
    )


def _ensure_games_are_not_committed(
    connection: sqlite3.Connection,
    game_ids: tuple[int, ...],
) -> None:
    placeholders = ", ".join("?" for _ in game_ids)
    row = connection.execute(
        f"SELECT 1 FROM rating_list_history WHERE game_id IN ({placeholders}) LIMIT 1",
        game_ids,
    ).fetchone()
    if row is not None:
        raise ValueError("game results are already part of a rating list")


def _delete_system_chat_events(
    connection: sqlite3.Connection,
    tournament_id: int,
    event_keys: tuple[str, ...],
) -> None:
    placeholders = ", ".join("?" for _ in event_keys)
    rows = connection.execute(
        f"""
        SELECT message_id FROM system_chat_events
        WHERE tournament_id = ? AND event_key IN ({placeholders})
        """,
        (tournament_id, *event_keys),
    )
    message_ids = tuple(int(row["message_id"]) for row in rows)
    if not message_ids:
        return
    message_placeholders = ", ".join("?" for _ in message_ids)
    connection.execute(
        f"DELETE FROM chat_messages WHERE id IN ({message_placeholders})",
        message_ids,
    )


def mark_game_live(connection: sqlite3.Connection, game_id: int) -> None:
    connection.execute(
        """
        UPDATE games
        SET status = 'live', started_at = COALESCE(started_at, ?)
        WHERE id = ?
        """,
        (utc_now(), game_id),
    )


def finish_game(
    connection: sqlite3.Connection,
    game_id: int,
    *,
    result: str,
    termination: str,
    pgn: str | None = None,
    white_hw: HardwareInfo | None = None,
    black_hw: HardwareInfo | None = None,
) -> None:
    connection.execute(
        """
        UPDATE games
        SET status = 'finished',
            result = ?,
            termination = ?,
            pgn = ?,
            white_hw = ?,
            black_hw = ?,
            finished_at = ?
        WHERE id = ?
        """,
        (
            result,
            termination,
            pgn,
            white_hw.model_dump_json() if white_hw is not None else None,
            black_hw.model_dump_json() if black_hw is not None else None,
            utc_now(),
            game_id,
        ),
    )
    connection.execute(
        "DELETE FROM game_pause_checkpoints WHERE game_id = ?",
        (game_id,),
    )


def record_move(
    connection: sqlite3.Connection,
    *,
    game_id: int,
    assignment_id: int | None = None,
    assignment_key: str | None = None,
    ply: int,
    uci: str,
    san: str,
    is_book: bool = False,
    eval_cp: int | None = None,
    eval_mate: int | None = None,
    score_bound: str | None = None,
    depth: int | None = None,
    seldepth: int | None = None,
    nodes: int | None = None,
    nps: int | None = None,
    hashfull: int | None = None,
    pv: str | None = None,
    info_line: str | None = None,
    time_ms: int = 0,
    clock_after_ms: int = 0,
    engine_version_id: int | None = None,
) -> MoveRecord:
    if (assignment_id is None) != (assignment_key is None):
        raise ValueError("assignment id and key must be provided together")
    values = (
        game_id,
        ply,
        uci,
        san,
        int(is_book),
        eval_cp,
        eval_mate,
        score_bound,
        depth,
        seldepth,
        nodes,
        nps,
        hashfull,
        pv,
        info_line,
        time_ms,
        clock_after_ms,
        engine_version_id,
    )
    if assignment_id is None:
        cursor = connection.execute(
            """
            INSERT INTO moves (
              game_id, ply, uci, san, is_book, eval_cp, eval_mate, score_bound,
              depth, seldepth, nodes, nps, hashfull, pv, info_line, time_ms, clock_after_ms,
              engine_version_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
    else:
        cursor = connection.execute(
            """
            INSERT INTO moves (
              game_id, ply, uci, san, is_book, eval_cp, eval_mate, score_bound,
              depth, seldepth, nodes, nps, hashfull, pv, info_line, time_ms, clock_after_ms,
              engine_version_id
            )
            SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            WHERE EXISTS (
              SELECT 1 FROM game_assignments
              WHERE id = ? AND assignment_key = ? AND game_id = ?
                AND status IN ('assigned', 'acked', 'live')
            )
            """,
            (*values, assignment_id, assignment_key, game_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"assignment {assignment_id} is no longer active")
    return MoveRecord(
        game_id=game_id,
        ply=ply,
        uci=uci,
        san=san,
        is_book=is_book,
        eval_cp=eval_cp,
        eval_mate=eval_mate,
        score_bound=score_bound,
        depth=depth,
        seldepth=seldepth,
        nodes=nodes,
        nps=nps,
        hashfull=hashfull,
        pv=pv,
        info_line=info_line,
        time_ms=time_ms,
        clock_after_ms=clock_after_ms,
        engine_version_id=engine_version_id,
    )


def list_moves(connection: sqlite3.Connection, game_id: int) -> tuple[MoveRecord, ...]:
    return tuple(
        _move_from_row(row)
        for row in connection.execute(
            "SELECT * FROM moves WHERE game_id = ? ORDER BY ply",
            (game_id,),
        )
    )


def create_game_assignment(
    connection: sqlite3.Connection,
    *,
    game_id: int,
    assignment_key: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO game_assignments (game_id, assignment_key, worker_id)
        VALUES (?, ?, NULL)
        """,
        (game_id, assignment_key),
    )
    return int(cursor.lastrowid)


def get_game_assignment(
    connection: sqlite3.Connection,
    assignment_id: int,
) -> GameAssignmentRecord | None:
    return _get_game_assignment(connection, "id", assignment_id)


def get_game_assignment_for_game(
    connection: sqlite3.Connection,
    game_id: int,
) -> GameAssignmentRecord | None:
    return _get_game_assignment(connection, "game_id", game_id)


def get_game_pause_checkpoint(
    connection: sqlite3.Connection,
    game_id: int,
) -> GamePauseCheckpointRecord | None:
    row = connection.execute(
        "SELECT game_id, state, paused_at FROM game_pause_checkpoints WHERE game_id = ?",
        (game_id,),
    ).fetchone()
    if row is None:
        return None
    state = json.loads(row["state"])
    if not isinstance(state, dict):
        raise RuntimeError(f"invalid pause checkpoint for game {game_id}")
    return GamePauseCheckpointRecord(
        game_id=int(row["game_id"]),
        state=state,
        paused_at=str(row["paused_at"]),
    )


def pause_game_assignment(
    connection: sqlite3.Connection,
    assignment_id: int,
    assignment_key: str,
    state: dict[str, Any],
) -> None:
    assignment = get_game_assignment(connection, assignment_id)
    if assignment is None or assignment.assignment_key != assignment_key:
        raise RuntimeError(f"assignment {assignment_id} is no longer active")
    now = utc_now()
    cursor = connection.execute(
        """
        UPDATE game_assignments
        SET status = 'expired', finished_at = ?, last_error = NULL, worker_id = NULL
        WHERE id = ? AND assignment_key = ? AND status IN ('assigned', 'acked', 'live')
        """,
        (now, assignment_id, assignment_key),
    )
    if cursor.rowcount != 1:
        raise RuntimeError(f"assignment {assignment_id} is no longer active")
    connection.execute(
        """
        INSERT INTO game_pause_checkpoints (game_id, state, paused_at)
        VALUES (?, ?, ?)
        ON CONFLICT(game_id) DO UPDATE SET
          state = excluded.state,
          paused_at = excluded.paused_at
        """,
        (assignment.game_id, json.dumps(state, separators=(",", ":")), now),
    )


def pause_unstarted_game_assignment(
    connection: sqlite3.Connection,
    assignment_id: int,
    assignment_key: str,
) -> None:
    assignment = get_game_assignment(connection, assignment_id)
    if assignment is None or assignment.assignment_key != assignment_key:
        return
    now = utc_now()
    cursor = connection.execute(
        """
        UPDATE game_assignments
        SET status = 'expired', finished_at = ?, last_error = NULL, worker_id = NULL
        WHERE id = ? AND assignment_key = ? AND status IN ('assigned', 'acked')
        """,
        (now, assignment_id, assignment_key),
    )
    if cursor.rowcount == 1:
        connection.execute(
            "UPDATE games SET status = 'pending' WHERE id = ? AND status = 'assigned'",
            (assignment.game_id,),
        )


def resume_paused_tournament_games(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> int:
    cursor = connection.execute(
        """
        UPDATE games
        SET status = 'pending'
        WHERE tournament_id = ?
          AND status = 'live'
          AND EXISTS (
            SELECT 1 FROM game_pause_checkpoints checkpoint
            WHERE checkpoint.game_id = games.id
          )
        """,
        (tournament_id,),
    )
    return cursor.rowcount


def record_game_assignment_progress(
    connection: sqlite3.Connection,
    progress: AssignmentProgress,
    *,
    source: str,
) -> int:
    assignment = get_game_assignment(connection, progress.assignment_id)
    if assignment is None:
        raise RuntimeError(f"unknown assignment {progress.assignment_id}")
    if (
        assignment.assignment_key != progress.assignment_key
        or assignment.game_id != progress.game_id
    ):
        raise RuntimeError(f"progress does not match assignment {progress.assignment_id}")
    cursor = connection.execute(
        """
        INSERT INTO game_assignment_progress (
          assignment_id, assignment_key, game_id, source,
          stage, stage_label, stage_order, substage, status, detail,
          engine_id, engine_name, current_value, total_value, metadata, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            progress.assignment_id,
            progress.assignment_key,
            progress.game_id,
            source,
            progress.stage,
            progress.stage_label,
            progress.stage_order,
            progress.substage,
            progress.status,
            progress.detail,
            progress.engine_id,
            progress.engine_name,
            progress.current,
            progress.total,
            _json_dump(progress.metadata),
            utc_now(),
        ),
    )
    return cursor.lastrowid


def record_game_assignment_progress_batch(
    connection: sqlite3.Connection,
    items: Iterable[tuple[AssignmentProgress, str]],
) -> None:
    pending = tuple(items)
    if not pending:
        return
    assignment_ids = tuple(
        dict.fromkeys(progress.assignment_id for progress, _source in pending)
    )
    placeholders = ", ".join("?" for _ in assignment_ids)
    rows = connection.execute(
        f"""
        SELECT id, assignment_key, game_id
        FROM game_assignments
        WHERE id IN ({placeholders})
        """,
        assignment_ids,
    )
    assignments = {
        int(row["id"]): (str(row["assignment_key"]), int(row["game_id"]))
        for row in rows
    }
    valid = tuple(
        (progress, source)
        for progress, source in pending
        if assignments.get(progress.assignment_id)
        == (progress.assignment_key, progress.game_id)
    )
    if not valid:
        return
    occurred_at = utc_now()
    connection.executemany(
        """
        INSERT INTO game_assignment_progress (
          assignment_id, assignment_key, game_id, source,
          stage, stage_label, stage_order, substage, status, detail,
          engine_id, engine_name, current_value, total_value, metadata, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                progress.assignment_id,
                progress.assignment_key,
                progress.game_id,
                source,
                progress.stage,
                progress.stage_label,
                progress.stage_order,
                progress.substage,
                progress.status,
                progress.detail,
                progress.engine_id,
                progress.engine_name,
                progress.current,
                progress.total,
                _json_dump(progress.metadata),
                occurred_at,
            )
            for progress, source in valid
        ),
    )
def list_game_assignment_progress(
    connection: sqlite3.Connection,
    game_id: int,
    *,
    limit: int = 100,
) -> tuple[GameProgressRecord, ...]:
    assignment = get_game_assignment_for_game(connection, game_id)
    if assignment is None:
        return ()
    rows = connection.execute(
        """
        SELECT * FROM (
          SELECT * FROM game_assignment_progress
          WHERE game_id = ? AND assignment_key = ?
          ORDER BY id DESC
          LIMIT ?
        ) AS recent
        ORDER BY id
        """,
        (game_id, assignment.assignment_key, max(1, min(limit, 500))),
    )
    return tuple(_game_progress_from_row(row) for row in rows)


def list_game_assignment_stage_progress(
    connection: sqlite3.Connection,
    game_id: int,
) -> tuple[GameProgressRecord, ...]:
    assignment = get_game_assignment_for_game(connection, game_id)
    if assignment is None:
        return ()
    rows = connection.execute(
        """
        SELECT * FROM (
          SELECT DISTINCT ON (stage) *
          FROM game_assignment_progress
          WHERE game_id = ? AND assignment_key = ?
          ORDER BY stage, id DESC
        ) AS latest
        ORDER BY stage_order, id
        """,
        (game_id, assignment.assignment_key),
    )
    return tuple(_game_progress_from_row(row) for row in rows)


def _get_game_assignment(
    connection: sqlite3.Connection,
    column: str,
    value: int,
) -> GameAssignmentRecord | None:
    row = connection.execute(
        f"SELECT * FROM game_assignments WHERE {column} = ?",
        (value,),
    ).fetchone()
    if row is None:
        return None
    return _game_assignment_from_row(row)


def assign_game_to_worker(
    connection: sqlite3.Connection,
    *,
    game_id: int,
    assignment_key: str,
    worker_id: int,
) -> GameAssignmentRecord | None:
    now = utc_now()
    claimed = connection.execute(
        """
        UPDATE games
        SET status = 'assigned'
        WHERE id = ? AND status = 'pending'
        RETURNING id
        """,
        (game_id,),
    ).fetchone()
    if claimed is None:
        return None
    connection.execute(
        """
        INSERT INTO game_assignments (
          game_id, assignment_key, worker_id,
          status, sent_at, acked_at, finished_at, last_error
        )
        VALUES (?, ?, ?, 'assigned', ?, NULL, NULL, NULL)
        ON CONFLICT(game_id) DO UPDATE SET
          assignment_key = excluded.assignment_key,
          worker_id = excluded.worker_id,
          status = 'assigned',
          sent_at = excluded.sent_at,
          acked_at = NULL,
          finished_at = NULL,
          last_error = NULL
        """,
        (
            game_id,
            assignment_key,
            worker_id,
            now,
        ),
    )
    assignment = get_game_assignment_for_game(connection, game_id)
    if assignment is None:
        raise RuntimeError(f"failed to assign game {game_id}")
    return assignment


def mark_game_assignment_live(
    connection: sqlite3.Connection,
    assignment_id: int,
) -> None:
    connection.execute(
        """
        UPDATE game_assignments
        SET status = 'live', acked_at = COALESCE(acked_at, ?)
        WHERE id = ? AND status IN ('assigned', 'acked', 'live')
        """,
        (utc_now(), assignment_id),
    )


def acknowledge_game_assignment(
    connection: sqlite3.Connection,
    assignment_id: int,
    assignment_key: str,
) -> None:
    cursor = connection.execute(
        """
        UPDATE game_assignments
        SET status = 'acked', acked_at = COALESCE(acked_at, ?)
        WHERE id = ? AND assignment_key = ? AND status IN ('assigned', 'acked')
        """,
        (utc_now(), assignment_id, assignment_key),
    )
    if cursor.rowcount != 1:
        raise RuntimeError(f"assignment {assignment_id} is no longer awaiting readiness")


def finish_game_assignment(
    connection: sqlite3.Connection,
    assignment_id: int,
    assignment_key: str,
) -> None:
    connection.execute(
        """
        UPDATE game_assignments
        SET status = 'finished', finished_at = ?
        WHERE id = ? AND assignment_key = ?
        """,
        (utc_now(), assignment_id, assignment_key),
    )


def fail_game_assignment(
    connection: sqlite3.Connection,
    assignment_id: int,
    assignment_key: str,
    error: str,
) -> None:
    assignment = get_game_assignment(connection, assignment_id)
    if assignment is None or assignment.assignment_key != assignment_key:
        return
    now = utc_now()
    cursor = connection.execute(
        """
        UPDATE game_assignments
        SET status = 'abandoned', finished_at = ?, last_error = ?
        WHERE id = ? AND assignment_key = ? AND status IN ('assigned', 'acked', 'live')
        """,
        (now, error[:500], assignment_id, assignment_key),
    )
    if cursor.rowcount == 1:
        _reset_games_for_replay(
            connection,
            (assignment.game_id,),
            reason=error,
            occurred_at=now,
        )


def create_worker(
    connection: sqlite3.Connection,
    *,
    label: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO workers (label, status)
        VALUES (?, 'minted')
        """,
        (label,),
    )
    return int(cursor.lastrowid)


def mint_worker_token(
    connection: sqlite3.Connection,
    *,
    label: str,
    ttl_seconds: int = 7200,
) -> WorkerToken:
    worker_id = create_worker(
        connection,
        label=label,
    )
    return mint_worker_token_for_worker(
        connection,
        worker_id=worker_id,
        ttl_seconds=ttl_seconds,
    )


def mint_worker_token_for_worker(
    connection: sqlite3.Connection,
    *,
    worker_id: int,
    ttl_seconds: int = 7200,
) -> WorkerToken:
    token = secrets.token_urlsafe(32)
    expires_at = (utc_now_datetime() + timedelta(seconds=ttl_seconds)).isoformat(
        timespec="seconds"
    )
    cursor = connection.execute(
        """
        UPDATE workers
        SET token_hash = ?,
            token_expires_at = ?,
            status = 'minted'
        WHERE id = ?
          AND status != 'revoked'
          AND session_id IS NULL
        """,
        (hash_worker_token(token), expires_at, worker_id),
    )
    if cursor.rowcount == 0:
        raise ValueError("worker cannot receive a registration token")
    return WorkerToken(
        worker_id=worker_id,
        token=token,
        expires_at=expires_at,
    )


def hash_worker_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_worker_by_token(
    connection: sqlite3.Connection,
    token: str,
) -> WorkerRecord | None:
    row = connection.execute(
        "SELECT * FROM workers WHERE token_hash = ?",
        (hash_worker_token(token),),
    ).fetchone()
    if row is None:
        return None
    return _worker_from_row(row)


def get_worker_by_session_id(
    connection: sqlite3.Connection,
    session_id: str,
) -> WorkerRecord | None:
    row = connection.execute(
        "SELECT * FROM workers WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return _worker_from_row(row)


def worker_token_is_valid(record: WorkerRecord, *, now: datetime | None = None) -> bool:
    if record.status == "revoked":
        return False

    if record.token_expires_at is None:
        return False

    check_time = now or utc_now_datetime()
    expires_at = datetime.fromisoformat(record.token_expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at > check_time


def upsert_worker_connection(
    connection: sqlite3.Connection,
    *,
    worker_id: int,
    label: str,
    session_id: str,
    app_commit: str,
    protocol_version: int,
    machine_id: str,
    hw: HardwareInfo,
    status: str = "connected",
) -> int:
    connection.execute(
        """
        INSERT INTO workers (
          id, label, status, session_id, app_commit, protocol_version, machine_id,
          hw, last_seen
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          label = excluded.label,
          token_hash = NULL,
          token_expires_at = NULL,
          status = excluded.status,
          session_id = excluded.session_id,
          app_commit = excluded.app_commit,
          protocol_version = excluded.protocol_version,
          machine_id = excluded.machine_id,
          hw = excluded.hw,
          last_seen = excluded.last_seen
        WHERE workers.status != 'revoked'
        """,
        (
            worker_id,
            label,
            status,
            session_id,
            app_commit,
            protocol_version,
            machine_id,
            hw.model_dump_json(),
            utc_now(),
        ),
    )
    return worker_id


def get_worker(connection: sqlite3.Connection, worker_id: int) -> WorkerRecord | None:
    row = connection.execute(
        "SELECT * FROM workers WHERE id = ?",
        (worker_id,),
    ).fetchone()
    if row is None:
        return None
    return _worker_from_row(row)


def list_workers(connection: sqlite3.Connection) -> tuple[WorkerRecord, ...]:
    return tuple(
        _worker_from_row(row)
        for row in connection.execute(
            "SELECT * FROM workers WHERE status != 'revoked' ORDER BY id"
        )
    )


def list_worker_tournament_ids(
    connection: sqlite3.Connection,
    worker_id: int,
) -> tuple[int, ...]:
    return tuple(
        int(row["tournament_id"])
        for row in connection.execute(
            """
            SELECT tournament_id
            FROM worker_tournament_permissions
            WHERE worker_id = ?
            ORDER BY tournament_id
            """,
            (worker_id,),
        )
    )


def list_worker_event_ids(
    connection: sqlite3.Connection,
    worker_id: int,
) -> tuple[int, ...]:
    return tuple(
        int(row["event_id"])
        for row in connection.execute(
            """
            SELECT event_id
            FROM worker_event_permissions
            WHERE worker_id = ?
            ORDER BY event_id
            """,
            (worker_id,),
        )
    )


def get_worker_event_fixture(
    connection: sqlite3.Connection,
    worker_id: int,
) -> EventFixtureWorkerRecord | None:
    row = connection.execute(
        """
        SELECT claim.*, tournament.status AS tournament_status
        FROM event_fixture_workers claim
        JOIN tournaments tournament ON tournament.id = claim.tournament_id
        WHERE claim.worker_id = ?
        """,
        (worker_id,),
    ).fetchone()
    if row is None:
        return None
    if row["tournament_status"] not in {"scheduled", "running", "paused"}:
        connection.execute(
            "DELETE FROM event_fixture_workers WHERE tournament_id = ?",
            (int(row["tournament_id"]),),
        )
        return None
    return EventFixtureWorkerRecord(
        tournament_id=int(row["tournament_id"]),
        event_id=int(row["event_id"]),
        worker_id=int(row["worker_id"]),
        claimed_at=str(row["claimed_at"]),
    )


def list_worker_event_fixture_candidates(
    connection: sqlite3.Connection,
    worker_id: int,
) -> tuple[int, ...]:
    return tuple(
        int(row["tournament_id"])
        for row in connection.execute(
            """
            SELECT fixture.tournament_id
            FROM engine_relay_fixtures fixture
            JOIN events event ON event.id = fixture.event_id
            JOIN tournaments tournament ON tournament.id = fixture.tournament_id
            LEFT JOIN event_fixture_workers claim
              ON claim.tournament_id = fixture.tournament_id
            WHERE event.status NOT IN ('completed', 'cancelled')
              AND tournament.status IN ('scheduled', 'running')
              AND claim.tournament_id IS NULL
              AND (
                EXISTS (
                  SELECT 1 FROM worker_event_permissions permission
                  WHERE permission.worker_id = ?
                    AND permission.event_id = fixture.event_id
                )
                OR EXISTS (
                  SELECT 1 FROM worker_tournament_permissions permission
                  WHERE permission.worker_id = ?
                    AND permission.tournament_id = fixture.tournament_id
                )
              )
            ORDER BY
              CASE WHEN tournament.status = 'running' THEN 0 ELSE 1 END,
              tournament.scheduled_start_at ASC NULLS LAST,
              fixture.position,
              fixture.id
            """,
            (worker_id, worker_id),
        )
    )


def event_fixture_worker(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> EventFixtureWorkerRecord | None:
    row = connection.execute(
        "SELECT * FROM event_fixture_workers WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone()
    if row is None:
        return None
    return EventFixtureWorkerRecord(
        tournament_id=int(row["tournament_id"]),
        event_id=int(row["event_id"]),
        worker_id=int(row["worker_id"]),
        claimed_at=str(row["claimed_at"]),
    )


def event_fixture_has_worker_permissions(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM engine_relay_fixtures fixture
        WHERE fixture.tournament_id = ?
          AND (
            EXISTS (
              SELECT 1 FROM event_fixture_workers claim
              WHERE claim.tournament_id = fixture.tournament_id
            )
            OR EXISTS (
              SELECT 1 FROM worker_event_permissions permission
              WHERE permission.event_id = fixture.event_id
            )
            OR EXISTS (
              SELECT 1 FROM worker_tournament_permissions permission
              WHERE permission.tournament_id = fixture.tournament_id
            )
          )
        LIMIT 1
        """,
        (tournament_id,),
    ).fetchone()
    return row is not None


def claim_event_fixture_worker(
    connection: sqlite3.Connection,
    tournament_id: int,
    worker_id: int,
) -> EventFixtureWorkerRecord | None:
    connection.execute(
        """
        INSERT INTO event_fixture_workers (
          tournament_id, event_id, worker_id, claimed_at
        )
        SELECT fixture.tournament_id, fixture.event_id, ?, ?
        FROM engine_relay_fixtures fixture
        JOIN tournaments tournament ON tournament.id = fixture.tournament_id
        WHERE fixture.tournament_id = ?
          AND tournament.status IN ('scheduled', 'running')
          AND (
            EXISTS (
              SELECT 1 FROM worker_event_permissions permission
              WHERE permission.worker_id = ?
                AND permission.event_id = fixture.event_id
            )
            OR EXISTS (
              SELECT 1 FROM worker_tournament_permissions permission
              WHERE permission.worker_id = ?
                AND permission.tournament_id = fixture.tournament_id
            )
          )
        ON CONFLICT DO NOTHING
        """,
        (worker_id, utc_now(), tournament_id, worker_id, worker_id),
    )
    claim = event_fixture_worker(connection, tournament_id)
    return claim if claim is not None and claim.worker_id == worker_id else None


def release_event_fixture_worker(
    connection: sqlite3.Connection,
    tournament_id: int,
    worker_id: int,
) -> None:
    connection.execute(
        "DELETE FROM event_fixture_workers WHERE tournament_id = ? AND worker_id = ?",
        (tournament_id, worker_id),
    )


def update_worker_assignment_settings(
    connection: sqlite3.Connection,
    worker_id: int,
    *,
    core_limit: int | None,
    tournament_scope: str,
    tournament_ids: Iterable[int],
    event_ids: Iterable[int],
) -> None:
    worker = get_worker(connection, worker_id)
    if worker is None:
        raise ValueError("worker not found")
    if core_limit is not None:
        if core_limit <= 0:
            raise ValueError("worker core limit must be positive")
        if worker.hw is not None and core_limit > worker.hw.logical_cores:
            raise ValueError(
                f"worker core limit cannot exceed its {worker.hw.logical_cores}-thread capacity"
            )
    if tournament_scope not in {"all", "selected"}:
        raise ValueError("worker tournament scope is invalid")

    selected_ids = tuple(dict.fromkeys(int(value) for value in tournament_ids))
    if any(tournament_id <= 0 for tournament_id in selected_ids):
        raise ValueError("worker tournament ids must be positive")
    if tournament_scope == "all":
        selected_ids = ()
    elif selected_ids:
        placeholders = ", ".join("?" for _ in selected_ids)
        available_ids = {
            int(row["id"])
            for row in connection.execute(
                f"""
                SELECT id FROM tournaments
                WHERE id IN ({placeholders})
                  AND status NOT IN ('finished', 'aborted')
                """,
                selected_ids,
            )
        }
        if available_ids != set(selected_ids):
            raise ValueError("workers can only be assigned to unfinished tournaments")

    selected_event_ids = tuple(dict.fromkeys(int(value) for value in event_ids))
    if any(event_id <= 0 for event_id in selected_event_ids):
        raise ValueError("worker event ids must be positive")
    if selected_event_ids:
        placeholders = ", ".join("?" for _ in selected_event_ids)
        available_event_ids = {
            int(row["id"])
            for row in connection.execute(
                f"""
                SELECT id FROM events
                WHERE id IN ({placeholders})
                  AND status NOT IN ('completed', 'cancelled')
                """,
                selected_event_ids,
            )
        }
        if available_event_ids != set(selected_event_ids):
            raise ValueError("workers can only be assigned to current events")

    connection.execute(
        """
        UPDATE workers
        SET core_limit = ?, tournament_scope = ?
        WHERE id = ?
        """,
        (core_limit, tournament_scope, worker_id),
    )
    connection.execute(
        "DELETE FROM worker_tournament_permissions WHERE worker_id = ?",
        (worker_id,),
    )
    if selected_ids:
        connection.executemany(
            """
            INSERT INTO worker_tournament_permissions (worker_id, tournament_id)
            VALUES (?, ?)
            """,
            ((worker_id, tournament_id) for tournament_id in selected_ids),
        )
    connection.execute(
        "DELETE FROM worker_event_permissions WHERE worker_id = ?",
        (worker_id,),
    )
    if selected_event_ids:
        connection.executemany(
            """
            INSERT INTO worker_event_permissions (worker_id, event_id)
            VALUES (?, ?)
            """,
            ((worker_id, event_id) for event_id in selected_event_ids),
        )
    connection.execute(
        """
        DELETE FROM event_fixture_workers
        WHERE event_fixture_workers.worker_id = ?
          AND NOT EXISTS (
            SELECT 1 FROM worker_event_permissions permission
            WHERE permission.worker_id = event_fixture_workers.worker_id
              AND permission.event_id = event_fixture_workers.event_id
          )
          AND NOT EXISTS (
            SELECT 1
            FROM game_assignments assignment
            JOIN games game ON game.id = assignment.game_id
            WHERE game.tournament_id = event_fixture_workers.tournament_id
              AND assignment.worker_id = event_fixture_workers.worker_id
              AND assignment.status IN ('assigned', 'acked', 'live')
          )
        """,
        (worker_id,),
    )


def record_worker_failure(
    connection: sqlite3.Connection,
    *,
    worker: WorkerRecord,
    assignment_id: int,
    game_id: int,
    engine_id: int,
    engine_name: str,
    stage: str,
    error: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO worker_failures (
          worker_id, worker_label, machine_id, assignment_id, game_id,
          engine_id, engine_name, stage, error, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            worker.id,
            worker.label,
            worker.machine_id,
            assignment_id,
            game_id,
            engine_id,
            engine_name[:80],
            stage,
            error[:8000],
            utc_now(),
        ),
    )
    return cursor.lastrowid


def list_worker_failures(
    connection: sqlite3.Connection,
    worker_id: int,
    *,
    limit: int = 20,
) -> tuple[WorkerFailureRecord, ...]:
    rows = connection.execute(
        """
        SELECT * FROM worker_failures
        WHERE worker_id = ?
        ORDER BY occurred_at DESC, id DESC
        LIMIT ?
        """,
        (worker_id, max(1, min(limit, 100))),
    )
    return tuple(
        WorkerFailureRecord(
            id=row["id"],
            worker_id=row["worker_id"],
            worker_label=row["worker_label"],
            machine_id=row["machine_id"],
            assignment_id=row["assignment_id"],
            game_id=row["game_id"],
            engine_id=row["engine_id"],
            engine_name=row["engine_name"],
            stage=row["stage"],
            error=row["error"],
            occurred_at=row["occurred_at"],
        )
        for row in rows
    )


def record_worker_resource_sample(
    connection: sqlite3.Connection,
    worker_id: int,
    session_id: str,
    telemetry: WorkerResourceTelemetry,
) -> bool:
    sampled_at = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO worker_resource_samples (
          worker_id, sampled_at, cpu_percent, memory_used_mb, memory_total_mb,
          memory_available_mb, coordinator_cpu_cores, coordinator_memory_mb,
          engine_cpu_cores, engine_memory_mb, disk_used_mb, disk_free_mb,
          disk_total_mb
        )
        SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        WHERE EXISTS (
          SELECT 1 FROM workers
          WHERE id = ? AND session_id = ?
            AND status IN ('connected', 'downloading', 'ready', 'busy')
        )
        """,
        (
            worker_id,
            sampled_at,
            telemetry.cpu_percent,
            telemetry.memory_used_mb,
            telemetry.memory_total_mb,
            telemetry.memory_available_mb,
            telemetry.coordinator_cpu_cores,
            telemetry.coordinator_memory_mb,
            telemetry.engine_cpu_cores,
            telemetry.engine_memory_mb,
            telemetry.disk_used_mb,
            telemetry.disk_free_mb,
            telemetry.disk_total_mb,
            worker_id,
            session_id,
        ),
    )
    if cursor.rowcount != 1:
        return False
    retention_start = (utc_now_datetime() - timedelta(hours=1)).isoformat(
        timespec="seconds"
    )
    connection.execute(
        "DELETE FROM worker_resource_samples WHERE worker_id = ? AND sampled_at < ?",
        (worker_id, retention_start),
    )
    return True


def list_worker_resource_samples(
    connection: sqlite3.Connection,
    worker_id: int,
    *,
    limit: int = 120,
) -> tuple[WorkerResourceSampleRecord, ...]:
    rows = connection.execute(
        """
        SELECT * FROM worker_resource_samples
        WHERE worker_id = ?
        ORDER BY sampled_at DESC, id DESC
        LIMIT ?
        """,
        (worker_id, max(1, min(limit, 720))),
    ).fetchall()
    return tuple(
        WorkerResourceSampleRecord(
            id=row["id"],
            worker_id=row["worker_id"],
            sampled_at=row["sampled_at"],
            cpu_percent=float(row["cpu_percent"]),
            memory_used_mb=float(row["memory_used_mb"]),
            memory_total_mb=float(row["memory_total_mb"]),
            memory_available_mb=float(row["memory_available_mb"]),
            coordinator_cpu_cores=float(row["coordinator_cpu_cores"]),
            coordinator_memory_mb=float(row["coordinator_memory_mb"]),
            engine_cpu_cores=float(row["engine_cpu_cores"]),
            engine_memory_mb=float(row["engine_memory_mb"]),
            disk_used_mb=float(row["disk_used_mb"]),
            disk_free_mb=float(row["disk_free_mb"]),
            disk_total_mb=float(row["disk_total_mb"]),
        )
        for row in reversed(rows)
    )


def update_worker_label(
    connection: sqlite3.Connection,
    worker_id: int,
    label: str,
) -> None:
    connection.execute(
        "UPDATE workers SET label = ? WHERE id = ?",
        (label, worker_id),
    )


def update_worker_status(
    connection: sqlite3.Connection,
    worker_id: int,
    status: str,
    *,
    session_id: str | None = None,
) -> bool:
    cursor = connection.execute(
        """
        UPDATE workers
        SET status = ?, last_seen = ?
        WHERE id = ? AND status != 'revoked'
          AND (CAST(? AS TEXT) IS NULL OR session_id = ?)
        """,
        (status, utc_now(), worker_id, session_id, session_id),
    )
    return cursor.rowcount > 0


def touch_worker_seen(
    connection: sqlite3.Connection,
    worker_id: int,
    *,
    session_id: str | None = None,
) -> bool:
    cursor = connection.execute(
        """
        UPDATE workers
        SET last_seen = ?
        WHERE id = ?
          AND status IN ('connected', 'downloading', 'ready', 'busy')
          AND (CAST(? AS TEXT) IS NULL OR session_id = ?)
        """,
        (utc_now(), worker_id, session_id, session_id),
    )
    return cursor.rowcount > 0


def touch_workers_seen(
    connection: sqlite3.Connection,
    sessions: list[tuple[int, str]],
) -> set[int]:
    """Persist many live worker sessions in one database transaction."""
    if not sessions:
        return set()
    now = utc_now()
    values = ", ".join("(?, ?)" for _ in sessions)
    parameters: list[Any] = [now]
    for worker_id, session_id in sessions:
        parameters.extend((worker_id, session_id))
    rows = connection.execute(
        f"""
        UPDATE workers AS worker
        SET last_seen = ?
        FROM (VALUES {values}) AS live(worker_id, session_id)
        WHERE worker.id = live.worker_id
          AND worker.session_id = live.session_id
          AND worker.status IN ('connected', 'downloading', 'ready', 'busy')
        RETURNING worker.id
        """,
        parameters,
    ).fetchall()
    return {int(row["id"]) for row in rows}


def disconnect_worker(
    connection: sqlite3.Connection,
    worker_id: int,
    *,
    session_id: str | None = None,
    reason: str = "worker connection lost",
) -> tuple[int, ...]:
    row = connection.execute(
        "SELECT status, session_id FROM workers WHERE id = ?",
        (worker_id,),
    ).fetchone()
    if row is None or row["status"] == "revoked":
        return ()
    if session_id is not None and row["session_id"] != session_id:
        return ()

    now = utc_now()
    tournament_ids = _active_worker_tournament_ids(connection, worker_id)
    _release_worker_active_assignments(
        connection,
        worker_id,
        now=now,
        reason=reason,
    )
    connection.execute(
        "DELETE FROM event_fixture_workers WHERE worker_id = ?",
        (worker_id,),
    )
    connection.execute(
        """
        UPDATE workers
        SET status = 'offline', last_seen = ?
        WHERE id = ? AND status != 'revoked'
          AND (CAST(? AS TEXT) IS NULL OR session_id = ?)
        """,
        (now, worker_id, session_id, session_id),
    )
    return tournament_ids


def revoke_worker(connection: sqlite3.Connection, worker_id: int) -> None:
    """Decommission a worker and remove its credentials and worker record."""
    now = utc_now()
    _release_worker_active_assignments(
        connection,
        worker_id,
        now=now,
        reason="worker revoked",
    )
    connection.execute("DELETE FROM workers WHERE id = ?", (worker_id,))


def _active_worker_tournament_ids(
    connection: sqlite3.Connection,
    worker_id: int,
) -> tuple[int, ...]:
    return tuple(
        int(row["tournament_id"])
        for row in connection.execute(
            """
            SELECT DISTINCT games.tournament_id
            FROM game_assignments
            JOIN games ON games.id = game_assignments.game_id
            WHERE game_assignments.worker_id = ?
              AND game_assignments.status IN ('assigned', 'acked', 'live')
              AND games.status IN ('assigned', 'live')
            """,
            (worker_id,),
        )
    )


def _release_worker_active_assignments(
    connection: sqlite3.Connection,
    worker_id: int,
    *,
    now: str,
    reason: str,
) -> None:
    game_ids = _active_worker_game_ids(connection, worker_id)
    connection.execute(
        """
        UPDATE game_assignments
        SET status = 'abandoned',
            finished_at = ?,
            last_error = ?
        WHERE worker_id = ?
          AND status IN ('assigned', 'acked', 'live')
        """,
        (now, reason[:500], worker_id),
    )
    _reset_games_for_replay(connection, game_ids, reason=reason, occurred_at=now)


def _active_worker_game_ids(
    connection: sqlite3.Connection,
    worker_id: int,
) -> tuple[int, ...]:
    return tuple(
        int(row["game_id"])
        for row in connection.execute(
            """
            SELECT game_id
            FROM game_assignments
            WHERE worker_id = ?
              AND status IN ('assigned', 'acked', 'live')
            """,
            (worker_id,),
        )
    )


def _reset_games_for_replay(
    connection: sqlite3.Connection,
    game_ids: Iterable[int],
    *,
    include_abandoned: bool = False,
    reason: str = "game reset for replay",
    occurred_at: str | None = None,
) -> None:
    """Discard every attempt-scoped artifact before games are reassigned."""
    ids = tuple(dict.fromkeys(int(game_id) for game_id in game_ids))
    if not ids:
        return
    placeholders = ", ".join("?" for _ in ids)
    connection.execute(
        f"""
        INSERT INTO game_assignment_progress (
          assignment_id, assignment_key, game_id, source,
          stage, stage_label, stage_order, substage, status, detail,
          engine_id, engine_name, current_value, total_value, metadata, occurred_at
        )
        SELECT assignment.id,
               assignment.assignment_key,
               assignment.game_id,
               'server',
               'assignment',
               'Assignment',
               0,
               'attempt_replayed',
               'failed',
               LEFT(?, 4000),
               NULL,
               NULL,
               attempt.last_ply,
               NULL,
               json_build_object(
                 'worker_id', assignment.worker_id,
                 'worker_label', worker.label,
                 'machine_id', worker.machine_id,
                 'worker_session_id', worker.session_id,
                 'worker_app_commit', worker.app_commit,
                 'assignment_status', assignment.status,
                 'sent_at', assignment.sent_at,
                 'acked_at', assignment.acked_at,
                 'finished_at', assignment.finished_at,
                 'last_error', assignment.last_error,
                 'game_started_at', game.started_at,
                 'move_count', attempt.move_count,
                 'last_ply', attempt.last_ply
               )::text,
               ?
        FROM game_assignments AS assignment
        JOIN games AS game ON game.id = assignment.game_id
        LEFT JOIN workers AS worker ON worker.id = assignment.worker_id
        LEFT JOIN LATERAL (
          SELECT COUNT(*) AS move_count, MAX(ply) AS last_ply
          FROM moves
          WHERE moves.game_id = assignment.game_id
        ) AS attempt ON TRUE
        WHERE assignment.game_id IN ({placeholders})
          AND assignment.status IN ('assigned', 'acked', 'live', 'abandoned', 'expired')
          AND NOT EXISTS (
            SELECT 1
            FROM game_assignment_progress AS progress
            WHERE progress.assignment_id = assignment.id
              AND progress.assignment_key = assignment.assignment_key
              AND progress.substage = 'attempt_replayed'
          )
        """,
        (reason[:4000], occurred_at or utc_now(), *ids),
    )
    connection.execute(f"DELETE FROM moves WHERE game_id IN ({placeholders})", ids)
    connection.execute(
        f"DELETE FROM game_pause_checkpoints WHERE game_id IN ({placeholders})",
        ids,
    )
    connection.execute(
        f"DELETE FROM game_hardware_scores WHERE game_id IN ({placeholders})",
        ids,
    )
    resettable_statuses = (
        "'pending', 'assigned', 'live', 'abandoned'"
        if include_abandoned
        else "'pending', 'assigned', 'live'"
    )
    connection.execute(
        f"""
        UPDATE games
        SET status = 'pending',
            result = NULL,
            termination = NULL,
            pgn = NULL,
            white_hw = NULL,
            black_hw = NULL,
            started_at = NULL,
            finished_at = NULL
        WHERE id IN ({placeholders})
          AND status IN ({resettable_statuses})
        """,
        ids,
    )


def delete_worker(connection: sqlite3.Connection, worker_id: int) -> None:
    """Delete a worker and return its active assignments to the pending pool."""
    game_ids = _active_worker_game_ids(connection, worker_id)
    now = utc_now()
    connection.execute(
        """
        UPDATE game_assignments
        SET status = CASE
              WHEN status IN ('assigned', 'acked', 'live') THEN 'expired'
              ELSE status
            END,
            finished_at = CASE
              WHEN status IN ('assigned', 'acked', 'live') THEN ?
              ELSE finished_at
            END,
            last_error = CASE
              WHEN status IN ('assigned', 'acked', 'live') THEN 'worker deleted'
              ELSE last_error
            END
        WHERE worker_id = ?
        """,
        (now, worker_id),
    )
    _reset_games_for_replay(
        connection,
        game_ids,
        reason="worker deleted",
        occurred_at=now,
    )
    connection.execute("DELETE FROM workers WHERE id = ?", (worker_id,))


def create_opening_suite(
    connection: sqlite3.Connection,
    *,
    name: str,
    description: str = "",
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO opening_suites (name, description, created_at)
        VALUES (?, ?, ?)
        """,
        (name, description, utc_now()),
    )
    return int(cursor.lastrowid)


def update_opening_suite(
    connection: sqlite3.Connection,
    suite_id: int,
    *,
    name: str,
    description: str = "",
) -> None:
    connection.execute(
        "UPDATE opening_suites SET name = ?, description = ? WHERE id = ?",
        (name, description, suite_id),
    )


def delete_opening_suite(connection: sqlite3.Connection, suite_id: int) -> None:
    connection.execute("DELETE FROM opening_suites WHERE id = ?", (suite_id,))


def get_opening_suite(
    connection: sqlite3.Connection,
    suite_id: int,
) -> OpeningSuiteRecord | None:
    row = connection.execute(
        "SELECT * FROM opening_suites WHERE id = ?",
        (suite_id,),
    ).fetchone()
    if row is None:
        return None
    return _opening_suite_from_row(row)


def list_opening_suites(connection: sqlite3.Connection) -> tuple[OpeningSuiteRecord, ...]:
    return tuple(
        _opening_suite_from_row(row)
        for row in connection.execute("SELECT * FROM opening_suites ORDER BY name")
    )


def replace_suite_openings(
    connection: sqlite3.Connection,
    suite_id: int,
    openings: list[OpeningLine],
) -> int:
    connection.execute("DELETE FROM openings WHERE suite_id = ?", (suite_id,))
    _insert_opening_rows(
        connection,
        (
            (
                suite_id,
                position,
                opening.name,
                opening.start_fen,
                json.dumps(opening.moves),
                opening.fen,
            )
            for position, opening in enumerate(openings, start=1)
        ),
    )
    return len(openings)


def append_suite_openings(
    connection: sqlite3.Connection,
    suite_id: int,
    openings: list[OpeningLine],
) -> int:
    if not openings:
        return 0
    copy_rows = getattr(connection, "copy_rows", None)
    if copy_rows is None:
        return _append_suite_openings_in_memory(connection, suite_id, openings)
    connection.execute("DROP TABLE IF EXISTS cope_pending_openings")
    connection.execute(
        """
        CREATE TEMP TABLE cope_pending_openings (
          input_order BIGINT NOT NULL,
          name TEXT NOT NULL,
          start_fen TEXT NOT NULL,
          moves TEXT NOT NULL,
          fen TEXT NOT NULL
        ) ON COMMIT DROP
        """
    )
    copy_rows(
        """
        COPY cope_pending_openings (input_order, name, start_fen, moves, fen)
        FROM STDIN
        """,
        (
            (
                input_order,
                opening.name,
                opening.start_fen,
                json.dumps(opening.moves),
                opening.fen,
            )
            for input_order, opening in enumerate(openings, start=1)
        ),
    )
    connection.execute(
        "CREATE INDEX ON cope_pending_openings (start_fen, moves)"
    )
    connection.execute("ANALYZE cope_pending_openings")
    connection.execute(
        """
        DELETE FROM cope_pending_openings pending
        USING openings existing
        WHERE existing.suite_id = ?
          AND existing.start_fen = pending.start_fen
          AND existing.moves = pending.moves
        """,
        (suite_id,),
    )
    row = connection.execute(
        "SELECT COALESCE(MAX(position), 0) AS position FROM openings WHERE suite_id = ?",
        (suite_id,),
    ).fetchone()
    cursor = connection.execute(
        """
        WITH unique_pending AS (
          SELECT DISTINCT ON (start_fen, moves)
                 input_order, name, start_fen, moves, fen
          FROM cope_pending_openings
          ORDER BY start_fen, moves, input_order
        ), numbered AS (
          SELECT ? + ROW_NUMBER() OVER (ORDER BY input_order) AS position,
                 name, start_fen, moves, fen
          FROM unique_pending
        )
        INSERT INTO openings (suite_id, position, name, start_fen, moves, fen)
        SELECT ?, position, name, start_fen, moves, fen
        FROM numbered
        ORDER BY position
        """,
        (int(row["position"]), suite_id),
    )
    return cursor.rowcount


def _append_suite_openings_in_memory(
    connection: sqlite3.Connection,
    suite_id: int,
    openings: list[OpeningLine],
) -> int:
    rows = connection.execute(
        "SELECT start_fen, moves FROM openings WHERE suite_id = ?",
        (suite_id,),
    )
    seen = {(row["start_fen"], row["moves"]) for row in rows}
    pending: list[tuple[OpeningLine, str]] = []
    for opening in openings:
        moves = json.dumps(opening.moves)
        key = (opening.start_fen, moves)
        if key in seen:
            continue
        seen.add(key)
        pending.append((opening, moves))
    if not pending:
        return 0
    row = connection.execute(
        "SELECT COALESCE(MAX(position), 0) AS position FROM openings WHERE suite_id = ?",
        (suite_id,),
    ).fetchone()
    first_position = int(row["position"]) + 1
    _insert_opening_rows(
        connection,
        (
            (
                suite_id,
                position,
                opening.name,
                opening.start_fen,
                moves,
                opening.fen,
            )
            for position, (opening, moves) in enumerate(
                pending,
                start=first_position,
            )
        ),
    )
    return len(pending)


def _insert_opening_rows(
    connection: sqlite3.Connection,
    rows: Iterable[Iterable[Any]],
) -> None:
    copy_rows = getattr(connection, "copy_rows", None)
    if copy_rows is not None:
        copy_rows(
            "COPY openings (suite_id, position, name, start_fen, moves, fen) FROM STDIN",
            rows,
        )
        return
    connection.executemany(
        """
        INSERT INTO openings (suite_id, position, name, start_fen, moves, fen)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def list_suite_openings(
    connection: sqlite3.Connection,
    suite_id: int,
) -> tuple[OpeningRecord, ...]:
    return tuple(
        _opening_from_row(row)
        for row in connection.execute(
            "SELECT * FROM openings WHERE suite_id = ? ORDER BY position",
            (suite_id,),
        )
    )


def suite_opening_count(connection: sqlite3.Connection, suite_id: int) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS count FROM openings WHERE suite_id = ?",
        (suite_id,),
    ).fetchone()
    return int(row["count"])


def list_suite_opening_ids(
    connection: sqlite3.Connection,
    suite_id: int,
    *,
    limit: int,
    start_position: int,
) -> tuple[int, ...]:
    if limit <= 0:
        return ()
    rows = connection.execute(
        """
        SELECT id FROM openings
        WHERE suite_id = ? AND position >= ?
        ORDER BY position
        LIMIT ?
        """,
        (suite_id, start_position, limit),
    ).fetchall()
    opening_ids = [int(row["id"]) for row in rows]
    remaining = limit - len(opening_ids)
    if remaining > 0:
        rows = connection.execute(
            """
            SELECT id FROM openings
            WHERE suite_id = ? AND position < ?
            ORDER BY position
            LIMIT ?
            """,
            (suite_id, start_position, remaining),
        ).fetchall()
        opening_ids.extend(int(row["id"]) for row in rows)
    return tuple(opening_ids)


def create_chat_message(
    connection: sqlite3.Connection,
    *,
    tournament_id: int | None = None,
    event_id: int | None = None,
    display_name: str,
    text: str,
) -> int:
    if (tournament_id is None) == (event_id is None):
        raise ValueError("chat message requires exactly one subject")
    cursor = connection.execute(
        """
        INSERT INTO chat_messages (tournament_id, event_id, display_name, text, at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (tournament_id, event_id, display_name, text, utc_now()),
    )
    return int(cursor.lastrowid)


def get_chat_message(
    connection: sqlite3.Connection,
    message_id: int,
) -> ChatMessageRecord | None:
    row = connection.execute(
        "SELECT * FROM chat_messages WHERE id = ?",
        (message_id,),
    ).fetchone()
    return None if row is None else _chat_message_from_row(row)


def list_chat_messages(
    connection: sqlite3.Connection,
    *,
    limit: int | None = 50,
    tournament_id: int | None = None,
    event_id: int | None = None,
    system: bool | None = None,
) -> tuple[ChatMessageRecord, ...]:
    if tournament_id is not None and event_id is not None:
        raise ValueError("chat messages can only be filtered by one subject")
    parameters: list[int] = []
    if tournament_id is not None:
        query = "SELECT * FROM chat_messages WHERE tournament_id = ?"
        parameters.append(tournament_id)
    elif event_id is not None:
        query = "SELECT * FROM chat_messages WHERE event_id = ?"
        parameters.append(event_id)
    else:
        query = "SELECT * FROM chat_messages"
    if system is not None:
        query += " AND" if parameters else " WHERE"
        query += " display_name = 'System'" if system else " display_name <> 'System'"
    query += " ORDER BY id DESC"
    if limit is not None:
        query += " LIMIT ?"
        parameters.append(limit)
    rows = connection.execute(query, tuple(parameters))
    return tuple(
        _chat_message_from_row(row)
        for row in rows
    )


def get_chat_settings(connection: sqlite3.Connection) -> ChatSettingsRecord:
    values = {
        row["key"]: row["value"]
        for row in connection.execute("SELECT key, value FROM chat_settings")
    }
    return ChatSettingsRecord(
        enabled=_bool_setting(values.get("enabled"), default=True),
        slowmode_seconds=_int_setting(values.get("slowmode_seconds"), default=0),
        max_message_length=_int_setting(values.get("max_message_length"), default=300),
        allow_anonymous_names=_bool_setting(
            values.get("allow_anonymous_names"), default=True
        ),
        retention_days=_int_setting(values.get("retention_days"), default=30),
    )


def update_chat_settings(
    connection: sqlite3.Connection,
    settings: ChatSettingsRecord,
) -> None:
    values = {
        "enabled": str(settings.enabled).lower(),
        "slowmode_seconds": str(settings.slowmode_seconds),
        "max_message_length": str(settings.max_message_length),
        "allow_anonymous_names": str(settings.allow_anonymous_names).lower(),
        "retention_days": str(settings.retention_days),
    }
    connection.executemany(
        """
        INSERT INTO chat_settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        values.items(),
    )


def delete_chat_message(
    connection: sqlite3.Connection,
    message_id: int,
) -> ChatMessageRecord | None:
    row = connection.execute(
        "SELECT * FROM chat_messages WHERE id = ?",
        (message_id,),
    ).fetchone()
    if row is None:
        return None
    connection.execute("DELETE FROM chat_messages WHERE id = ?", (message_id,))
    return _chat_message_from_row(row)


def create_deployment_job(
    connection: sqlite3.Connection,
    *,
    requested_ref: str,
    scope: str = "platform",
) -> int:
    if scope not in {"platform", "web"}:
        raise ValueError("unsupported deployment scope")
    dockerfile_pull = connection.execute(
        "SELECT id FROM dockerfile_pull_jobs WHERE status NOT IN ('succeeded', 'failed') LIMIT 1"
    ).fetchone()
    if dockerfile_pull is not None:
        raise ValueError(f"Dockerfile pull {dockerfile_pull['id']} is already in progress")
    active = connection.execute(
        """
        SELECT id FROM deployment_jobs
        WHERE status NOT IN ('succeeded', 'failed')
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    if active is not None:
        raise ValueError(f"deployment {active['id']} is already in progress")
    now = utc_now()
    try:
        cursor = connection.execute(
            """
            INSERT INTO deployment_jobs (requested_ref, scope, status, requested_at)
            VALUES (?, ?, 'pending', ?)
            """,
            (requested_ref, scope, now),
        )
    except sqlite3.IntegrityError as error:
        raise ValueError("a deployment is already in progress") from error
    job_id = int(cursor.lastrowid)
    server_label = "Web application" if scope == "web" else "Server platform"
    connection.execute(
        """
        INSERT INTO deployment_targets (
          job_id, target_kind, target_id, label, current_commit, status, updated_at
        )
        VALUES (?, 'server', NULL, ?, NULL, 'pending', ?)
        """,
        (job_id, server_label, now),
    )
    if scope == "platform":
        connection.execute(
            """
            INSERT INTO deployment_targets (
              job_id, target_kind, target_id, label, current_commit, status, updated_at
            )
            SELECT ?, 'worker', id, label, app_commit, 'pending', ?
            FROM workers
            WHERE status != 'revoked'
            ORDER BY id
            """,
            (job_id, now),
        )
        connection.execute(
            """
            INSERT INTO deployment_targets (
              job_id, target_kind, target_id, label, current_commit, status, updated_at
            )
            SELECT ?, 'benchmarker', id, label, app_commit, 'pending', ?
            FROM benchmarkers
            WHERE status != 'revoked'
            ORDER BY id
            """,
            (job_id, now),
        )
    return job_id


def create_dockerfile_pull_job(
    connection: sqlite3.Connection,
    *,
    requested_ref: str,
) -> int:
    deployment = connection.execute(
        "SELECT id FROM deployment_jobs WHERE status NOT IN ('succeeded', 'failed') LIMIT 1"
    ).fetchone()
    if deployment is not None:
        raise ValueError(f"deployment {deployment['id']} is already in progress")
    active = connection.execute(
        "SELECT id FROM dockerfile_pull_jobs WHERE status NOT IN ('succeeded', 'failed') LIMIT 1"
    ).fetchone()
    if active is not None:
        raise ValueError(f"Dockerfile pull {active['id']} is already in progress")
    cursor = connection.execute(
        "INSERT INTO dockerfile_pull_jobs (requested_ref, requested_at) VALUES (?, ?)",
        (requested_ref, utc_now()),
    )
    return int(cursor.lastrowid)


def get_dockerfile_pull_job(
    connection: sqlite3.Connection,
    job_id: int,
) -> DockerfilePullJobRecord | None:
    row = connection.execute(
        "SELECT * FROM dockerfile_pull_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    return None if row is None else _dockerfile_pull_job_from_row(row)


def latest_dockerfile_pull_job(
    connection: sqlite3.Connection,
) -> DockerfilePullJobRecord | None:
    row = connection.execute(
        "SELECT * FROM dockerfile_pull_jobs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return None if row is None else _dockerfile_pull_job_from_row(row)


def claim_dockerfile_pull_job(
    connection: sqlite3.Connection,
) -> DockerfilePullJobRecord | None:
    row = connection.execute(
        """
        WITH candidate AS (
          SELECT id FROM dockerfile_pull_jobs
          WHERE status = 'pending'
          ORDER BY id
          FOR UPDATE SKIP LOCKED
          LIMIT 1
        )
        UPDATE dockerfile_pull_jobs
        SET status = 'resolving', started_at = COALESCE(started_at, ?)
        WHERE id = (SELECT id FROM candidate)
        RETURNING *
        """,
        (utc_now(),),
    ).fetchone()
    return None if row is None else _dockerfile_pull_job_from_row(row)


def update_dockerfile_pull_job(
    connection: sqlite3.Connection,
    job_id: int,
    status: str,
    *,
    target_commit: str | None = None,
    files_updated: int = 0,
    error: str | None = None,
) -> None:
    terminal = status in {"succeeded", "failed"}
    connection.execute(
        """
        UPDATE dockerfile_pull_jobs
        SET status = ?, target_commit = COALESCE(?, target_commit),
            files_updated = ?, error = ?,
            finished_at = CASE WHEN ? THEN ? ELSE NULL END
        WHERE id = ?
        """,
        (status, target_commit, files_updated, error, terminal, utc_now(), job_id),
    )


def get_deployment_job(
    connection: sqlite3.Connection,
    job_id: int,
) -> DeploymentJobRecord | None:
    row = connection.execute(
        "SELECT * FROM deployment_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    return None if row is None else _deployment_job_from_row(row)


def list_deployment_jobs(
    connection: sqlite3.Connection,
    *,
    limit: int = 20,
) -> tuple[DeploymentJobRecord, ...]:
    rows = connection.execute(
        "SELECT * FROM deployment_jobs ORDER BY id DESC LIMIT ?",
        (max(1, min(limit, 100)),),
    )
    return tuple(_deployment_job_from_row(row) for row in rows)


def list_deployment_targets(
    connection: sqlite3.Connection,
    job_id: int,
) -> tuple[DeploymentTargetRecord, ...]:
    rows = connection.execute(
        """
        SELECT * FROM deployment_targets
        WHERE job_id = ?
        ORDER BY CASE target_kind WHEN 'server' THEN 0 ELSE 1 END, id
        """,
        (job_id,),
    )
    return tuple(_deployment_target_from_row(row) for row in rows)


def claim_deployment_job(
    connection: sqlite3.Connection,
) -> DeploymentJobRecord | None:
    row = connection.execute(
        """
        WITH candidate AS (
          SELECT id
          FROM deployment_jobs
          WHERE status = 'pending'
          ORDER BY id
          FOR UPDATE SKIP LOCKED
          LIMIT 1
        )
        UPDATE deployment_jobs
        SET status = 'resolving', started_at = COALESCE(started_at, ?),
            finished_at = NULL, error = NULL
        WHERE id = (SELECT id FROM candidate)
        RETURNING *
        """,
        (utc_now(),),
    ).fetchone()
    return None if row is None else _deployment_job_from_row(row)


def fail_interrupted_deployment_jobs(
    connection: sqlite3.Connection,
) -> tuple[int, ...]:
    rows = connection.execute(
        """
        SELECT id
        FROM deployment_jobs
        WHERE status NOT IN ('pending', 'succeeded', 'failed')
        ORDER BY id
        """
    ).fetchall()
    job_ids = tuple(int(row["id"]) for row in rows)
    if not job_ids:
        return ()
    now = utc_now()
    for job_id in job_ids:
        connection.execute(
            """
            UPDATE deployment_jobs
            SET status = 'failed', finished_at = ?,
                error = 'Updater restarted before the deployment completed.'
            WHERE id = ?
            """,
            (now, job_id),
        )
        connection.execute(
            """
            UPDATE deployment_targets
            SET status = CASE WHEN status = 'succeeded' THEN status ELSE 'failed' END,
                detail = CASE
                  WHEN status = 'succeeded' THEN detail
                  ELSE 'Updater restarted before the deployment completed.'
                END,
                updated_at = ?
            WHERE job_id = ?
            """,
            (now, job_id),
        )
    return job_ids


def fail_interrupted_dockerfile_pull_jobs(
    connection: sqlite3.Connection,
) -> tuple[int, ...]:
    rows = connection.execute(
        "SELECT id FROM dockerfile_pull_jobs WHERE status NOT IN ('pending', 'succeeded', 'failed') ORDER BY id"
    ).fetchall()
    job_ids = tuple(int(row["id"]) for row in rows)
    if job_ids:
        connection.execute(
            """
            UPDATE dockerfile_pull_jobs
            SET status = 'failed', finished_at = ?, error = ?
            WHERE status NOT IN ('pending', 'succeeded', 'failed')
            """,
            (utc_now(), "Updater restarted before the Dockerfile pull completed."),
        )
    return job_ids


def set_deployment_target_commit(
    connection: sqlite3.Connection,
    job_id: int,
    target_commit: str,
    repository_url: str,
) -> None:
    now = utc_now()
    connection.execute(
        """
        UPDATE deployment_jobs
        SET target_commit = ?
        WHERE id = ? AND status NOT IN ('succeeded', 'failed')
        """,
        (target_commit, job_id),
    )
    connection.execute(
        """
        UPDATE deployment_targets
        SET target_commit = ?, repository_url = ?,
            status = CASE WHEN target_kind = 'benchmarker' THEN 'pending' ELSE 'waiting' END,
            detail = '', updated_at = ?
        WHERE job_id = ? AND status = 'pending'
        """,
        (target_commit, repository_url, now, job_id),
    )


def activate_benchmarker_deployment_targets(
    connection: sqlite3.Connection,
    job_id: int,
) -> None:
    connection.execute(
        """
        UPDATE deployment_targets
        SET status = 'waiting', detail = '', updated_at = ?
        WHERE job_id = ? AND target_kind = 'benchmarker' AND status = 'pending'
        """,
        (utc_now(), job_id),
    )


def update_deployment_job_status(
    connection: sqlite3.Connection,
    job_id: int,
    status: str,
    *,
    error: str | None = None,
) -> None:
    terminal = status in {"succeeded", "failed"}
    connection.execute(
        """
        UPDATE deployment_jobs
        SET status = ?, error = ?,
            finished_at = CASE WHEN ? THEN ? ELSE NULL END
        WHERE id = ?
        """,
        (status, error, terminal, utc_now(), job_id),
    )


def update_deployment_target_status(
    connection: sqlite3.Connection,
    target_id: int,
    status: str,
    *,
    current_commit: str | None = None,
    detail: str = "",
) -> None:
    connection.execute(
        """
        UPDATE deployment_targets
        SET status = ?, current_commit = COALESCE(?, current_commit),
            detail = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, current_commit, detail[:4000], utc_now(), target_id),
    )


def update_server_deployment_target(
    connection: sqlite3.Connection,
    job_id: int,
    status: str,
    *,
    current_commit: str | None = None,
    detail: str = "",
) -> None:
    connection.execute(
        """
        UPDATE deployment_targets
        SET status = ?, current_commit = COALESCE(?, current_commit),
            detail = ?, updated_at = ?
        WHERE job_id = ? AND target_kind = 'server'
        """,
        (status, current_commit, detail[:4000], utc_now(), job_id),
    )


def _client_deployment_target(
    connection: sqlite3.Connection,
    target_kind: str,
    target_id: int,
) -> DeploymentTargetRecord | None:
    row = connection.execute(
        """
        SELECT target.*
        FROM deployment_targets AS target
        JOIN deployment_jobs AS job ON job.id = target.job_id
        WHERE target.target_kind = ?
          AND target.target_id = ?
          AND target.target_commit IS NOT NULL
          AND target.status IN ('waiting', 'updating', 'restarting', 'deferred', 'failed')
          AND job.status != 'failed'
        ORDER BY target.job_id DESC
        LIMIT 1
        """,
        (target_kind, target_id),
    ).fetchone()
    return None if row is None else _deployment_target_from_row(row)


def worker_deployment_target(
    connection: sqlite3.Connection,
    worker_id: int,
) -> DeploymentTargetRecord | None:
    return _client_deployment_target(connection, "worker", worker_id)


def benchmarker_deployment_target(
    connection: sqlite3.Connection,
    benchmarker_id: int,
) -> DeploymentTargetRecord | None:
    return _client_deployment_target(connection, "benchmarker", benchmarker_id)


def _reconcile_client_deployment(
    connection: sqlite3.Connection,
    target_kind: str,
    target_id: int,
    app_commit: str,
) -> DeploymentTargetRecord | None:
    target = _client_deployment_target(connection, target_kind, target_id)
    if target is None:
        return None
    if target.target_commit == app_commit:
        update_deployment_target_status(
            connection,
            target.id,
            "succeeded",
            current_commit=app_commit,
        )
        return None
    if target.status in {"failed", "deferred"}:
        update_deployment_target_status(
            connection,
            target.id,
            "waiting",
            current_commit=app_commit,
        )
        return _client_deployment_target(connection, target_kind, target_id)
    if target.current_commit != app_commit:
        update_deployment_target_status(
            connection,
            target.id,
            target.status,
            current_commit=app_commit,
            detail=target.detail,
        )
        return _client_deployment_target(connection, target_kind, target_id)
    return target


def reconcile_worker_deployment(
    connection: sqlite3.Connection,
    worker_id: int,
    app_commit: str,
) -> DeploymentTargetRecord | None:
    target = worker_deployment_target(connection, worker_id)
    if target is not None and target.target_commit == app_commit:
        connection.execute(
            "DELETE FROM worker_failures WHERE worker_id = ?",
            (worker_id,),
        )
    return _reconcile_client_deployment(connection, "worker", worker_id, app_commit)


def reconcile_benchmarker_deployment(
    connection: sqlite3.Connection,
    benchmarker_id: int,
    app_commit: str,
) -> DeploymentTargetRecord | None:
    return _reconcile_client_deployment(
        connection,
        "benchmarker",
        benchmarker_id,
        app_commit,
    )


def _deployment_job_from_row(row: sqlite3.Row) -> DeploymentJobRecord:
    return DeploymentJobRecord(
        id=row["id"],
        requested_ref=row["requested_ref"],
        scope=row["scope"],
        target_commit=row["target_commit"],
        status=row["status"],
        requested_at=row["requested_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error=row["error"],
    )


def _deployment_target_from_row(row: sqlite3.Row) -> DeploymentTargetRecord:
    return DeploymentTargetRecord(
        id=row["id"],
        job_id=row["job_id"],
        target_kind=row["target_kind"],
        target_id=row["target_id"],
        label=row["label"],
        repository_url=row["repository_url"],
        target_commit=row["target_commit"],
        current_commit=row["current_commit"],
        status=row["status"],
        detail=row["detail"],
        updated_at=row["updated_at"],
    )


def enqueue_runner_command(
    connection: sqlite3.Connection,
    command: str,
    payload: dict[str, Any] | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO runner_commands (command, payload, created_at)
        VALUES (?, ?, ?)
        """,
        (command, _json_dump(payload or {}), utc_now()),
    )
    return int(cursor.lastrowid)


def claim_next_runner_command(
    connection: sqlite3.Connection,
) -> RunnerCommandRecord | None:
    while True:
        row = connection.execute(
            """
            SELECT * FROM runner_commands
            WHERE status = 'pending'
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None

        claimed_at = utc_now()
        cursor = connection.execute(
            """
            UPDATE runner_commands
            SET status = 'claimed', claimed_at = ?, finished_at = NULL, error = NULL
            WHERE id = ? AND status = 'pending'
            """,
            (claimed_at, row["id"]),
        )
        if cursor.rowcount == 0:
            continue

        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            payload = {"_invalid_payload": row["payload"]}
        if not isinstance(payload, dict):
            payload = {"_invalid_payload": row["payload"]}

        return RunnerCommandRecord(
            id=row["id"],
            command=row["command"],
            payload=payload,
            status="claimed",
            created_at=row["created_at"],
            claimed_at=claimed_at,
            finished_at=None,
            error=None,
        )


def finish_runner_command(connection: sqlite3.Connection, command_id: int) -> None:
    connection.execute(
        """
        UPDATE runner_commands
        SET status = 'applied', finished_at = ?, error = NULL
        WHERE id = ? AND status = 'claimed'
        """,
        (utc_now(), command_id),
    )


def fail_runner_command(
    connection: sqlite3.Connection,
    command_id: int,
    error: str,
) -> None:
    connection.execute(
        """
        UPDATE runner_commands
        SET status = 'failed', finished_at = ?, error = ?
        WHERE id = ? AND status = 'claimed'
        """,
        (utc_now(), error, command_id),
    )


def request_tournament_rating_commit(
    connection: sqlite3.Connection,
    tournament: TournamentRecord,
    rating_list_ids: Iterable[int] | None = None,
) -> int:
    if tournament.status not in {"finished", "aborted"}:
        raise ValueError("tournament is not finished or aborted")
    if tournament.status == "aborted":
        finished_game = connection.execute(
            """
            SELECT 1 FROM games
            WHERE tournament_id = ? AND status = 'finished' AND result IS NOT NULL
            LIMIT 1
            """,
            (tournament.id,),
        ).fetchone()
        if finished_game is None:
            raise ValueError("aborted tournament has no finished games")
    if not tournament.config.rated:
        raise ValueError("unrated tournament results cannot be committed")
    if connection.execute(
        "SELECT 1 FROM engine_relay_fixtures WHERE tournament_id = ?",
        (tournament.id,),
    ).fetchone() is not None:
        raise ValueError("engine relay tournaments are never eligible for ratings")
    if rating_list_ids is None:
        rating_list_ids = (item.id for item in list_rating_lists(connection))
    selected = tuple(sorted(set(int(value) for value in rating_list_ids)))
    if not selected or any(value <= 0 for value in selected):
        raise ValueError("choose at least one rating list")
    placeholders = ", ".join("?" for _ in selected)
    found = {
        int(row["id"])
        for row in connection.execute(
            f"SELECT id FROM rating_lists WHERE id IN ({placeholders})", selected
        )
    }
    if found != set(selected):
        raise ValueError("one or more rating lists do not exist")

    requested = 0
    now = utc_now()
    for rating_list_id in selected:
        existing = connection.execute(
            "SELECT * FROM tournament_rating_list_commits WHERE tournament_id = ? AND rating_list_id = ?",
            (tournament.id, rating_list_id),
        ).fetchone()
        if existing is not None and existing["status"] in {"claimed", "applied"}:
            continue
        if existing is not None and existing["status"] == "pending" and existing["command_id"] is not None:
            command = connection.execute(
                "SELECT status FROM runner_commands WHERE id = ?", (existing["command_id"],)
            ).fetchone()
            if command is not None and command["status"] in {"pending", "claimed"}:
                continue
        command_id = enqueue_runner_command(
            connection,
            "commit_tournament_results",
            {"tournament_id": tournament.id, "rating_list_id": rating_list_id},
        )
        connection.execute(
            """
            INSERT INTO tournament_rating_list_commits (
              tournament_id, rating_list_id, command_id, status, requested_at
            ) VALUES (?, ?, ?, 'pending', ?)
            ON CONFLICT(tournament_id, rating_list_id) DO UPDATE SET
              status = 'pending', command_id = excluded.command_id,
              requested_at = excluded.requested_at, applied_at = NULL, error = NULL
            """,
            (tournament.id, rating_list_id, command_id, now),
        )
        requested += 1
    return requested


def get_tournament_rating_commit(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> TournamentRatingCommitRecord | None:
    row = connection.execute(
        "SELECT * FROM tournament_rating_list_commits WHERE tournament_id = ? ORDER BY rating_list_id LIMIT 1",
        (tournament_id,),
    ).fetchone()
    if row is None:
        return None
    return _tournament_rating_commit_from_row(row)


def list_tournament_rating_commits(
    connection: sqlite3.Connection, tournament_id: int
) -> tuple[TournamentRatingCommitRecord, ...]:
    return tuple(
        _tournament_rating_commit_from_row(row)
        for row in connection.execute(
            "SELECT * FROM tournament_rating_list_commits WHERE tournament_id = ? ORDER BY rating_list_id",
            (tournament_id,),
        )
    )


def create_rating_list(connection: sqlite3.Connection, name: str) -> int:
    cursor = connection.execute(
        "INSERT INTO rating_lists (name, created_at) VALUES (?, ?)",
        (name.strip(), utc_now()),
    )
    return int(cursor.lastrowid)


def get_rating_list(connection: sqlite3.Connection, rating_list_id: int) -> RatingListRecord | None:
    row = connection.execute("SELECT * FROM rating_lists WHERE id = ?", (rating_list_id,)).fetchone()
    return None if row is None else _rating_list_from_row(row)


def list_rating_lists(connection: sqlite3.Connection) -> tuple[RatingListRecord, ...]:
    return tuple(
        _rating_list_from_row(row)
        for row in connection.execute("SELECT * FROM rating_lists ORDER BY name, id")
    )


def _dockerfile_pull_job_from_row(row: sqlite3.Row) -> DockerfilePullJobRecord:
    return DockerfilePullJobRecord(
        id=row["id"],
        requested_ref=row["requested_ref"],
        target_commit=row["target_commit"],
        status=row["status"],
        files_updated=row["files_updated"],
        requested_at=row["requested_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error=row["error"],
    )


def update_rating_list_anchor(
    connection: sqlite3.Connection,
    rating_list_id: int,
    engine_version_id: int,
    elo: float,
) -> None:
    if get_engine_version_record(connection, engine_version_id) is None:
        raise ValueError("engine version not found")
    cursor = connection.execute(
        "UPDATE rating_lists SET anchor_engine_id = ?, anchor_elo = ? WHERE id = ?",
        (engine_version_id, elo, rating_list_id),
    )
    if cursor.rowcount != 1:
        raise ValueError("rating list not found")


def delete_rating_list(connection: sqlite3.Connection, rating_list_id: int) -> None:
    connection.execute("DELETE FROM rating_lists WHERE id = ?", (rating_list_id,))


def _rating_list_from_row(row: sqlite3.Row) -> RatingListRecord:
    return RatingListRecord(
        id=row["id"],
        name=row["name"],
        anchor_engine_id=row["anchor_engine_id"],
        anchor_elo=float(row["anchor_elo"]),
        created_at=row["created_at"],
    )


def _engine_from_row(row: sqlite3.Row) -> EngineSpec:
    return EngineSpec(
        engine_id=row["id"],
        name=row["name"],
        author=row["author"],
        version=row["version"],
        repository_url=row["repository_url"],
        source_ref=row["source_ref"],
        dockerfile=row["dockerfile"],
        build_hash=row["build_hash"],
        artifact=_artifact_spec_from_row(row),
        uci_options=json.loads(row["uci_options"]),
    )


def _engine_record_from_row(row: sqlite3.Row) -> EngineRecord:
    return EngineRecord(
        id=row["id"],
        name=row["name"],
        author=row["author"],
        active=bool(row["active"]),
    )


def _engine_version_from_row(row: sqlite3.Row) -> EngineVersionRecord:
    benchmark_current = bool(row["benchmark_current"])
    engine_active = bool(row["engine_active"])
    return EngineVersionRecord(
        id=row["id"], engine_id=row["engine_id"], name=row["name"], author=row["author"],
        version=row["version"], git_host_id=row["git_host_id"],
        repository_url=row["repository_url"] or "",
        repository_full_name=row["repository_full_name"] or "",
        source_ref=row["source_ref"] or "",
        source_kind=row["source_kind"] or "commit",
        dockerfile_path=row["dockerfile_path"] or "",
        dockerfile=row["dockerfile"] or "",
        build_hash=row["build_hash"] or "",
        uci_options=json.loads(row["uci_options"]),
        artifact=_artifact_spec_from_row(row),
        active=engine_active and benchmark_current,
        benchmark_current=benchmark_current,
        engine_active=engine_active,
        created_at=row["created_at"],
    )


def _git_host_from_row(row: sqlite3.Row) -> GitHostRecord:
    return GitHostRecord(
        id=row["id"],
        name=row["name"],
        provider=row["provider"],
        base_url=row["base_url"],
        api_url=row["api_url"],
        access_token=row["access_token"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
    )


def _opening_suite_from_row(row: sqlite3.Row) -> OpeningSuiteRecord:
    return OpeningSuiteRecord(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        created_at=row["created_at"],
    )


def _opening_from_row(row: sqlite3.Row) -> OpeningRecord:
    return OpeningRecord(
        id=row["id"],
        suite_id=row["suite_id"],
        position=row["position"],
        name=row["name"],
        start_fen=row["start_fen"],
        moves=tuple(json.loads(row["moves"])),
        fen=row["fen"],
    )


def _chat_message_from_row(row: sqlite3.Row) -> ChatMessageRecord:
    return ChatMessageRecord(
        id=row["id"],
        tournament_id=row["tournament_id"],
        event_id=row["event_id"],
        display_name=row["display_name"],
        text=row["text"],
        at=row["at"],
    )


def _tournament_from_row(row: sqlite3.Row) -> TournamentRecord:
    config_data = json.loads(row["config"])
    config = TournamentConfig.model_validate(config_data)
    return TournamentRecord(
        id=row["id"],
        name=row["name"],
        config=config,
        status=row["status"],
        current_round=row["current_round"],
        worker_profile=row["worker_profile"],
        created_at=row["created_at"],
        scheduled_start_at=row["scheduled_start_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _tournament_rating_commit_from_row(row: sqlite3.Row) -> TournamentRatingCommitRecord:
    return TournamentRatingCommitRecord(
        tournament_id=row["tournament_id"],
        rating_list_id=row["rating_list_id"],
        command_id=row["command_id"],
        status=row["status"],
        requested_at=row["requested_at"],
        applied_at=row["applied_at"],
        error=row["error"],
    )


def _game_from_row(row: sqlite3.Row) -> GameRecord:
    return GameRecord(
        id=row["id"],
        tournament_id=row["tournament_id"],
        round=row["round"],
        pair_index=row["pair_index"],
        white_engine_id=row["white_engine_id"],
        black_engine_id=row["black_engine_id"],
        match_id=row["match_id"],
        game_number=row["game_number"],
        tiebreak_kind=row["tiebreak_kind"],
        opening_id=row["opening_id"],
        status=row["status"],
        result=row["result"],
        termination=row["termination"],
        pgn=row["pgn"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _tournament_match_from_row(row: sqlite3.Row) -> TournamentMatchRecord:
    return TournamentMatchRecord(
        id=row["id"],
        tournament_id=row["tournament_id"],
        round=row["round"],
        match_index=row["match_index"],
        engine1_id=row["engine1_id"],
        engine2_id=row["engine2_id"],
        status=row["status"],
        winner_engine_id=row["winner_engine_id"],
    )


def _move_from_row(row: sqlite3.Row) -> MoveRecord:
    return MoveRecord(
        game_id=row["game_id"],
        ply=row["ply"],
        uci=row["uci"],
        san=row["san"],
        is_book=bool(row["is_book"]),
        eval_cp=row["eval_cp"],
        eval_mate=row["eval_mate"],
        score_bound=row["score_bound"],
        depth=row["depth"],
        seldepth=row["seldepth"],
        nodes=row["nodes"],
        nps=row["nps"],
        hashfull=row["hashfull"],
        pv=row["pv"],
        info_line=row["info_line"],
        time_ms=row["time_ms"],
        clock_after_ms=row["clock_after_ms"],
        engine_version_id=row["engine_version_id"],
    )


def _game_assignment_from_row(row: sqlite3.Row) -> GameAssignmentRecord:
    return GameAssignmentRecord(
        id=row["id"],
        game_id=row["game_id"],
        assignment_key=row["assignment_key"],
        worker_id=row["worker_id"],
        status=row["status"],
        sent_at=row["sent_at"],
        acked_at=row["acked_at"],
        finished_at=row["finished_at"],
        last_error=row["last_error"],
    )


def _game_progress_from_row(row: sqlite3.Row) -> GameProgressRecord:
    return GameProgressRecord(
        id=row["id"],
        assignment_id=row["assignment_id"],
        assignment_key=row["assignment_key"],
        game_id=row["game_id"],
        source=row["source"],
        stage=row["stage"],
        stage_label=row["stage_label"],
        stage_order=row["stage_order"],
        substage=row["substage"],
        status=row["status"],
        detail=row["detail"],
        engine_id=row["engine_id"],
        engine_name=row["engine_name"],
        current=row["current_value"],
        total=row["total_value"],
        metadata=json.loads(row["metadata"] or "{}"),
        occurred_at=row["occurred_at"],
    )


def _worker_from_row(row: sqlite3.Row) -> WorkerRecord:
    hw = None
    if row["hw"] is not None:
        hw = HardwareInfo.model_validate_json(row["hw"])

    return WorkerRecord(
        id=row["id"],
        label=row["label"],
        token_expires_at=row["token_expires_at"],
        status=row["status"],
        session_id=row["session_id"],
        app_commit=row["app_commit"],
        protocol_version=row["protocol_version"],
        machine_id=row["machine_id"],
        hw=hw,
        core_limit=row["core_limit"],
        tournament_scope=row["tournament_scope"],
        last_seen=row["last_seen"],
    )


def _artifact_spec_from_row(row: sqlite3.Row) -> EngineArtifactSpec | None:
    sha256 = row["artifact_sha256"]
    if not sha256:
        return None
    return EngineArtifactSpec(
        url=f"/api/engine-artifacts/{sha256}",
        sha256=str(sha256),
        size=int(row["artifact_size"]),
        format=str(row["artifact_format"]),
        entrypoint=str(row["entrypoint"]),
        platform=str(row["platform"]),
    )


def _engine_artifact_from_row(row: sqlite3.Row) -> EngineArtifactRecord:
    return EngineArtifactRecord(
        build_hash=str(row["build_hash"]),
        artifact_sha256=str(row["artifact_sha256"]),
        artifact_size=int(row["artifact_size"]),
        artifact_format=str(row["artifact_format"]),
        entrypoint=str(row["entrypoint"]),
        platform=str(row["platform"]),
        storage_key=str(row["storage_key"]),
        created_at=str(row["created_at"]),
    )


def worker_hardware_profile(hw: HardwareInfo) -> str:
    return _json_dump(
        {
            "cpu_model": hw.cpu_model.strip(),
            "physical_cores": hw.physical_cores,
            "logical_cores": hw.logical_cores,
            "os": hw.os.strip(),
        }
    )


def _json_dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _bool_setting(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _int_setting(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default
