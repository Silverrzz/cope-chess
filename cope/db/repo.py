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
    EngineSpec,
    HardwareInfo,
    OpeningLine,
    TournamentConfig,
    WorkerResources,
)


@dataclass(frozen=True, slots=True)
class CategoryRecord:
    id: int
    name: str
    description: str
    default_config: dict[str, Any]
    active: bool
    created_at: str


@dataclass(frozen=True, slots=True)
class RatingListRecord:
    id: int
    name: str
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
    started_at: str | None
    finished_at: str | None

    @property
    def category_id(self) -> None:
        """Compatibility for legacy server-rendered views; tournaments are list-agnostic."""
        return None

    @property
    def settings_unlinked(self) -> bool:
        return True


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
    depth: int | None
    nodes: int | None
    nps: int | None
    pv: str | None
    info_line: str | None
    time_ms: int
    clock_after_ms: int


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
    last_seen: str | None

    @property
    def capacity(self) -> WorkerResources | None:
        if self.hw is None:
            return None
        return WorkerResources(
            threads=self.hw.physical_cores,
            hash_mb=self.hw.total_ram_mb,
        )


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
    active: bool
    benchmark_current: bool
    engine_active: bool
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
    tournament_id: int
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


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def utc_now_datetime() -> datetime:
    return datetime.now(UTC)


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
    connection.execute("DELETE FROM ratings WHERE engine_id = ?", (version_id,))
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
                  EXISTS (
                    SELECT 1 FROM engine_benchmarks benchmark
                    WHERE benchmark.build_hash = version.build_hash
                  ) AS benchmark_current
           FROM engine_versions version JOIN engines engine ON engine.id = version.engine_id
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
                      EXISTS (
                        SELECT 1 FROM engine_benchmarks benchmark
                        WHERE benchmark.build_hash = version.build_hash
                      ) AS benchmark_current
               FROM engine_versions version JOIN engines engine ON engine.id = version.engine_id
               ORDER BY engine.name, version.created_at DESC, version.id DESC"""
        )
    )


def list_engine_versions(connection: sqlite3.Connection, engine_id: int) -> tuple[EngineVersionRecord, ...]:
    return tuple(record for record in list_engine_records(connection) if record.engine_id == engine_id)


def create_category(
    connection: sqlite3.Connection,
    *,
    name: str,
    description: str = "",
    default_config: dict[str, Any] | None = None,
    active: bool = True,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO categories (name, description, default_config, active, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, description, _json_dump(default_config or {}), int(active), utc_now()),
    )
    return int(cursor.lastrowid)


def update_category(
    connection: sqlite3.Connection,
    category_id: int,
    *,
    name: str,
    description: str = "",
    default_config: dict[str, Any] | None = None,
    active: bool = True,
) -> None:
    connection.execute(
        """
        UPDATE categories
        SET name = ?, description = ?, default_config = ?, active = ?
        WHERE id = ?
        """,
        (
            name,
            description,
            _json_dump(default_config or {}),
            int(active),
            category_id,
        ),
    )


def get_category(
    connection: sqlite3.Connection,
    category_id: int,
) -> CategoryRecord | None:
    row = connection.execute(
        "SELECT * FROM categories WHERE id = ?",
        (category_id,),
    ).fetchone()
    if row is None:
        return None
    return _category_from_row(row)


def list_categories(
    connection: sqlite3.Connection,
    *,
    active_only: bool = False,
) -> tuple[CategoryRecord, ...]:
    sql = "SELECT * FROM categories"
    params: tuple[Any, ...] = ()
    if active_only:
        sql = f"{sql} WHERE active = ?"
        params = (1,)
    sql = f"{sql} ORDER BY active DESC, name"
    return tuple(_category_from_row(row) for row in connection.execute(sql, params))


def category_tournament_count(connection: sqlite3.Connection, category_id: int) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS count FROM tournaments WHERE category_id = ?",
        (category_id,),
    ).fetchone()
    return int(row["count"])


def delete_category(connection: sqlite3.Connection, category_id: int) -> None:
    """Delete a category. Raises ValueError if tournaments or ratings reference it."""
    if category_id == 1:
        raise ValueError("the default category cannot be deleted")
    if category_tournament_count(connection, category_id) > 0:
        raise ValueError("category has tournaments; deactivate it instead of deleting")
    row = connection.execute(
        "SELECT COUNT(*) AS count FROM ratings WHERE category_id = ?",
        (category_id,),
    ).fetchone()
    if int(row["count"]) > 0:
        raise ValueError("category has ratings; deactivate it instead of deleting")
    connection.execute("DELETE FROM categories WHERE id = ?", (category_id,))


def get_engine(connection: sqlite3.Connection, engine_id: int) -> EngineSpec | None:
    row = connection.execute(
        """SELECT version.*, engine.name, engine.author, engine.active AS engine_active
           FROM engine_versions version JOIN engines engine ON engine.id = version.engine_id
           WHERE version.id = ?""",
        (engine_id,),
    ).fetchone()
    if row is None:
        return None
    return _engine_from_row(row)


def list_engines(connection: sqlite3.Connection, *, active_only: bool = False) -> tuple[EngineSpec, ...]:
    sql = """SELECT version.*, engine.name, engine.author, engine.active AS engine_active
             FROM engine_versions version JOIN engines engine ON engine.id = version.engine_id"""
    params: tuple[Any, ...] = ()
    if active_only:
        sql = f"""{sql} WHERE engine.active = ?
                 AND EXISTS (
                   SELECT 1 FROM engine_benchmarks benchmark
                   WHERE benchmark.build_hash = version.build_hash
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
) -> int:
    created_at = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO tournaments (
          name, category_id, settings_unlinked, config, status, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            None,
            1,
            config.model_dump_json(),
            status,
            created_at,
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


def list_tournaments(connection: sqlite3.Connection) -> tuple[TournamentRecord, ...]:
    return tuple(
        _tournament_from_row(row)
        for row in connection.execute("SELECT * FROM tournaments ORDER BY id DESC")
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
    connection.execute(
        """
        UPDATE tournaments
        SET name = ?, category_id = ?, settings_unlinked = ?, config = ?
        WHERE id = ?
        """,
        (
            name,
            None,
            1,
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


def delete_tournament(connection: sqlite3.Connection, tournament_id: int) -> None:
    """Delete a tournament and its games, moves, and participants (cascade)."""
    rating_commits = list_tournament_rating_commits(connection, tournament_id)
    for rating_commit in rating_commits:
        if rating_commit.status == "applied":
            raise ValueError("tournament results are already part of the ratings")
        if rating_commit.status in {"pending", "claimed"}:
            raise ValueError("tournament has a rating commit in progress")
    connection.execute("DELETE FROM tournaments WHERE id = ?", (tournament_id,))


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
) -> tuple[GameRecord, ...]:
    if status is None:
        rows = connection.execute(
            """
            SELECT * FROM games
            WHERE tournament_id = ?
            ORDER BY round, pair_index, id
            """,
            (tournament_id,),
        )
    else:
        rows = connection.execute(
            """
            SELECT * FROM games
            WHERE tournament_id = ? AND status = ?
            ORDER BY round, pair_index, id
            """,
            (tournament_id, status),
        )
    return tuple(_game_from_row(row) for row in rows)


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


def record_move(
    connection: sqlite3.Connection,
    *,
    game_id: int,
    ply: int,
    uci: str,
    san: str,
    is_book: bool = False,
    eval_cp: int | None = None,
    eval_mate: int | None = None,
    depth: int | None = None,
    nodes: int | None = None,
    nps: int | None = None,
    pv: str | None = None,
    info_line: str | None = None,
    time_ms: int = 0,
    clock_after_ms: int = 0,
) -> None:
    connection.execute(
        """
        INSERT INTO moves (
          game_id, ply, uci, san, is_book, eval_cp, eval_mate, depth,
          nodes, nps, pv, info_line, time_ms, clock_after_ms
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game_id,
            ply,
            uci,
            san,
            int(is_book),
            eval_cp,
            eval_mate,
            depth,
            nodes,
            nps,
            pv,
            info_line,
            time_ms,
            clock_after_ms,
        ),
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
    cursor = connection.execute(
        """
        UPDATE game_assignments
        SET status = 'abandoned', finished_at = ?, last_error = ?
        WHERE id = ? AND assignment_key = ? AND status IN ('assigned', 'acked', 'live')
        """,
        (utc_now(), error[:500], assignment_id, assignment_key),
    )
    if cursor.rowcount == 1:
        _reset_games_for_replay(connection, (assignment.game_id,))


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
            last_error = ?,
            worker_id = NULL
        WHERE worker_id = ?
          AND status IN ('assigned', 'acked', 'live')
        """,
        (now, reason[:500], worker_id),
    )
    _reset_games_for_replay(connection, game_ids)


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
) -> None:
    """Discard every attempt-scoped artifact before games are reassigned."""
    ids = tuple(dict.fromkeys(int(game_id) for game_id in game_ids))
    if not ids:
        return
    placeholders = ", ".join("?" for _ in ids)
    connection.execute(f"DELETE FROM moves WHERE game_id IN ({placeholders})", ids)
    connection.execute(
        f"DELETE FROM game_hardware_scores WHERE game_id IN ({placeholders})",
        ids,
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
          AND status IN ('pending', 'assigned', 'live')
        """,
        ids,
    )


def delete_worker(connection: sqlite3.Connection, worker_id: int) -> None:
    """Delete a worker and return its active assignments to the pending pool."""
    game_ids = _active_worker_game_ids(connection, worker_id)
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
            worker_id = NULL
        WHERE worker_id = ?
        """,
        (utc_now(), worker_id),
    )
    _reset_games_for_replay(connection, game_ids)
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


def create_chat_message(
    connection: sqlite3.Connection,
    *,
    tournament_id: int,
    display_name: str,
    text: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO chat_messages (tournament_id, display_name, text, at)
        VALUES (?, ?, ?, ?)
        """,
        (tournament_id, display_name, text, utc_now()),
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
    limit: int = 50,
    tournament_id: int | None = None,
) -> tuple[ChatMessageRecord, ...]:
    if tournament_id is None:
        rows = connection.execute(
            "SELECT * FROM chat_messages WHERE tournament_id IS NOT NULL ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    else:
        rows = connection.execute(
            """
            SELECT * FROM chat_messages
            WHERE tournament_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (tournament_id, limit),
        )
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
) -> int:
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
            INSERT INTO deployment_jobs (requested_ref, status, requested_at)
            VALUES (?, 'pending', ?)
            """,
            (requested_ref, now),
        )
    except sqlite3.IntegrityError as error:
        raise ValueError("a deployment is already in progress") from error
    job_id = int(cursor.lastrowid)
    connection.execute(
        """
        INSERT INTO deployment_targets (
          job_id, target_kind, target_id, label, current_commit, status, updated_at
        )
        VALUES (?, 'server', NULL, 'Server platform', NULL, 'pending', ?)
        """,
        (job_id, now),
    )
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
    return None if row is None else RatingListRecord(row["id"], row["name"], row["created_at"])


def list_rating_lists(connection: sqlite3.Connection) -> tuple[RatingListRecord, ...]:
    return tuple(
        RatingListRecord(row["id"], row["name"], row["created_at"])
        for row in connection.execute("SELECT * FROM rating_lists ORDER BY name, id")
    )


def delete_rating_list(connection: sqlite3.Connection, rating_list_id: int) -> None:
    connection.execute("DELETE FROM rating_lists WHERE id = ?", (rating_list_id,))


def _category_from_row(row: sqlite3.Row) -> CategoryRecord:
    default_config = json.loads(row["default_config"])
    adjudication = default_config.get("adjudication")
    if isinstance(adjudication, dict):
        default_config["adjudication"] = {
            key: adjudication[key]
            for key in ("draw", "resign", "max_moves")
            if key in adjudication
        }
    return CategoryRecord(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        default_config=default_config,
        active=bool(row["active"]),
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
        depth=row["depth"],
        nodes=row["nodes"],
        nps=row["nps"],
        pv=row["pv"],
        info_line=row["info_line"],
        time_ms=row["time_ms"],
        clock_after_ms=row["clock_after_ms"],
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
        last_seen=row["last_seen"],
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
