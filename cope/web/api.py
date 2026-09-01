from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import urllib.parse
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import chess
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from starlette.datastructures import UploadFile

from cope.chat import announce_tournament_finished
from cope.core.models import EngineArtifactSpec, HardwareInfo, OpeningLine, TournamentConfig
from cope.environment_clone import (
    CLONE_PROTOCOL_VERSION,
    RemoteCloneError,
    authorize_environment_export,
    cancel_clone_job,
    clone_catalog_payload,
    clone_job_payload,
    create_clone_from_source,
    create_environment_export,
    engine_artifact_root,
    environment_export_artifact,
    environment_export_dataset_path,
    environment_export_payload,
    environment_instance_id,
    environment_inventory,
    list_clone_jobs,
    normalize_source_url,
    authenticated_source_opener,
    remove_clone_transfer_tree,
    remote_json,
    resume_clone_job,
)
from cope.db import (
    CURRENT_EVENT_STATUSES,
    ChatSettingsRecord,
    CopeBuildSettingsRecord,
    PlatformSettingsRecord,
    EventRecord,
    append_suite_openings,
    cancel_tool_job,
    create_deployment_job,
    create_dockerfile_pull_job,
    create_engine,
    create_engine_version,
    create_git_host,
    create_opening_suite,
    create_rating_list,
    create_tournament,
    create_worker,
    create_tool_job,
    create_puzzle_suite,
    connect_database,
    count_games,
    create_badge,
    database_stats,
    database_schema_version,
    delete_chat_message,
    delete_badge,
    delete_engine,
    delete_engine_version,
    delete_event,
    delete_git_host,
    delete_opening_suite,
    delete_rating_list,
    delete_tournament,
    delete_worker,
    engine_game_filter_options,
    engine_build_is_benchmarked,
    engine_result_summary,
    event_resource_counts,
    forget_benchmarker,
    forget_engine_benchmarks,
    forget_failed_benchmark_job,
    get_benchmarker,
    get_benchmarker_by_session_id,
    get_badge,
    get_deployment_job,
    get_chat_settings,
    get_cope_build_settings,
    get_platform_settings,
    get_engine_record,
    get_event,
    get_event_by_slug,
    get_event_chat_settings,
    get_engine_artifact_by_sha256,
    get_engine_family,
    get_engine_version_record,
    get_git_host,
    get_game,
    get_opening_suite,
    get_rating_list,
    get_tournament,
    get_worker,
    get_worker_by_session_id,
    get_tool_job,
    get_puzzle_suite,
    invalidate_game_pair,
    invalidate_rating_list_engine_games,
    list_deployment_jobs,
    list_deployment_targets,
    list_deployment_targets_for_jobs,
    latest_dockerfile_pull_job,
    list_badge_engine_ids,
    list_badges,
    list_benchmarkers,
    list_benchmark_hardware,
    list_chat_messages,
    list_engine_games,
    list_engine_badges,
    list_engine_game_counts,
    list_event_awards,
    list_event_cast,
    list_event_contest_cast,
    list_event_contests,
    list_event_sessions,
    list_event_stages,
    list_event_updates,
    list_events,
    list_engine_records,
    list_engine_result_summaries,
    list_engine_benchmark_jobs,
    list_engines,
    list_engine_families,
    list_engine_versions,
    list_git_hosts,
    list_games,
    list_games_page,
    list_games_by_status,
    list_active_games,
    list_opening_suites,
    list_rating_lists,
    list_rating_list_engine_ids,
    list_rating_rows,
    list_service_heartbeats,
    list_suite_openings,
    list_tournaments,
    list_tool_job_items,
    list_tool_jobs,
    list_puzzle_suite_engine_results,
    list_puzzle_suite_puzzles,
    list_puzzle_suite_runs,
    list_puzzle_suites,
    list_tournament_rating_commits,
    list_uncommitted_finished_tournaments,
    list_workers,
    mint_benchmarker_token,
    mint_worker_token_for_worker,
    replace_suite_openings,
    replace_badge_engines,
    replay_game,
    reset_event,
    record_manual_benchmark,
    prepare_puzzle_suite_run,
    register_engine_artifact,
    reschedule_engine_benchmarks,
    request_tournament_rating_commit,
    restore_tournament,
    resume_paused_tournament_games,
    revoke_worker,
    schedule_tournament,
    set_tournament_concurrency,
    set_tournament_status,
    set_puzzle_suite_puzzle_included,
    suite_opening_count,
    update_chat_settings,
    update_cope_build_settings,
    update_platform_settings,
    update_badge,
    update_engine,
    update_engine_version,
    update_git_host,
    update_opening_suite,
    update_rating_list_anchor,
    update_tournament,
    update_tournament_name,
    update_worker_assignment_settings,
    update_worker_label,
    unschedule_tournament,
)
from cope.events import (
    event_extension_payload,
    event_modules,
    get_event_module,
    provision_event_module,
    register_event_api_routes,
)
from cope.engine_dockerfiles import (
    EngineDockerfileError,
    list_engine_dockerfiles,
    read_engine_dockerfile,
)
from cope.engine_artifacts import (
    ARTIFACT_FORMAT,
    ARTIFACT_PLATFORM,
    sha256_file,
    validate_artifact_archive,
)
from cope.pgn import (
    PgnExportFilters,
    iter_pgn_export,
    pgn_export_exists,
    safe_pgn_filename,
)
from cope.web.engine_sources import (
    SourceServiceError,
    canonical_repository_url,
    list_releases,
    search_repositories,
)
from cope.web.openings import format_opening, parse_opening_input
from cope.web.forms import form_value
from cope.web.requests import read_form
from cope.version import app_version
from cope.ratings import (
    RatingCommitError,
    recalculate_ratings,
    uncommit_tournament_ratings,
)
from cope.runner.scheduler import (
    add_running_tournament_participant,
    materialize_tournament_schedule,
    remove_running_tournament_participant,
    start_tournament,
)
from cope.tournament.estimates import TournamentEstimator


LOG = logging.getLogger("cope.web.api")
OPENING_EDITOR_POSITION_LIMIT = 10_000
ADMIN_GAME_RESULT_TYPES = frozenset(
    {
        "win_checkmate",
        "win_adjudication",
        "win_max_moves",
        "win_timeout",
        "win_illegal_move",
        "win_engine_error",
        "win_variant_end",
        "win_other",
        "draw_stalemate",
        "draw_insufficient_material",
        "draw_adjudication",
        "draw_max_moves",
        "draw_threefold_repetition",
        "draw_fivefold_repetition",
        "draw_fifty_moves",
        "draw_seventyfive_moves",
        "draw_variant_end",
        "draw_other",
    }
)


def _engine_artifact_root() -> Path:
    return Path(
        os.environ.get("COPE_ENGINE_ARTIFACT_DIR", "/var/lib/cope/engine-artifacts")
    ).expanduser().resolve()


def _engine_artifact_path(storage_key: str) -> Path:
    return _engine_artifact_root() / f"{storage_key}.tar.gz"


def _engine_artifact_spec(record) -> EngineArtifactSpec:
    return EngineArtifactSpec(
        url=f"/api/engine-artifacts/{record.artifact_sha256}",
        sha256=record.artifact_sha256,
        size=record.artifact_size,
        format=record.artifact_format,
        entrypoint=record.entrypoint,
        platform=record.platform,
    )


async def _store_engine_artifact_upload(
    request: Request,
    connection: sqlite3.Connection,
    *,
    build_hash: str,
    expected_sha256: str | None = None,
):
    maximum = int(
        os.environ.get("COPE_ENGINE_ARTIFACT_MAX_BYTES", str(1024 * 1024 * 1024))
    )
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > maximum:
        raise HTTPException(status_code=413, detail="Engine artifact is too large.")
    root = _engine_artifact_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = root / f".upload-{uuid.uuid4().hex}"
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("xb") as output:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > maximum:
                    raise HTTPException(status_code=413, detail="Engine artifact is too large.")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size == 0:
            raise HTTPException(status_code=422, detail="Engine artifact is empty.")
        artifact_sha256 = digest.hexdigest()
        if expected_sha256 is not None and artifact_sha256 != expected_sha256:
            raise HTTPException(status_code=422, detail="Engine artifact SHA-256 does not match.")
        try:
            await asyncio.to_thread(
                validate_artifact_archive,
                temporary,
                expected_build_hash=build_hash,
                expected_entrypoint="engine",
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        destination = _engine_artifact_path(artifact_sha256)
        try:
            connection.execute("BEGIN IMMEDIATE")
            artifact = register_engine_artifact(
                connection,
                build_hash=build_hash,
                artifact_sha256=artifact_sha256,
                artifact_size=size,
                artifact_format=ARTIFACT_FORMAT,
                entrypoint="engine",
                platform=ARTIFACT_PLATFORM,
                storage_key=artifact_sha256,
            )
            if destination.exists():
                stored_sha256 = await asyncio.to_thread(sha256_file, destination)
                if destination.stat().st_size != size or stored_sha256 != artifact_sha256:
                    os.replace(temporary, destination)
            else:
                os.replace(temporary, destination)
            destination.chmod(0o600)
            connection.commit()
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception:
            connection.rollback()
            raise
        return artifact
    finally:
        temporary.unlink(missing_ok=True)


def _bearer_credential(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, credential = authorization.partition(" ")
    return credential if scheme.lower() == "bearer" else ""


def _cope_build_settings(connection: sqlite3.Connection) -> CopeBuildSettingsRecord:
    return get_cope_build_settings(
        connection,
        default_repository_url=os.environ.get("COPE_UPDATE_REPOSITORY_URL", ""),
        default_update_ref=os.environ.get("COPE_UPDATE_REF", "main"),
    )


def _public_update_repository_url(raw: str) -> str:
    raw = raw.strip()
    if not raw or raw.startswith(("/", ".")):
        return ""
    if "://" not in raw:
        if ":" in raw:
            authority, path = raw.split(":", 1)
            host = authority.rsplit("@", 1)[-1]
            raw = f"https://{host}/{path.lstrip('/')}"
        else:
            raw = f"https://{raw}"
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"git", "http", "https", "ssh"} or not parsed.hostname:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    host = parsed.hostname.lower()
    netloc_host = f"[{host}]" if ":" in host else host
    web_port = port if parsed.scheme in {"http", "https"} else None
    netloc = (
        netloc_host
        if web_port in {None, 80, 443}
        else f"{netloc_host}:{web_port}"
    )
    path = f"/{parsed.path.strip('/')}".removesuffix(".git")
    if path == "/":
        return ""
    return urllib.parse.urlunsplit(("https", netloc, path, "", ""))


def _artifact_client_is_authenticated(connection, credential: str) -> bool:
    if not credential:
        return False
    worker = get_worker_by_session_id(connection, credential)
    if worker is not None and worker.status in {
        "connected",
        "downloading",
        "building",
        "ready",
        "busy",
    }:
        return True
    benchmarker = get_benchmarker_by_session_id(connection, credential)
    return benchmarker is not None and benchmarker.status in {"connected", "busy"}


def _load_engine_dockerfile(selected_path: str) -> str:
    try:
        return read_engine_dockerfile(selected_path)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Dockerfile {selected_path!r} was not found in data/engines.",
        ) from exc
    except EngineDockerfileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="The engine Dockerfile could not be read.") from exc


def _engine_version_admin_payload(version) -> dict[str, Any]:
    payload = jsonable_encoder(version)
    payload["benchmark_current"] = version.benchmark_current
    payload["active"] = version.active
    return payload


def _benchmark_job_admin_payload(item) -> dict[str, Any]:
    """Stable, intentionally small API shape for engine-version observability."""
    job = item.job
    result = item.result
    return {
        "id": job.id,
        "build_hash": job.build_hash,
        "hardware_key": job.hardware_key,
        "status": job.status,
        "attempt": job.attempt,
        "scheduled_at": job.scheduled_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": job.error,
        "output": result.output if result is not None else job.output,
        "benchmarker": None if item.benchmarker_label is None else {
            "id": job.benchmarker_id,
            "label": item.benchmarker_label,
            "status": item.benchmarker_status,
        },
        "hardware": jsonable_encoder(item.hardware),
        "result": None if result is None else {
            "nps": result.nps,
            "elapsed_ms": result.elapsed_ms,
            "recorded_at": result.recorded_at,
            "artifact_sha256": result.artifact_sha256,
        },
    }


def _benchmark_activity(output: str) -> dict[str, str] | None:
    matches = list(
        re.finditer(
            r"(?m)^\[([^\]]+)\] ([^/\s]+)/([^\s]+) ([^\s]+)\n",
            output,
        )
    )
    if not matches:
        return None
    match = matches[-1]
    detail = output[match.end():].strip()
    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    return {
        "updated_at": match.group(1),
        "stage": match.group(2),
        "substage": match.group(3),
        "status": match.group(4),
        "detail": (lines[-1] if lines else "Working")[-500:],
    }


def _admin_rating_list_summaries(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        WITH rating_stats AS (
          SELECT rating_list_id,
                 COUNT(*) AS engine_versions,
                 COALESCE(SUM(games_played), 0) / 2 AS games
          FROM rating_list_ratings
          GROUP BY rating_list_id
        ),
        commit_stats AS (
          SELECT rating_list_id, COUNT(*) AS tournaments
          FROM tournament_rating_list_commits
          WHERE status = 'applied'
          GROUP BY rating_list_id
        )
        SELECT rating_list.*,
               COALESCE(rating_stats.engine_versions, 0) AS engine_versions,
               COALESCE(rating_stats.games, 0) AS games,
               COALESCE(commit_stats.tournaments, 0) AS tournaments
        FROM rating_lists rating_list
        LEFT JOIN rating_stats ON rating_stats.rating_list_id = rating_list.id
        LEFT JOIN commit_stats ON commit_stats.rating_list_id = rating_list.id
        ORDER BY rating_list.name, rating_list.id
        """
    )
    return [
        {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "anchor_engine_id": row["anchor_engine_id"],
            "anchor_elo": float(row["anchor_elo"]),
            "created_at": str(row["created_at"]),
            "engine_versions": int(row["engine_versions"]),
            "games": int(row["games"]),
            "tournaments": int(row["tournaments"]),
        }
        for row in rows
    ]


def _admin_rating_list_tournaments(
    connection: sqlite3.Connection,
    rating_list_id: int | None = None,
) -> list[dict[str, Any]]:
    rating_list_filter = ""
    parameters: tuple[int, ...] = ()
    if rating_list_id is not None:
        rating_list_filter = " AND rating_commit.rating_list_id = ?"
        parameters = (rating_list_id,)
    rows = connection.execute(
        f"""
        WITH selected_commits AS (
          SELECT *
          FROM tournament_rating_list_commits rating_commit
          WHERE rating_commit.status = 'applied'{rating_list_filter}
        ),
        hardware_by_game AS (
          SELECT score.game_id, COUNT(*) AS score_count
          FROM game_hardware_scores score
          JOIN games game ON game.id = score.game_id
          WHERE game.tournament_id IN (
            SELECT tournament_id FROM selected_commits
          )
          GROUP BY score.game_id
        ),
        game_stats AS (
          SELECT game.tournament_id,
                 COUNT(*) AS games,
                 COUNT(*) FILTER (WHERE hardware.score_count = 2) AS hardware_games
          FROM games game
          LEFT JOIN hardware_by_game hardware ON hardware.game_id = game.id
          WHERE game.status = 'finished'
            AND game.result IN ('1-0', '0-1', '1/2-1/2')
            AND game.tournament_id IN (
              SELECT tournament_id FROM selected_commits
            )
          GROUP BY game.tournament_id
        )
        SELECT rating_commit.*, tournament.name AS tournament_name,
               tournament.status AS tournament_status,
               rating_list.name AS rating_list_name,
               COALESCE(game_stats.games, 0) AS games,
               COALESCE(game_stats.hardware_games, 0) AS hardware_games
        FROM selected_commits rating_commit
        JOIN tournaments tournament ON tournament.id = rating_commit.tournament_id
        JOIN rating_lists rating_list ON rating_list.id = rating_commit.rating_list_id
        LEFT JOIN game_stats ON game_stats.tournament_id = rating_commit.tournament_id
        ORDER BY COALESCE(rating_commit.applied_at, rating_commit.requested_at) DESC,
                 rating_commit.tournament_id DESC
        """,
        parameters,
    )
    return [
        {
            "tournament_id": int(row["tournament_id"]),
            "tournament_name": str(row["tournament_name"]),
            "tournament_status": str(row["tournament_status"]),
            "rating_list_id": int(row["rating_list_id"]),
            "rating_list_name": str(row["rating_list_name"]),
            "requested_at": str(row["requested_at"]),
            "applied_at": row["applied_at"],
            "games": int(row["games"]),
            "hardware_games": int(row["hardware_games"]),
            "missing_hardware_games": int(row["games"]) - int(row["hardware_games"]),
        }
        for row in rows
    ]


def _admin_ratings_payload(connection: sqlite3.Connection) -> dict[str, Any]:
    return {"rating_lists": _admin_rating_list_summaries(connection)}


class TournamentPayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    config: TournamentConfig

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("tournament name cannot be blank")
        return value


class TournamentNamePayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("tournament name cannot be blank")
        return value


class TournamentCreatorBatchPayload(BaseModel):
    tournaments: list[TournamentPayload] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_shared_settings(self) -> TournamentCreatorBatchPayload:
        shared = self.tournaments[0].config.model_dump(
            mode="json",
            exclude={"participants"},
        )
        if any(
            item.config.model_dump(mode="json", exclude={"participants"}) != shared
            for item in self.tournaments[1:]
        ):
            raise ValueError("batch tournaments must share the same settings")
        return self


class GauntletPreviewPayload(BaseModel):
    rating_list_id: int = Field(gt=0)
    hero_engine_id: int = Field(gt=0)
    elo_estimate: float = Field(ge=-10000, le=10000)
    gauntlet_size: int = Field(ge=2, le=500)


class TournamentParticipantPayload(BaseModel):
    engine_id: int = Field(gt=0)


class RatingCommitPayload(BaseModel):
    rating_list_ids: list[int] = Field(min_length=1)


class RatingListPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("rating list name cannot be blank")
        return value


class TournamentStatusPayload(BaseModel):
    action: str = Field(min_length=1, max_length=20)


class TournamentBulkStatusPayload(BaseModel):
    action: Literal["pause", "resume"]


class TournamentSchedulePayload(BaseModel):
    scheduled_start_at: datetime

    @field_validator("scheduled_start_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled start time must include a timezone")
        return value.astimezone(UTC)


class EnginePayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    author: str = Field(default="", max_length=120)
    active: bool = True

    @field_validator("name")
    @classmethod
    def strip_engine_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("engine name cannot be blank")
        return value

    @field_validator("author")
    @classmethod
    def strip_engine_author(cls, value: str) -> str:
        return value.strip()


class BadgePayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    emoji: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=240)
    engine_ids: list[int] = Field(default_factory=list, max_length=10_000)

    @field_validator("name")
    @classmethod
    def strip_badge_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("badge name cannot be blank")
        return value

    @field_validator("emoji")
    @classmethod
    def strip_badge_emoji(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("select an emoji")
        return value

    @field_validator("description")
    @classmethod
    def strip_badge_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("engine_ids")
    @classmethod
    def normalize_badge_engine_ids(cls, value: list[int]) -> list[int]:
        if any(engine_id < 1 for engine_id in value):
            raise ValueError("engine ids must be positive")
        return list(dict.fromkeys(value))


class EngineVersionUpdatePayload(BaseModel):
    version: str = Field(min_length=1, max_length=80)
    dockerfile_path: str = Field(default="", max_length=500)
    uci_options: dict[str, str | int | bool] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def strip_value(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value


class ManualBenchmarkPayload(BaseModel):
    nps: int = Field(gt=0)
    elapsed_ms: int = Field(default=0, ge=0)
    hardware_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    machine_id: str | None = Field(default=None, min_length=1, max_length=128)
    hardware: HardwareInfo | None = None

    @model_validator(mode="after")
    def validate_hardware(self):
        if self.hardware_key is None and (self.machine_id is None or self.hardware is None):
            raise ValueError("Select a hardware profile or enter new hardware details.")
        return self


class RatingCalculationPayload(BaseModel):
    anchor_engine_id: int = Field(gt=0)
    anchor_elo: float = Field(ge=-10000, le=10000)


class EngineVersionCreatePayload(BaseModel):
    version: str = Field(min_length=1, max_length=80)
    distribution: Literal["managed", "worker_local"] = "managed"
    worker_local_key: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$",
    )
    git_host_id: int | None = Field(default=None, gt=0)
    repository_full_name: str = Field(default="", max_length=300)
    source_ref: str = Field(default="", max_length=200)
    source_kind: str = Field(default="commit", pattern=r"^(release|commit)$")
    dockerfile_path: str = Field(default="", max_length=500)
    uci_options: dict[str, str | int | bool] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def strip_value(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value

    @field_validator("repository_full_name", "source_ref", "dockerfile_path")
    @classmethod
    def strip_optional_value(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_distribution(self):
        if self.distribution == "worker_local":
            if self.worker_local_key is None:
                raise ValueError("Enter a worker-local engine key.")
            return self
        if self.git_host_id is None:
            raise ValueError("Choose a Git host.")
        if not self.repository_full_name or not self.source_ref or not self.dockerfile_path:
            raise ValueError("Managed versions require a repository, source, and Dockerfile.")
        if self.worker_local_key is not None:
            raise ValueError("Managed versions cannot have a worker-local key.")
        return self


class GitHostPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(pattern=r"^(github|gitlab)$")
    base_url: str = Field(min_length=8, max_length=500)
    api_url: str = Field(min_length=8, max_length=500)
    access_token: str | None = Field(default=None, max_length=1000)
    clear_access_token: bool = False
    enabled: bool = True

    @field_validator("name", "base_url", "api_url")
    @classmethod
    def strip_host_value(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            raise ValueError("value cannot be blank")
        return value

    @field_validator("base_url", "api_url")
    @classmethod
    def validate_host_url(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("Git host URLs must use HTTP or HTTPS")
        parsed = urllib.parse.urlsplit(value)
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError("Git host URLs cannot contain embedded credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("Git host URLs cannot contain a query or fragment")
        return value


class WorkerPayload(BaseModel):
    label: str = Field(default="worker", min_length=1, max_length=80)

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("worker label cannot be blank")
        return value


class WorkerTokenPayload(BaseModel):
    ttl_seconds: int = Field(default=7200, ge=60, le=86_400)


class BenchmarkerPayload(BaseModel):
    label: str = Field(default="benchmarker", min_length=1, max_length=80)
    ttl_seconds: int = Field(default=7200, ge=60, le=86_400)

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("benchmarker label cannot be blank")
        return value


class WhoHasThisPayload(BaseModel):
    engine_ids: list[int] = Field(min_length=1, max_length=500)
    option_name: str = Field(min_length=1, max_length=200)

    @field_validator("engine_ids")
    @classmethod
    def validate_engine_ids(cls, value: list[int]) -> list[int]:
        if any(engine_id <= 0 for engine_id in value):
            raise ValueError("engine ids must be positive")
        unique = list(dict.fromkeys(value))
        if len(unique) != len(value):
            raise ValueError("engine ids must be unique")
        return unique

    @field_validator("option_name")
    @classmethod
    def validate_option_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned or any(ord(character) < 32 for character in cleaned):
            raise ValueError("enter a valid UCI option name")
        return cleaned


class PuzzleSuiteCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    puzzles: str = Field(min_length=1, max_length=2_000_000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("enter a suite name")
        return normalized


class PuzzleSuiteUniquenessPayload(BaseModel):
    engine_id: int = Field(gt=0)
    movetime_ms: int = Field(default=30_000, ge=100, le=3_600_000)
    multipv: int = Field(default=2, ge=2, le=20)
    threads: int = Field(default=1, gt=0, le=1024)
    hash_mb: int = Field(default=256, gt=0, le=1_048_576)
    min_sigmoid_gap: float = Field(default=0.15, gt=0, le=1, allow_inf_nan=False)


class PuzzleSuiteDifficultyPayload(BaseModel):
    engine_ids: list[int] = Field(min_length=1, max_length=100)
    rating_list_id: int = Field(gt=0)
    movetime_ms: int = Field(default=30_000, ge=100, le=3_600_000)
    threads: int = Field(default=1, gt=0, le=1024)
    hash_mb: int = Field(default=256, gt=0, le=1_048_576)

    @field_validator("engine_ids")
    @classmethod
    def validate_engine_ids(cls, value: list[int]) -> list[int]:
        if any(engine_id <= 0 for engine_id in value):
            raise ValueError("engine ids must be positive")
        if len(set(value)) != len(value):
            raise ValueError("engine ids must be unique")
        return value


class PuzzleSuitePuzzleIncludePayload(BaseModel):
    included: bool


class InvalidateRatingListEnginePayload(BaseModel):
    engine_id: int = Field(gt=0)
    rating_list_id: int = Field(gt=0)


class EnvironmentExportPayload(BaseModel):
    datasets: list[str] = Field(min_length=1, max_length=100)


class EnvironmentClonePayload(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    admin_token: str = Field(min_length=1, max_length=1000)
    datasets: list[str] = Field(min_length=1, max_length=100)


class EnvironmentClonePreflightPayload(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    admin_token: str = Field(min_length=1, max_length=1000)


class WorkerSettingsPayload(BaseModel):
    core_limit: int | None = Field(default=None, ge=1)
    tournament_scope: Literal["all", "selected"] = "all"
    tournament_ids: list[int] = Field(default_factory=list)
    event_ids: list[int] = Field(default_factory=list)

    @field_validator("tournament_ids", "event_ids")
    @classmethod
    def validate_tournament_ids(cls, value: list[int]) -> list[int]:
        if any(tournament_id <= 0 for tournament_id in value):
            raise ValueError("tournament ids must be positive")
        return list(dict.fromkeys(value))


class DeploymentPayload(BaseModel):
    ref: str = Field(default="", max_length=200)
    scope: Literal["platform", "web"] = "platform"

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@+-]{0,199}", cleaned) is None:
            raise ValueError("Git ref contains unsupported characters")
        return cleaned


class UpdatePayload(BaseModel):
    method: Literal["platform", "web", "dockerfiles"]
    ref: str = Field(default="", max_length=200)

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@+-]{0,199}", cleaned) is None:
            raise ValueError("Git ref contains unsupported characters")
        return cleaned


class CopeBuildSettingsPayload(BaseModel):
    repository_url: str = Field(min_length=1, max_length=1000)
    update_ref: str = Field(min_length=1, max_length=200)

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(character.isspace() for character in cleaned):
            raise ValueError("Git repository URL cannot be blank or contain whitespace")
        if cleaned.startswith("-"):
            raise ValueError("Git repository URL cannot begin with a dash")
        if any(ord(character) < 32 for character in cleaned):
            raise ValueError("Git repository URL contains unsupported characters")
        if "://" in cleaned:
            parsed = urllib.parse.urlsplit(cleaned)
            if parsed.scheme not in {"git", "http", "https", "ssh"} or not parsed.hostname:
                raise ValueError("Git repository URL must use Git, HTTP, HTTPS, or SSH")
            try:
                parsed.port
            except ValueError as exc:
                raise ValueError("Git repository URL contains an invalid port") from exc
            if parsed.scheme in {"git", "http", "https"} and (
                parsed.username is not None or parsed.password is not None
            ):
                raise ValueError("Git repository URL cannot contain embedded credentials")
            if parsed.query or parsed.fragment:
                raise ValueError("Git repository URL cannot contain a query or fragment")
            if not parsed.path.strip("/"):
                raise ValueError("Git repository URL must include a repository path")
        return cleaned

    @field_validator("update_ref")
    @classmethod
    def validate_update_ref(cls, value: str) -> str:
        cleaned = value.strip()
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@+-]{0,199}", cleaned) is None:
            raise ValueError("Git branch or ref contains unsupported characters")
        return cleaned


class PlatformSettingsPayload(BaseModel):
    privatise_platform: bool


class ChatSettingsPayload(BaseModel):
    enabled: bool
    max_message_length: int = Field(ge=1, le=2_000)
    allow_anonymous_names: bool


def _tournament_status_matches(current: str, requested: str) -> bool:
    if requested in {"", "all"}:
        return True
    if requested == "active":
        return current in {"scheduled", "running", "paused"}
    if requested == "live":
        return current == "running"
    if requested == "ended":
        return current in {"finished", "aborted"}
    return current == requested


def register_api_routes(app: FastAPI) -> None:
    from cope.web import app as web_app

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    @app.get("/api/session")
    def session(
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        admin_token = web_app._admin_token(request)
        manager_token = web_app._manager_token(request)
        build_settings = _cope_build_settings(connection)
        platform_settings = get_platform_settings(connection)
        role = web_app._admin_session_role(request)
        token = admin_token if role == "admin" else manager_token if role == "manager" else None
        response = _json(
            {
                "admin_configured": bool(admin_token or manager_token),
                "authenticated": role is not None,
                "repository_url": _public_update_repository_url(build_settings.repository_url),
                "privatise_platform": platform_settings.privatise_platform,
                "secure_context": web_app._request_is_secure_or_local(request),
                "user": {"role": role} if role else None,
                "csrf_token": (
                    web_app._csrf_token(request, token)
                    if token and role
                    else ""
                ),
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/session")
    async def create_session(
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        admin_token = web_app._admin_token(request)
        manager_token = web_app._manager_token(request)
        if not admin_token and not manager_token:
            raise HTTPException(
                status_code=503,
                detail="Admin access is not configured.",
            )
        if not web_app._request_is_secure_or_local(request):
            raise HTTPException(status_code=403, detail="Admin access requires HTTPS.")

        form = await read_form(request)
        supplied = form_value(form, "token")
        if admin_token and hmac.compare_digest(supplied, admin_token):
            token = admin_token
            role = "admin"
        elif manager_token and hmac.compare_digest(supplied, manager_token):
            token = manager_token
            role = "manager"
        else:
            raise HTTPException(status_code=401, detail="Invalid admin token.")

        nonce = secrets.token_urlsafe(32)
        response = _json(
            {
                "authenticated": True,
                "csrf_token": web_app._csrf_for_nonce(token, nonce),
                "message": "Signed in.",
                "repository_url": _public_update_repository_url(
                    _cope_build_settings(connection).repository_url
                ),
                "privatise_platform": get_platform_settings(
                    connection
                ).privatise_platform,
                "user": {"role": role},
            }
        )
        response.set_cookie(
            "cope_admin_session",
            web_app._signed_value(token, nonce),
            httponly=True,
            secure=web_app._request_is_secure(request),
            samesite="lax",
            max_age=web_app.ADMIN_SESSION_MAX_AGE_SECONDS,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.delete("/api/session")
    def delete_session():
        response = _json({"authenticated": False, "message": "Signed out."})
        response.delete_cookie("cope_admin_session")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/admin/environment-export/capabilities")
    def environment_export_capabilities(
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        response = _json(
            {
                **clone_catalog_payload(),
                "instance_id": environment_instance_id(connection),
                "app_version": app_version(),
                "inventory": environment_inventory(connection),
                "csrf_token": web_app._csrf_token(request, web_app._admin_token(request)),
            }
        )
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.post("/api/admin/environment-exports")
    def create_admin_environment_export(
        payload: EnvironmentExportPayload,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            export_id, export_token, expires_at = create_environment_export(connection, payload.datasets)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        response = _json(
            {
                "export_id": export_id,
                "export_token": export_token,
                "expires_at": expires_at,
                "status": "queued",
                "message": "Environment export queued.",
            },
            status_code=201,
        )
        response.headers["Cache-Control"] = "private, no-store"
        return response

    def authorized_export(export_id: str, request: Request, connection):
        export = authorize_environment_export(connection, export_id, _bearer_credential(request))
        if export is None:
            raise HTTPException(status_code=401, detail="A current environment export token is required.")
        return export

    @app.get("/api/environment-exports/{export_id}/status")
    def environment_export_status(
        export_id: str,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        authorized_export(export_id, request, connection)
        payload = environment_export_payload(connection, export_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Environment export not found.")
        payload.pop("manifest", None)
        return _json(payload)

    @app.get("/api/environment-exports/{export_id}/manifest")
    def environment_export_manifest(
        export_id: str,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        authorized_export(export_id, request, connection)
        payload = environment_export_payload(connection, export_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Environment export not found.")
        if payload["status"] != "ready":
            raise HTTPException(status_code=409, detail="Environment export is not ready.")
        return _json(payload["manifest"])

    @app.get("/api/environment-exports/{export_id}/datasets/{dataset_name}")
    def download_environment_export_dataset(
        export_id: str,
        dataset_name: str,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        export = authorized_export(export_id, request, connection)
        if export["status"] != "ready":
            raise HTTPException(status_code=409, detail="Environment export is not ready.")
        row = connection.execute(
            """
            SELECT * FROM environment_export_datasets
            WHERE export_id = ? AND dataset_name = ? AND status = 'ready'
            """,
            (export_id, dataset_name),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Environment export dataset not found.")
        try:
            path = environment_export_dataset_path(export_id, dataset_name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Environment export dataset not found.") from exc
        if not path.is_file() or path.stat().st_size != int(row["byte_count"]):
            raise HTTPException(status_code=503, detail="Environment export dataset is unavailable.")
        response = FileResponse(path, media_type="application/gzip", filename=path.name)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Cope-Clone-SHA256"] = str(row["sha256"])
        return response

    @app.get("/api/environment-exports/{export_id}/artifacts/{artifact_sha256}")
    def download_environment_export_artifact(
        export_id: str,
        artifact_sha256: str,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        authorized_export(export_id, request, connection)
        artifact = environment_export_artifact(connection, export_id, artifact_sha256)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Environment export artifact not found.")
        path = engine_artifact_root() / f"{artifact['storage_key']}.tar.gz"
        if not path.is_file() or path.stat().st_size != int(artifact["artifact_size"]):
            raise HTTPException(status_code=503, detail="Environment export artifact is unavailable.")
        response = FileResponse(path, media_type="application/gzip", filename=f"{artifact_sha256}.tar.gz")
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Cope-Artifact-SHA256"] = artifact_sha256
        return response

    @app.delete("/api/environment-exports/{export_id}")
    def cancel_environment_export(
        export_id: str,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        authorized_export(export_id, request, connection)
        connection.execute(
            """
            UPDATE environment_exports
            SET cancel_requested = 1, status = 'cancelled', token_hash = ?,
                error = 'Environment export cancelled.', finished_at = COALESCE(finished_at, ?)
            WHERE export_id = ? AND status <> 'expired'
            """,
            ("0" * 64, datetime.now(UTC).isoformat(), export_id),
        )
        connection.commit()
        remove_clone_transfer_tree("exports", export_id)
        return _json({"message": "Environment export cancellation requested."})

    @app.put("/api/benchmarker/engine-artifacts/{build_hash}")
    async def upload_engine_artifact(
        build_hash: str,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if re.fullmatch(r"[0-9a-f]{64}", build_hash) is None:
            raise HTTPException(status_code=422, detail="Build hash is invalid.")
        credential = _bearer_credential(request)
        benchmarker = get_benchmarker_by_session_id(connection, credential) if credential else None
        if benchmarker is None or benchmarker.status != "busy":
            raise HTTPException(status_code=401, detail="A busy benchmarker session is required.")
        running = connection.execute(
            """SELECT 1 FROM benchmark_jobs
               WHERE benchmarker_id = ? AND build_hash = ? AND status = 'running'
               LIMIT 1""",
            (benchmarker.id, build_hash),
        ).fetchone()
        if running is None:
            raise HTTPException(status_code=409, detail="No matching benchmark job is running.")
        expected_sha256 = request.headers.get("x-cope-artifact-sha256", "")
        if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
            raise HTTPException(status_code=422, detail="Artifact SHA-256 header is invalid.")
        artifact = await _store_engine_artifact_upload(
            request,
            connection,
            build_hash=build_hash,
            expected_sha256=expected_sha256,
        )
        return _json({"artifact": _engine_artifact_spec(artifact)})

    @app.get("/api/engine-artifacts/{artifact_sha256}")
    def download_engine_artifact(
        artifact_sha256: str,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if re.fullmatch(r"[0-9a-f]{64}", artifact_sha256) is None:
            raise HTTPException(status_code=404, detail="Engine artifact was not found.")
        if not _artifact_client_is_authenticated(connection, _bearer_credential(request)):
            raise HTTPException(status_code=401, detail="A current client session is required.")
        artifact = get_engine_artifact_by_sha256(connection, artifact_sha256)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Engine artifact was not found.")
        path = _engine_artifact_path(artifact.storage_key)
        if not path.is_file() or path.stat().st_size != artifact.artifact_size:
            LOG.error("engine artifact is missing or corrupt sha256=%s path=%s", artifact_sha256, path)
            raise HTTPException(status_code=503, detail="Engine artifact is unavailable.")
        response = FileResponse(
            path,
            media_type="application/gzip",
            filename=f"{artifact_sha256}.tar.gz",
        )
        response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        response.headers["X-Cope-Artifact-SHA256"] = artifact.artifact_sha256
        response.headers["X-Cope-Artifact-Format"] = artifact.artifact_format
        return response

    # ------------------------------------------------------------------
    # Public reads
    # ------------------------------------------------------------------

    @app.get("/api/home")
    def public_home(
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        engines = web_app._engine_names(connection)
        running_tournaments = web_app._home_tournament_cards(connection, engines)
        for item in running_tournaments:
            summary = item["tournament"]
            summary["spectator_count"] = request.app.state.stream_hub.tournament_spectator_count(
                summary["record"].id
            )
        return _json(
            {
                "running_tournaments": running_tournaments,
                "upcoming_rows": web_app._upcoming_rows(connection, engines, limit=16),
                "recent_games": list_games_by_status(connection, "finished", limit=16),
                "engines": engines,
                "tournament_names": web_app._tournament_names(connection),
            }
        )

    @app.get("/api/tournaments")
    def public_tournaments(
        request: Request,
        status: str = "",
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        engines = web_app._engine_names(connection)
        estimator = TournamentEstimator(connection)
        event_tournament_ids = web_app._event_linked_tournament_ids(connection)
        available_tournaments = tuple(
            tournament
            for tournament in list_tournaments(connection)
            if tournament.status != "draft"
            and tournament.id not in event_tournament_ids
        )
        tournaments = tuple(
            tournament
            for tournament in available_tournaments
            if _tournament_status_matches(tournament.status, status)
        )
        items = web_app._tournament_summaries(
            connection,
            tournaments,
            engines,
            estimator=estimator,
            include_completed_estimates=False,
            include_active_estimates=False,
        )
        for item in items:
            item["spectator_count"] = request.app.state.stream_hub.tournament_spectator_count(
                item["record"].id
            )
        stats = web_app._tournament_index_stats(items)
        stats["total"] = len(available_tournaments)
        stats["active"] = sum(
            tournament.status in {"scheduled", "running", "paused"}
            for tournament in available_tournaments
        )
        return _json(
            {
                "tournaments": items,
                "tournament_stats": stats,
            }
        )

    @app.get("/api/events/current")
    def current_public_event(connection: sqlite3.Connection = Depends(web_app._database)):
        event = next(
            (
                item
                for item in list_events(connection, public_only=True)
                if item.status in CURRENT_EVENT_STATUSES
            ),
            None,
        )
        return _json({
            "event": (
                _event_summary_payload(connection, event, admin=False)
                if event is not None
                else None
            ),
        })

    @app.get("/api/events")
    def public_events(connection: sqlite3.Connection = Depends(web_app._database)):
        events = list_events(connection, public_only=True)
        current = next(
            (item for item in events if item.status in CURRENT_EVENT_STATUSES),
            None,
        )
        return _json({
            "server_time": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "current": (
                _event_summary_payload(connection, current, admin=False)
                if current is not None
                else None
            ),
            "events": [
                _event_summary_payload(connection, event, admin=False)
                for event in events
            ],
        })

    @app.get("/api/events/{slug}")
    def public_event(
        slug: str,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = _require_viewable_event(connection, slug, request)
        payload = _event_detail_payload(connection, event, admin=False)
        payload["spectator_count"] = request.app.state.stream_hub.event_spectator_count(
            event.id
        )
        return _json(payload)

    @app.post("/api/events/{slug}/chat")
    async def public_event_chat(
        slug: str,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = _require_viewable_event(connection, slug, request)
        form = await read_form(request)
        settings = get_event_chat_settings(
            connection,
            event.id,
            defaults=get_chat_settings(connection),
        )

        def create_message():
            return web_app._create_event_chat_message_from_form(
                connection,
                form,
                event_id=event.id,
                settings=settings,
            )

        message = await asyncio.to_thread(create_message)
        if message is not None:
            web_app._publish_event_chat_message(request, event.id, message)
        return _json({"message": message}, status_code=201)

    @app.get("/api/events/{slug}/tournaments/{tournament_id}")
    @app.get("/api/tournaments/{tournament_id}")
    def public_tournament(
        tournament_id: int,
        request: Request,
        slug: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=100, ge=25, le=200),
        cross_table: bool = Query(default=False),
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        tournament = _require_tournament(connection, tournament_id)
        if slug is not None and request.url.path.startswith("/api/events/"):
            event = _require_viewable_event(connection, slug, request)
            if not web_app._event_has_tournament(
                connection,
                event.id,
                tournament_id,
            ):
                raise HTTPException(status_code=404, detail="Tournament not found.")
        elif web_app._is_event_tournament(connection, tournament_id):
            raise HTTPException(status_code=404, detail="Event tournament.")
        if tournament.status == "draft":
            raise HTTPException(status_code=404, detail="Tournament not found.")
        engines = web_app._engine_names(connection)
        tournament_overview = web_app._tournament_summaries(
            connection,
            (tournament,),
            engines,
        )[0]
        total_games = count_games(connection, tournament.id, status="finished")
        games = list_games_page(
            connection,
            tournament.id,
            page=page,
            page_size=page_size,
            status="finished",
        )
        active_games = list_active_games(
            connection,
            tournament_id=tournament.id,
            limit=None if cross_table else 500,
        )
        raw_game_id = request.query_params.get("game_id")
        if raw_game_id is not None:
            try:
                viewer_game = get_game(connection, int(raw_game_id))
            except ValueError:
                viewer_game = None
            if viewer_game is None or viewer_game.tournament_id != tournament.id:
                raise HTTPException(status_code=404, detail="game not found")
        else:
            viewer_game = web_app._tournament_viewer_game(active_games)
        viewer_moves = (
            web_app.list_moves(connection, viewer_game.id)
            if viewer_game and not cross_table
            else ()
        )
        viewer_locked = bool(
            request.query_params.get("game_id") is not None
            and viewer_game is not None
            and viewer_game.status not in {"assigned", "live"}
        )
        chat_settings = get_chat_settings(connection)
        tournament_live = request.app.state.stream_hub.tournament_live(tournament.id)
        game_live = None if cross_table else web_app._live_for_game(
            tournament_live,
            viewer_game.id if viewer_game else None,
        )
        opening = (
            web_app._opening_view(connection, viewer_game.opening_id)
            if viewer_game and not cross_table
            else None
        )
        engine_data = web_app._engine_data(viewer_game, viewer_moves, opening)
        clocks = web_app._clock_data(viewer_moves)
        clock_state = web_app._persisted_clock_state(viewer_game, viewer_moves)
        if isinstance(game_live, dict):
            engine_data = web_app._merge_engine_data(
                engine_data,
                game_live.get("engine_data"),
            )
            clocks = web_app._merge_clock_data(clocks, game_live.get("clocks"))
            if isinstance(game_live.get("clock_state"), dict):
                clock_state = game_live["clock_state"]
        return _json(
            {
                "tournament": tournament,
                "spectator_count": request.app.state.stream_hub.tournament_spectator_count(
                    tournament.id
                ),
                "estimate": tournament_overview["estimate"],
                "games": [
                    web_app._game_payload(game, engines, live=True)
                    for game in games
                ],
                "active_games": [
                    web_app._game_payload(game, engines, live=True)
                    for game in active_games
                ],
                "cross_table_games": (
                    web_app._cross_table_games_payload(
                        connection,
                        active_games,
                        engines,
                        tournament_live,
                    )
                    if cross_table
                    else []
                ),
                "game_pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": total_games,
                    "pages": max(1, (total_games + page_size - 1) // page_size),
                },
                "engines": engines,
                "viewer_game": (
                    web_app._game_payload(viewer_game, engines, live=True)
                    if viewer_game
                    else None
                ),
                "viewer_moves": web_app._move_payloads(viewer_moves, opening),
                "viewer_locked": viewer_locked,
                "engine_data": engine_data,
                "clocks": clocks,
                "clock_state": clock_state,
                "standings": web_app._standings(
                    connection,
                    tournament,
                    engines,
                ),
                "rating_summaries": (
                    web_app._tournament_rating_summaries(connection, tournament.id)
                    if tournament.status == "finished"
                    else []
                ),
                "settings": _settings_rows(web_app._settings_view(connection, tournament)),
                "engine_hardware": web_app._engine_hardware_view(connection, tournament),
                "chat_messages": (
                    list_chat_messages(
                        connection,
                        limit=None,
                        tournament_id=tournament_id,
                        system=False,
                    )
                    + list_chat_messages(
                        connection,
                        limit=100,
                        tournament_id=tournament_id,
                        system=True,
                    )
                ),
                "chat_settings": chat_settings,
                "opening": opening,
            }
        )

    @app.get("/api/ratings")
    def public_ratings(
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        rating_lists = list_rating_lists(connection)
        raw_id = request.query_params.get("rating_list_id")
        try:
            selected_id = int(raw_id) if raw_id else None
        except ValueError:
            selected_id = None
        rating_list = next(
            (item for item in rating_lists if item.id == selected_id),
            rating_lists[0] if rating_lists else None,
        )
        opponent_ids_by_engine: dict[str, list[int]] = {}
        if rating_list is not None:
            for row in connection.execute(
                """
                SELECT DISTINCT engine_id, opponent_engine_id
                FROM rating_list_history
                WHERE rating_list_id = ?
                ORDER BY engine_id, opponent_engine_id
                """,
                (rating_list.id,),
            ):
                opponent_ids_by_engine.setdefault(str(row["engine_id"]), []).append(
                    int(row["opponent_engine_id"])
                )
        return _json(
            {
                "rating_list": rating_list,
                "rating_lists": rating_lists,
                "ratings": list_rating_rows(connection, rating_list.id) if rating_list else [],
                "filter_options": {
                    "opponent_ids_by_engine": opponent_ids_by_engine,
                },
            }
        )

    @app.get("/api/engines")
    def public_engines(
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        families = list_engine_families(connection)
        versions = list_engine_records(connection)
        badges_by_family = list_engine_badges(connection)
        versions_by_family = {family.id: [] for family in families}
        for version in versions:
            versions_by_family.setdefault(version.engine_id, []).append(version)
        result_summaries = list_engine_result_summaries(connection)
        engine_rows = []
        for family in families:
            family_versions = versions_by_family[family.id]
            if not family_versions:
                continue
            latest = family_versions[0]
            records = [
                result_summaries.get(
                    version.id,
                    {"wins": 0, "draws": 0, "losses": 0, "games": 0},
                )
                for version in family_versions
            ]
            engine_rows.append(
                {
                    "id": family.id,
                    "name": family.name,
                    "author": family.author,
                    "active": family.active and any(version.active for version in family_versions),
                    "latest_version_id": latest.id,
                    "latest_version": latest.version,
                    "created_at": latest.created_at,
                    "version_count": len(family_versions),
                    "badges": badges_by_family.get(family.id, ()),
                    "versions": [
                        {
                            "id": version.id,
                            "version": version.version,
                            "distribution": version.distribution,
                            "source_kind": version.source_kind,
                            "source_ref": version.source_ref,
                            "repository_full_name": version.repository_full_name,
                            "active": version.active,
                            "created_at": version.created_at,
                        }
                        for version in family_versions
                    ],
                    "record": {
                        key: sum(record[key] for record in records)
                        for key in ("wins", "draws", "losses", "games")
                    },
                }
            )
        completed_games = sum(
            summary["games"] for summary in result_summaries.values()
        ) // 2
        return _json(
            {
                "engines": engine_rows,
                "stats": {
                    "families": len(engine_rows),
                    "versions": len(versions),
                    "available": sum(1 for engine in engine_rows if engine["active"]),
                    "games": completed_games,
                },
            }
        )

    @app.get("/api/engines/{engine_id}")
    def public_engine(
        engine_id: int,
        result: Literal["win", "draw", "loss"] | None = Query(default=None),
        rating_list_id: int | None = Query(default=None, gt=0),
        opponent_id: int | None = Query(default=None, gt=0),
        side: Literal["white", "black"] | None = Query(default=None),
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        engine = get_engine_record(connection, engine_id)
        if engine is None:
            raise HTTPException(status_code=404, detail="Engine not found.")
        family = get_engine_family(connection, engine.engine_id)
        engine_records = list_engine_records(connection)
        versions = tuple(
            version for version in engine_records
            if version.engine_id == engine.engine_id
        )
        games = list_engine_games(
            connection,
            engine_id,
            result_filter=result,
            rating_list_id=rating_list_id,
            opponent_id=opponent_id,
            side_filter=side,
        )
        return _json(
            {
                "engine": engine,
                "family": family,
                "versions": versions,
                "games": games,
                "engines": {
                    version.id: web_app._engine_display_name(version.name, version.version)
                    for version in engine_records
                },
                "engine_options": [
                    {
                        "id": version.id,
                        "engine_id": version.engine_id,
                        "name": version.name,
                        "author": version.author,
                        "version": version.version,
                        "distribution": version.distribution,
                        "source_kind": version.source_kind,
                        "active": version.active,
                    }
                    for version in engine_records
                ],
                "record": engine_result_summary(connection, engine_id),
                "filter_options": engine_game_filter_options(connection, engine_id),
                "ratings": _public_engine_ratings(connection, engine_id),
                "badges": list_engine_badges(connection).get(engine.engine_id, ()),
            }
        )

    @app.get("/api/pgn")
    def public_pgn_export(
        request: Request,
        game_id: int | None = Query(default=None, gt=0),
        tournament_id: int | None = Query(default=None, gt=0),
        rating_list_id: int | None = Query(default=None, gt=0),
        engine_id: int | None = Query(default=None, gt=0),
        opponent_id: int | None = Query(default=None, gt=0),
        side: Literal["white", "black"] | None = Query(default=None),
        result: Literal["win", "draw", "loss"] | None = Query(default=None),
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            filters = PgnExportFilters(
                game_id=game_id,
                tournament_id=tournament_id,
                rating_list_id=rating_list_id,
                engine_id=engine_id,
                opponent_engine_id=opponent_id,
                color=side,
                result=result,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        tournament = None
        if tournament_id is not None:
            tournament = get_tournament(connection, tournament_id)
            if tournament is None or tournament.status == "draft":
                raise HTTPException(status_code=404, detail="Tournament not found.")

        rating_list = None
        if rating_list_id is not None:
            rating_list = get_rating_list(connection, rating_list_id)
            if rating_list is None:
                raise HTTPException(status_code=404, detail="Rating list not found.")

        engine = None
        if engine_id is not None:
            engine = get_engine_record(connection, engine_id)
            if engine is None:
                raise HTTPException(status_code=404, detail="Engine not found.")
        if opponent_id is not None and get_engine_record(connection, opponent_id) is None:
            raise HTTPException(status_code=404, detail="Opponent engine not found.")

        game = None
        if game_id is not None:
            game = get_game(connection, game_id)
            if game is None:
                raise HTTPException(status_code=404, detail="Game not found.")
            game_tournament = get_tournament(connection, game.tournament_id)
            if game_tournament is None or game_tournament.status == "draft":
                raise HTTPException(status_code=404, detail="Game not found.")

        if not pgn_export_exists(connection, filters):
            raise HTTPException(
                status_code=409,
                detail="No completed games match these PGN filters.",
            )

        if game is not None:
            filename = f"cope-game-{game.id}.pgn"
        else:
            filename_parts = []
            if tournament is not None:
                filename_parts.append(tournament.name)
            if rating_list is not None:
                filename_parts.append(rating_list.name)
            if engine is not None:
                filename_parts.append(f"{engine.name}-{engine.version}")
            if result is not None:
                filename_parts.append(
                    {"win": "wins", "draw": "draws", "loss": "losses"}[result]
                )
            if opponent_id is not None:
                filename_parts.append(f"versus-{opponent_id}")
            if side is not None:
                filename_parts.append(f"as-{side}")
            filename = safe_pgn_filename(
                "-".join(filename_parts) if filename_parts else "cope-all-games",
                "cope-games",
            )

        database_url = request.app.state.db_path

        def content():
            export_connection = connect_database(database_url, check_same_thread=False)
            try:
                yield from iter_pgn_export(export_connection, filters)
            finally:
                export_connection.close()

        return StreamingResponse(
            content(),
            media_type="application/x-chess-pgn; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/api/tournaments/{tournament_id}/chat")
    async def public_chat(
        tournament_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        form = await read_form(request)

        def create_message():
            web_app._require_public_chat_tournament(connection, tournament_id)
            return web_app._create_chat_message_from_form(
                connection,
                form,
                tournament_id=tournament_id,
            )

        message = await asyncio.to_thread(create_message)
        if message is not None:
            web_app._publish_chat_message(request, tournament_id, message)
        return _json(
            {"message": message},
            status_code=201,
        )

    # ------------------------------------------------------------------
    # Admin reads and writes
    # ------------------------------------------------------------------

    @app.get("/api/admin/dashboard")
    def admin_dashboard(connection: sqlite3.Connection = Depends(web_app._database)):
        tournaments = list_tournaments(connection)
        event_tournament_ids = web_app._puzzle_gauntlet_tournament_ids(connection)
        return _json(
            {
                "workers": web_app._worker_admin_rows(connection, limit=20),
                "live_games": list_games_by_status(connection, "live", limit=8),
                "engines": web_app._engine_names(connection),
                "db_stats": database_stats(connection),
                "running_tournaments": [
                    tournament
                    for tournament in tournaments
                    if tournament.status in {"scheduled", "running", "paused"}
                    and tournament.id not in event_tournament_ids
                ],
                "complete_tournaments": list_uncommitted_finished_tournaments(connection),
                "recent_games": list_games_by_status(connection, "finished", limit=6),
                "system": {
                    "version": app_version(),
                    "schema_version": database_schema_version(connection),
                    "services": list_service_heartbeats(connection),
                },
            }
        )

    @app.get("/api/admin/ratings")
    def admin_ratings(connection: sqlite3.Connection = Depends(web_app._database)):
        return _json(_admin_ratings_payload(connection))

    @app.get("/api/admin/events")
    def admin_events(connection: sqlite3.Connection = Depends(web_app._database)):
        modules = event_modules()
        return _json(
            {
                "events": [
                    _event_summary_payload(connection, event, admin=True)
                    for event in list_events(connection)
                ],
                "statuses": [
                    "draft",
                    "announced",
                    "scheduled",
                    "live",
                    "intermission",
                    "postponed",
                    "completed",
                    "cancelled",
                ],
                "registered_modules": [
                    {
                        "key": module.key,
                        "label": module.label,
                        "version": module.version,
                    }
                    for module in modules.values()
                ],
            }
        )

    @app.get("/api/admin/events/{event_id}")
    def admin_event(
        event_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = get_event(connection, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found.")
        return _json(_event_detail_payload(connection, event, admin=True))

    @app.delete("/api/admin/events/{event_id}")
    def admin_delete_event(
        event_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = get_event(connection, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found.")
        linked_tournaments = [
            get_tournament(connection, int(row["tournament_id"]))
            for row in connection.execute(
                """
                SELECT tournament_id FROM engine_relay_fixtures WHERE event_id = ?
                UNION
                SELECT tournament_id FROM puzzle_gauntlet_events
                WHERE event_id = ? AND tournament_id IS NOT NULL
                """,
                (event_id, event_id),
            )
        ]
        gauntlet = connection.execute(
            "SELECT opening_suite_id FROM puzzle_gauntlet_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        active = [
            tournament
            for tournament in linked_tournaments
            if tournament is not None and tournament.status in {"running", "paused"}
        ]
        if active:
            raise HTTPException(
                status_code=409,
                detail="Abort active event tournaments before deleting this event.",
            )
        try:
            for tournament in linked_tournaments:
                if tournament is not None and (
                    tournament.status in {"draft", "scheduled"} or gauntlet is not None
                ):
                    delete_tournament(connection, tournament.id)
            delete_event(connection, event_id)
            if gauntlet is not None:
                delete_opening_suite(connection, int(gauntlet["opening_suite_id"]))
            connection.commit()
        except (sqlite3.IntegrityError, ValueError) as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        request.app.state.stream_hub.link_event_tournaments(event_id, ())
        _publish_admin_change(web_app, request)
        return _json({"message": f"{event.title} deleted."})

    @app.post("/api/admin/events/{event_id}/reset")
    def admin_reset_event(
        event_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = get_event(connection, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found.")
        linked_tournaments = [
            get_tournament(connection, int(row["tournament_id"]))
            for row in connection.execute(
                """
                SELECT tournament_id FROM engine_relay_fixtures WHERE event_id = ?
                UNION
                SELECT tournament_id FROM puzzle_gauntlet_events
                WHERE event_id = ? AND tournament_id IS NOT NULL
                """,
                (event_id, event_id),
            )
        ]
        active = [
            tournament
            for tournament in linked_tournaments
            if tournament is not None and tournament.status in {"running", "paused"}
        ]
        if active:
            raise HTTPException(
                status_code=409,
                detail="Abort active event tournaments before resetting this event.",
            )
        try:
            for tournament in linked_tournaments:
                if tournament is not None:
                    delete_tournament(connection, tournament.id)
            reset_event(connection, event_id)
            if event.handler_key == "puzzle-gauntlet":
                from cope.events.puzzle_gauntlet import reset_puzzle_gauntlet

                reset_puzzle_gauntlet(connection, event_id)
            connection.commit()
        except (sqlite3.IntegrityError, ValueError) as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        request.app.state.stream_hub.link_event_tournaments(event_id, ())
        request.app.state.stream_hub.publish(
            f"event.{event_id}",
            "event.changed",
            {"event_id": event_id},
            source="web",
        )
        _publish_admin_change(web_app, request)
        return _json({"message": f"{event.title} reset to a new draft."})

    @app.post("/api/admin/event-modules/{module_key}/provision")
    def admin_provision_event_module(
        module_key: str,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        module = get_event_module(module_key)
        if module is None:
            raise HTTPException(status_code=404, detail="Event module not found.")
        existing = next(
            (event for event in list_events(connection) if event.handler_key == module.key),
            None,
        )
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"{module.label} has already been created.",
            )
        try:
            event = provision_event_module(connection, module.key)
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"{module.label} could not be created because its event already exists.",
            ) from exc
        except (RuntimeError, ValueError) as exc:
            connection.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "id": event.id,
                "event": _event_summary_payload(connection, event, admin=True),
                "message": f"{event.title} created.",
            },
            status_code=201,
        )

    @app.post("/api/admin/rating-lists")
    def admin_create_rating_list(
        payload: RatingListPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            rating_list_id = create_rating_list(connection, payload.name)
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail="A rating list with that name already exists.") from exc
        _publish_admin_change(web_app, request)
        return _json({"id": rating_list_id, "message": "Rating list created."}, status_code=201)

    @app.get("/api/admin/rating-lists/{rating_list_id}")
    def admin_rating_list(
        rating_list_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        rating_list = get_rating_list(connection, rating_list_id)
        if rating_list is None:
            raise HTTPException(status_code=404, detail="Rating list not found.")
        return _json({
            "rating_list": rating_list,
            "ratings": list_rating_rows(connection, rating_list_id),
            "engine_versions": [
                {
                    "id": int(version["id"]),
                    "name": str(version["name"]),
                    "version": str(version["version"]),
                }
                for version in connection.execute(
                    """
                    SELECT version.id, engine.name, version.version
                    FROM engine_versions version
                    JOIN engines engine ON engine.id = version.engine_id
                    ORDER BY engine.name, version.created_at DESC, version.id DESC
                    """
                )
            ],
            "tournaments": _admin_rating_list_tournaments(connection, rating_list_id),
        })

    @app.delete("/api/admin/rating-lists/{rating_list_id}")
    def admin_delete_rating_list(
        rating_list_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_rating_list(connection, rating_list_id) is None:
            raise HTTPException(status_code=404, detail="Rating list not found.")
        delete_rating_list(connection, rating_list_id)
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({"message": "Rating list deleted."})

    @app.post("/api/admin/rating-lists/{rating_list_id}/calculate")
    def admin_calculate_rating_list(
        rating_list_id: int,
        payload: RatingCalculationPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_rating_list(connection, rating_list_id) is None:
            raise HTTPException(status_code=404, detail="Rating list not found.")
        try:
            update_rating_list_anchor(
                connection,
                rating_list_id,
                payload.anchor_engine_id,
                payload.anchor_elo,
            )
            result = recalculate_ratings(connection, rating_list_ids=(rating_list_id,))
            connection.commit()
        except (RatingCommitError, ValueError) as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "result": jsonable_encoder(result),
                "message": (
                    f"Calculated {result.engines_updated} engine ratings from "
                    f"{result.games_applied} games in {result.tournaments_applied} tournaments."
                ),
            }
        )

    @app.post("/api/admin/ratings/recalculate")
    def admin_recalculate_ratings(
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            result = recalculate_ratings(connection)
            connection.commit()
        except RatingCommitError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "result": jsonable_encoder(result),
                "message": (
                    f"Recalculated {result.engines_updated} engine ratings from "
                    f"{result.games_applied} games in {result.tournaments_applied} tournaments."
                ),
            }
        )

    @app.delete("/api/admin/rating-lists/{rating_list_id}/tournaments/{tournament_id}")
    def admin_uncommit_tournament_ratings(
        rating_list_id: int,
        tournament_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            uncommit_tournament_ratings(connection, tournament_id, rating_list_id)
            connection.commit()
        except RatingCommitError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "message": "Tournament uncommitted. Press Calculate to update the ratings.",
            }
        )

    @app.get("/api/admin/deployments")
    def admin_deployments(
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        build_settings = _cope_build_settings(connection)
        heartbeats = {
            item["service"]: item
            for item in list_service_heartbeats(connection)
        }
        jobs = list_deployment_jobs(connection, limit=25)
        targets_by_job = list_deployment_targets_for_jobs(
            connection,
            (job.id for job in jobs),
        )
        return _json(
            {
                "current_version": app_version(),
                "default_ref": build_settings.update_ref,
                "updater": heartbeats.get("updater"),
                "methods": [
                    {
                        "id": "web",
                        "label": "Web application",
                        "description": "Deploy same-schema website and API changes without touching game services.",
                        "scope": "Web only",
                        "impact": "Games continue",
                    },
                    {
                        "id": "platform",
                        "label": "Full platform",
                        "description": "Update services, workers, benchmarkers, and the database.",
                        "scope": "Entire fleet",
                        "impact": "Waits for active work",
                    },
                    {
                        "id": "dockerfiles",
                        "label": "Engine definitions",
                        "description": "Refresh engine Dockerfiles without restarting services.",
                        "scope": "Engine catalog",
                        "impact": "No restart",
                    },
                ],
                "dockerfile_pull": jsonable_encoder(latest_dockerfile_pull_job(connection)),
                "jobs": [
                    {
                        **jsonable_encoder(job),
                        "targets": jsonable_encoder(
                            targets_by_job.get(job.id, ())
                        ),
                    }
                    for job in jobs
                ],
            }
        )

    @app.post("/api/admin/deployments")
    def admin_create_deployment(
        payload: DeploymentPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        requested_ref = payload.ref or _cope_build_settings(connection).update_ref
        try:
            job_id = create_deployment_job(
                connection,
                requested_ref=requested_ref,
                scope=payload.scope,
            )
            connection.commit()
        except ValueError as error:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        _publish_admin_change(web_app, request)
        return _json(
            {
                "id": job_id,
                "message": (
                    f"Web update {job_id} queued for {requested_ref}."
                    if payload.scope == "web"
                    else f"Platform update {job_id} queued for {requested_ref}."
                ),
            },
            status_code=202,
        )

    @app.post("/api/admin/updates")
    def admin_create_update(
        payload: UpdatePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        requested_ref = payload.ref or _cope_build_settings(connection).update_ref
        try:
            if payload.method == "dockerfiles":
                job_id = create_dockerfile_pull_job(
                    connection,
                    requested_ref=requested_ref,
                )
                message = f"Engine definition update {job_id} queued for {requested_ref}."
            else:
                job_id = create_deployment_job(
                    connection,
                    requested_ref=requested_ref,
                    scope=payload.method,
                )
                label = "Web update" if payload.method == "web" else "Platform update"
                message = f"{label} {job_id} queued for {requested_ref}."
            connection.commit()
        except ValueError as error:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        _publish_admin_change(web_app, request)
        return _json(
            {
                "id": job_id,
                "method": payload.method,
                "message": message,
            },
            status_code=202,
        )

    @app.post("/api/admin/dockerfile-pulls")
    def admin_create_dockerfile_pull(
        payload: DeploymentPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        requested_ref = payload.ref or _cope_build_settings(connection).update_ref
        try:
            job_id = create_dockerfile_pull_job(
                connection,
                requested_ref=requested_ref,
            )
            connection.commit()
        except ValueError as error:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        _publish_admin_change(web_app, request)
        return _json(
            {
                "id": job_id,
                "message": f"Dockerfile pull {job_id} queued for {requested_ref}.",
            },
            status_code=202,
        )

    @app.get("/api/admin/deployments/{job_id}")
    def admin_deployment(
        job_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        job = get_deployment_job(connection, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Deployment not found.")
        return _json(
            {
                **jsonable_encoder(job),
                "targets": jsonable_encoder(
                    list_deployment_targets(connection, job.id)
                ),
            }
        )

    @app.get("/api/admin/tournaments/form")
    def admin_tournament_form(
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        return _json(_tournament_form_payload(web_app, request, connection))

    @app.get("/api/admin/tournaments")
    def admin_tournaments(
        status: str = "",
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        engines = web_app._engine_names(connection)
        estimator = TournamentEstimator(connection)
        event_tournament_ids = web_app._puzzle_gauntlet_tournament_ids(connection)
        available_tournaments = tuple(
            tournament
            for tournament in list_tournaments(connection)
            if tournament.id not in event_tournament_ids
        )
        tournaments = tuple(
            tournament
            for tournament in available_tournaments
            if _tournament_status_matches(tournament.status, status)
        )
        items = web_app._tournament_summaries(
            connection,
            tournaments,
            engines,
            estimator=estimator,
            include_completed_estimates=False,
            include_active_estimates=False,
        )
        return _json(
            {
                "tournaments": items,
                "status_filter": status,
                "running_count": sum(
                    tournament.status == "running"
                    for tournament in available_tournaments
                ),
                "paused_count": sum(
                    tournament.status == "paused"
                    for tournament in available_tournaments
                ),
                "statuses": [
                    "draft",
                    "scheduled",
                    "running",
                    "paused",
                    "finished",
                    "aborted",
                ],
            }
        )

    @app.post("/api/admin/tournaments/bulk-status")
    def admin_bulk_tournament_status(
        payload: TournamentBulkStatusPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        action = payload.action
        source = "running" if action == "pause" else "paused"
        target = "paused" if action == "pause" else "running"
        event_tournament_ids = web_app._puzzle_gauntlet_tournament_ids(connection)
        tournaments = tuple(
            tournament
            for tournament in list_tournaments(connection)
            if tournament.id not in event_tournament_ids
            and tournament.status == source
        )
        try:
            for tournament in tournaments:
                if action == "resume":
                    resume_paused_tournament_games(connection, tournament.id)
                set_tournament_status(connection, tournament.id, target)
            connection.commit()
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if tournaments:
            _publish_admin_change(web_app, request)
        count = len(tournaments)
        if count == 0:
            message = f"No {source} tournaments to {action}."
        elif action == "pause":
            noun = "tournament" if count == 1 else "tournaments"
            message = (
                f"Pause requested for {count} {noun}. "
                "Live games will freeze after their current move."
            )
        else:
            noun = "tournament" if count == 1 else "tournaments"
            message = f"Resumed {count} {noun} from their preserved games."
        return _json(
            {
                "action": action,
                "status": target,
                "changed": count,
                "message": message,
            }
        )

    @app.post("/api/admin/tournaments")
    def admin_create_tournament(
        payload: TournamentPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            config = _validated_tournament_config(connection, payload.config)
            tournament_id = create_tournament(connection, payload.name, config)
            connection.commit()
        except HTTPException:
            connection.rollback()
            raise
        except ValidationError as exc:
            connection.rollback()
            raise HTTPException(
                status_code=422,
                detail=[error["msg"] for error in exc.errors()],
            ) from exc
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise HTTPException(
                status_code=409,
                detail="Tournament data changed while the draft was being created. Reload the form and try again.",
            ) from exc
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            LOG.exception("tournament creation failed")
            raise HTTPException(
                status_code=503,
                detail="The database could not save the tournament. Try again.",
            ) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "id": tournament_id,
                "message": "Tournament draft created.",
            },
            status_code=201,
        )

    @app.get("/api/admin/tournaments/{tournament_id}")
    def admin_tournament(
        tournament_id: int,
        request: Request,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=100, ge=25, le=200),
        result_type: list[str] = Query(default=[]),
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if invalid_result_types := set(result_type) - ADMIN_GAME_RESULT_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown game result type: {sorted(invalid_result_types)[0]}",
            )
        tournament = _require_tournament(connection, tournament_id)
        engines = web_app._engine_names(connection)
        tournament_overview = web_app._tournament_summaries(
            connection,
            (tournament,),
            engines,
        )[0]
        total_games = count_games(
            connection,
            tournament.id,
            result_types=result_type,
        )
        payload: dict[str, Any] = {
            "tournament": tournament,
            "games": list_games_page(
                connection,
                tournament.id,
                page=page,
                page_size=page_size,
                result_types=result_type,
            ),
            "game_pagination": {
                "page": page,
                "page_size": page_size,
                "total": total_games,
                "pages": max(1, (total_games + page_size - 1) // page_size),
            },
            "game_summary": tournament_overview["summary"],
            "estimate": tournament_overview["estimate"],
            "engines": engines,
            "settings": _settings_rows(web_app._settings_view(connection, tournament)),
            "commits": list_tournament_rating_commits(connection, tournament.id),
            "rating_lists": list_rating_lists(connection),
            "actions": web_app.TOURNAMENT_ACTIONS.get(tournament.status, {}),
            "roster": _live_tournament_roster_payload(connection, tournament),
            "capabilities": {
                "editable": tournament.status == "draft",
                "schedulable": tournament.status in {"draft", "scheduled"},
                "unschedulable": tournament.status == "scheduled" and tournament.started_at is None,
                "concurrency_editable": tournament.status in {"running", "paused"},
                "deletable": tournament.status not in {"scheduled", "running"},
                "can_commit_ratings": (
                    tournament.status in {"finished", "aborted"}
                    and tournament.config.rated
                ),
            },
        }
        if tournament.status == "draft":
            payload["form"] = _tournament_form_payload(
                web_app,
                request,
                connection,
                tournament=tournament,
            )
        return _json(payload)

    @app.put("/api/admin/tournaments/{tournament_id}")
    def admin_update_tournament(
        tournament_id: int,
        payload: TournamentPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        tournament = _require_tournament(connection, tournament_id)
        if tournament.status not in {"draft", "running", "paused"}:
            raise HTTPException(
                status_code=409,
                detail="Only draft tournaments and the name or concurrency of running or paused tournaments can be edited here.",
            )
        try:
            name_changed = payload.name != tournament.name
            if tournament.status in {"running", "paused"}:
                unchanged_config = payload.config.model_dump(
                    mode="json",
                    exclude={"concurrency"},
                )
                current_config = tournament.config.model_dump(
                    mode="json",
                    exclude={"concurrency"},
                )
                if unchanged_config != current_config:
                    raise HTTPException(
                        status_code=409,
                        detail="Only the name and game concurrency can be changed while a tournament is running or paused.",
                    )
                update_tournament_name(connection, tournament_id, payload.name)
                set_tournament_concurrency(
                    connection,
                    tournament_id,
                    payload.config.concurrency,
                )
            else:
                config = _validated_tournament_config(connection, payload.config)
                update_tournament(connection, tournament_id, name=payload.name, config=config)
            connection.commit()
        except HTTPException:
            connection.rollback()
            raise
        except ValidationError as exc:
            connection.rollback()
            raise HTTPException(
                status_code=422,
                detail=[error["msg"] for error in exc.errors()],
            ) from exc
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise HTTPException(
                status_code=409,
                detail="Tournament data changed while the draft was being saved. Reload the form and try again.",
            ) from exc
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            LOG.exception("tournament update failed tournament_id=%s", tournament_id)
            raise HTTPException(
                status_code=503,
                detail="The database could not save the tournament. Try again.",
            ) from exc
        _publish_admin_change(web_app, request)
        if tournament.status in {"running", "paused"}:
            concurrency_changed = payload.config.concurrency != tournament.config.concurrency
            if name_changed and concurrency_changed:
                message = "Tournament name and concurrency updated."
            elif name_changed:
                message = "Tournament renamed."
            else:
                message = "Tournament concurrency updated."
        else:
            message = "Tournament updated."
        return _json({"id": tournament_id, "message": message})

    @app.put("/api/admin/tournaments/{tournament_id}/name")
    def admin_tournament_name(
        tournament_id: int,
        payload: TournamentNamePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _require_tournament(connection, tournament_id)
        update_tournament_name(connection, tournament_id, payload.name)
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({"id": tournament_id, "message": "Tournament renamed."})

    @app.post("/api/admin/tournaments/{tournament_id}/schedule")
    @app.patch("/api/admin/tournaments/{tournament_id}/schedule")
    def admin_schedule_tournament(
        tournament_id: int,
        payload: TournamentSchedulePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        tournament = _require_tournament(connection, tournament_id)
        if tournament.status not in {"draft", "scheduled"} or tournament.started_at is not None:
            raise HTTPException(
                status_code=409,
                detail="Only a draft or unstarted scheduled tournament can be scheduled.",
            )
        try:
            preparation = materialize_tournament_schedule(connection, tournament)
            scheduled = schedule_tournament(
                connection,
                tournament_id,
                payload.scheduled_start_at.isoformat(timespec="seconds"),
            )
            connection.commit()
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            LOG.exception("tournament scheduling failed tournament_id=%s", tournament_id)
            raise HTTPException(
                status_code=503,
                detail="The database could not schedule the tournament. Try again.",
            ) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "status": scheduled.status,
                "scheduled_start_at": scheduled.scheduled_start_at,
                "created_games": preparation.created_games,
                "message": "Tournament scheduled.",
            }
        )

    @app.delete("/api/admin/tournaments/{tournament_id}/schedule")
    def admin_unschedule_tournament(
        tournament_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _require_tournament(connection, tournament_id)
        try:
            unschedule_tournament(connection, tournament_id)
            connection.commit()
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            LOG.exception("tournament unscheduling failed tournament_id=%s", tournament_id)
            raise HTTPException(
                status_code=503,
                detail="The database could not return the tournament to draft. Try again.",
            ) from exc
        _publish_admin_change(web_app, request)
        return _json({"status": "draft", "message": "Tournament returned to draft."})

    @app.post("/api/admin/tournaments/{tournament_id}/start")
    def admin_start_tournament(
        tournament_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            preparation = start_tournament(connection, tournament_id)
            connection.commit()
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            LOG.exception("tournament start failed tournament_id=%s", tournament_id)
            raise HTTPException(
                status_code=503,
                detail="The database could not start the tournament. Try again.",
            ) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "status": "running",
                "created_games": preparation.created_games,
                "message": "Tournament started.",
            }
        )

    @app.post("/api/admin/tournaments/{tournament_id}/participants")
    def admin_add_tournament_participant(
        tournament_id: int,
        payload: TournamentParticipantPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        tournament = _require_tournament(connection, tournament_id)
        _ensure_tournament_games_mutable(connection, tournament_id)
        _validate_live_participant_engine(connection, payload.engine_id)
        try:
            result = add_running_tournament_participant(
                connection,
                tournament_id,
                payload.engine_id,
            )
            connection.commit()
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise HTTPException(
                status_code=409,
                detail="The tournament roster changed. Reload the page and try again.",
            ) from exc
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            LOG.exception(
                "live tournament participant addition failed tournament_id=%s engine_id=%s",
                tournament_id,
                payload.engine_id,
            )
            raise HTTPException(
                status_code=503,
                detail="The database could not add the participant. Try again.",
            ) from exc
        _publish_admin_change(web_app, request)
        count = result.scheduled_games
        return _json(
            {
                "message": (
                    f"Participant added and {count} game{'s' if count != 1 else ''} scheduled."
                ),
                "scheduled_games": count,
                "participant_count": len(result.tournament.config.participants),
            }
        )

    @app.delete("/api/admin/tournaments/{tournament_id}/participants/{engine_id}")
    def admin_remove_tournament_participant(
        tournament_id: int,
        engine_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        tournament = _require_tournament(connection, tournament_id)
        _ensure_tournament_games_mutable(connection, tournament_id)
        try:
            result = remove_running_tournament_participant(
                connection,
                tournament_id,
                engine_id,
            )
            connection.commit()
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise HTTPException(
                status_code=409,
                detail="The tournament roster changed. Reload the page and try again.",
            ) from exc
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            LOG.exception(
                "live tournament participant removal failed tournament_id=%s engine_id=%s",
                tournament_id,
                engine_id,
            )
            raise HTTPException(
                status_code=503,
                detail="The database could not remove the participant. Try again.",
            ) from exc
        _publish_admin_change(web_app, request)
        invalidated = result.invalidated
        active = invalidated.assigned + invalidated.live
        total = len(invalidated.game_ids)
        return _json(
            {
                "message": (
                    f"Participant removed and {total} game{'s' if total != 1 else ''} invalidated."
                ),
                "invalidated_games": total,
                "finished_games": invalidated.finished,
                "pending_games": invalidated.pending,
                "active_games": active,
                "scheduled_games": result.scheduled_games,
                "participant_count": len(result.tournament.config.participants),
            }
        )

    @app.post("/api/admin/tournaments/{tournament_id}/games/{game_id}/replay")
    def admin_replay_tournament_game(
        tournament_id: int,
        game_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        tournament = _require_tournament(connection, tournament_id)
        _require_tournament_game(connection, tournament_id, game_id)
        _ensure_tournament_games_mutable(connection, tournament_id)
        try:
            reopened = replay_game(connection, tournament_id, game_id)
            connection.commit()
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            LOG.exception(
                "game replay failed tournament_id=%s game_id=%s",
                tournament_id,
                game_id,
            )
            raise HTTPException(
                status_code=503,
                detail="The database could not reset the game. Try again.",
            ) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "message": (
                    f"Game reset to pending and {tournament.name} reopened."
                    if reopened
                    else "Game reset to pending."
                )
            }
        )

    @app.post("/api/admin/tournaments/{tournament_id}/games/{game_id}/invalidate")
    def admin_invalidate_tournament_game_pair(
        tournament_id: int,
        game_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        tournament = _require_tournament(connection, tournament_id)
        _require_tournament_game(connection, tournament_id, game_id)
        _ensure_tournament_games_mutable(connection, tournament_id)
        try:
            invalidated = invalidate_game_pair(connection, tournament_id, game_id)
            if tournament.status == "finished":
                announce_tournament_finished(connection, tournament)
            connection.commit()
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            LOG.exception(
                "game invalidation failed tournament_id=%s game_id=%s",
                tournament_id,
                game_id,
            )
            raise HTTPException(
                status_code=503,
                detail="The database could not invalidate the games. Try again.",
            ) from exc
        _publish_admin_change(web_app, request)
        count = len(invalidated)
        return _json(
            {
                "message": f"{count} game{'s' if count != 1 else ''} invalidated."
            }
        )

    @app.post("/api/admin/tournaments/{tournament_id}/status")
    def admin_tournament_status(
        tournament_id: int,
        payload: TournamentStatusPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        tournament = _require_tournament(connection, tournament_id)
        action = payload.action
        allowed = web_app.TOURNAMENT_ACTIONS.get(tournament.status, {})
        if action not in allowed:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot {action or 'change'} a {tournament.status} tournament.",
            )
        target = allowed[action]
        try:
            if action == "restore":
                restore_tournament(connection, tournament_id)
            else:
                if action == "resume":
                    resume_paused_tournament_games(connection, tournament_id)
                set_tournament_status(connection, tournament_id, target)
            connection.commit()
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "status": target,
                "message": (
                    "Tournament restored and paused."
                    if action == "restore"
                    else "Pause requested. Live games will freeze after their current move."
                    if action == "pause"
                    else "Tournament resumed from its preserved games."
                    if action == "resume"
                    else f"Tournament {target}."
                ),
            }
        )

    @app.post("/api/admin/tournaments/{tournament_id}/commit-results")
    def admin_commit_tournament_results(
        tournament_id: int,
        payload: RatingCommitPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        tournament = _require_tournament(connection, tournament_id)
        if tournament.status not in {"finished", "aborted"}:
            raise HTTPException(
                status_code=409,
                detail="Tournament is not finished or aborted.",
            )
        try:
            requested = request_tournament_rating_commit(
                connection, tournament, payload.rating_list_ids
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json(
            {
                "message": (
                    f"Rating commit requested for {requested} list{'s' if requested != 1 else ''}."
                    if requested
                    else "Those rating commits are already queued or applied."
                )
            }
        )

    @app.delete("/api/admin/tournaments/{tournament_id}")
    def admin_delete_tournament(
        tournament_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        tournament = _require_tournament(connection, tournament_id)
        if tournament.status in {"scheduled", "running"}:
            raise HTTPException(
                status_code=409,
                detail="Abort the tournament before deleting it.",
            )
        try:
            delete_tournament(connection, tournament_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({"message": "Tournament deleted."})

    @app.get("/api/admin/settings")
    def admin_settings(
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        hosts = list_git_hosts(connection)
        build_settings = _cope_build_settings(connection)
        platform_settings = get_platform_settings(connection)
        return _json(
            {
                "can_manage_tokens": web_app._admin_session_role(request) == "admin",
                "cope_build": jsonable_encoder(build_settings),
                "platform": jsonable_encoder(platform_settings),
                "git_hosts": [
                    {
                        "id": host.id,
                        "name": host.name,
                        "provider": host.provider,
                        "base_url": host.base_url,
                        "api_url": host.api_url,
                        "access_token_configured": bool(host.access_token),
                        "enabled": host.enabled,
                        "created_at": host.created_at,
                    }
                    for host in hosts
                ],
            }
        )

    @app.put("/api/admin/settings/cope-build")
    def admin_update_cope_build_settings(
        payload: CopeBuildSettingsPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        active_deployment = connection.execute(
            """
            SELECT id FROM deployment_jobs
            WHERE status NOT IN ('succeeded', 'failed')
            LIMIT 1
            """
        ).fetchone()
        active_dockerfile_pull = connection.execute(
            """
            SELECT id FROM dockerfile_pull_jobs
            WHERE status NOT IN ('succeeded', 'failed')
            LIMIT 1
            """
        ).fetchone()
        if active_deployment is not None or active_dockerfile_pull is not None:
            raise HTTPException(
                status_code=409,
                detail="Wait for the active update to finish before changing build settings.",
            )
        settings = CopeBuildSettingsRecord(
            repository_url=payload.repository_url,
            update_ref=payload.update_ref,
        )
        update_cope_build_settings(connection, settings)
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json(
            {
                "cope_build": jsonable_encoder(settings),
                "message": "Cope build settings updated.",
            }
        )

    @app.put("/api/admin/settings/platform")
    def admin_update_platform_settings(
        payload: PlatformSettingsPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        settings = PlatformSettingsRecord(
            privatise_platform=payload.privatise_platform,
        )
        update_platform_settings(connection, settings)
        connection.commit()
        web_app._set_platform_privacy_cache(request.app, settings.privatise_platform)
        _publish_admin_change(web_app, request)
        return _json(
            {
                "platform": jsonable_encoder(settings),
                "message": (
                    "Platform access is now private."
                    if settings.privatise_platform
                    else "Public platform access restored."
                ),
            }
        )

    @app.get("/api/admin/settings/access-tokens/{role}")
    def access_token(role: Literal["admin", "manager"], request: Request):
        if web_app._admin_session_role(request) != "admin":
            raise HTTPException(status_code=403, detail="The admin token is required.")
        token = web_app._admin_token(request) if role == "admin" else web_app._manager_token(request)
        if not token:
            raise HTTPException(status_code=503, detail=f"The {role} token is not configured.")
        response = _json({"token": token})
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.post("/api/admin/settings/access-tokens/{role}")
    def rotate_access_token(
        role: Literal["admin", "manager"],
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if web_app._admin_session_role(request) != "admin":
            raise HTTPException(status_code=403, detail="The admin token is required.")
        token = secrets.token_urlsafe(48)
        connection.execute(
            """
            INSERT INTO admin_access_tokens (role, token, rotated_at)
            VALUES (?, ?, ?)
            ON CONFLICT (role) DO UPDATE SET
              token = EXCLUDED.token,
              rotated_at = EXCLUDED.rotated_at
            """,
            (role, token, datetime.now(UTC).isoformat()),
        )
        connection.commit()
        setattr(request.app.state, f"{role}_token", token)
        request.app.state.access_tokens_loaded = True
        if role == "admin":
            nonce = secrets.token_urlsafe(32)
            csrf_token = web_app._csrf_for_nonce(token, nonce)
        else:
            nonce = None
            csrf_token = web_app._csrf_token(request, web_app._admin_token(request))
        response = _json({"csrf_token": csrf_token, "message": f"{role.title()} token rotated."})
        if nonce is not None:
            response.set_cookie(
                "cope_admin_session",
                web_app._signed_value(token, nonce),
                httponly=True,
                secure=web_app._request_is_secure(request),
                samesite="lax",
                max_age=web_app.ADMIN_SESSION_MAX_AGE_SECONDS,
            )
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.get("/api/admin/benchmarks/manager")
    def admin_benchmark_manager(
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        queue_rows = connection.execute(
            """
            SELECT job.*, benchmarker.label AS benchmarker_label,
                   benchmarker.status AS benchmarker_status
            FROM benchmark_jobs job
            LEFT JOIN benchmarkers benchmarker ON benchmarker.id = job.benchmarker_id
            WHERE job.status IN ('running', 'queued')
            ORDER BY CASE job.status
                       WHEN 'running' THEN 0
                       ELSE 1
                     END,
                     job.scheduled_at, job.id
            LIMIT 500
            """
        ).fetchall()
        jobs = [
            {
                "id": int(row["id"]),
                "engine_version_id": row["engine_version_id"],
                "engine_name": str(row["engine_name"]),
                "engine_version": str(row["engine_version"]),
                "build_hash": str(row["build_hash"]),
                "hardware_key": str(row["hardware_key"]),
                "status": str(row["status"]),
                "attempt": int(row["attempt"]),
                "scheduled_at": str(row["scheduled_at"]),
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "error": str(row["error"]),
                "activity": _benchmark_activity(str(row["output"])),
                "benchmarker": None if row["benchmarker_id"] is None else {
                    "id": int(row["benchmarker_id"]),
                    "label": row["benchmarker_label"],
                    "status": row["benchmarker_status"],
                },
            }
            for row in queue_rows
        ]
        failure_rows = connection.execute(
            """
            SELECT job.*, benchmarker.label AS benchmarker_label,
                   benchmarker.status AS benchmarker_status
            FROM benchmark_jobs job
            LEFT JOIN benchmarkers benchmarker ON benchmarker.id = job.benchmarker_id
            WHERE job.status = 'failed'
            ORDER BY job.finished_at DESC, job.id DESC
            LIMIT 500
            """
        ).fetchall()
        failures = [
            {
                "id": int(row["id"]),
                "engine_version_id": row["engine_version_id"],
                "engine_name": str(row["engine_name"]),
                "engine_version": str(row["engine_version"]),
                "build_hash": str(row["build_hash"]),
                "hardware_key": str(row["hardware_key"]),
                "status": str(row["status"]),
                "attempt": int(row["attempt"]),
                "scheduled_at": str(row["scheduled_at"]),
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "error": str(row["error"]),
                "activity": _benchmark_activity(str(row["output"])),
                "benchmarker": None if row["benchmarker_id"] is None else {
                    "id": int(row["benchmarker_id"]),
                    "label": row["benchmarker_label"],
                    "status": row["benchmarker_status"],
                },
            }
            for row in failure_rows
        ]
        running_by_benchmarker = {
            job["benchmarker"]["id"]: job
            for job in jobs
            if job["status"] == "running" and job["benchmarker"] is not None
        }
        benchmarkers = []
        for benchmarker in list_benchmarkers(connection):
            payload = web_app._benchmarker_admin_payload(benchmarker)
            payload["work"] = running_by_benchmarker.get(benchmarker.id)
            benchmarkers.append(payload)
        queued_build_hashes = {
            str(row["build_hash"])
            for row in connection.execute(
                "SELECT DISTINCT build_hash FROM benchmark_jobs WHERE status IN ('running', 'queued')"
            )
        }
        engines = [
            {
                "id": version.id,
                "name": version.name,
                "version": version.version,
                "build_hash": version.build_hash,
                "dockerfile_ready": bool(version.dockerfile_path and version.dockerfile),
            }
            for version in list_engine_records(connection)
            if version.version.strip()
            and version.distribution == "managed"
            and not version.benchmark_current
            and version.build_hash not in queued_build_hashes
        ]
        return _json(
            {
                "benchmarkers": benchmarkers,
                "queue": jobs,
                "failures": failures,
                "engines_needing_benchmark": engines,
            }
        )

    @app.get("/api/admin/engine-dockerfiles")
    def admin_engine_dockerfiles():
        try:
            files = list_engine_dockerfiles()
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail="The data/engines directory could not be read.",
            ) from exc
        return _json({"dockerfiles": files})

    @app.get("/api/admin/engine-dockerfiles/content")
    def admin_engine_dockerfile_content(path: str = Query(min_length=1, max_length=500)):
        return _json({"path": path, "content": _load_engine_dockerfile(path)})

    @app.post("/api/admin/git-hosts")
    def admin_create_git_host(
        payload: GitHostPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            host_id = create_git_host(
                connection,
                name=payload.name,
                provider=payload.provider,
                base_url=payload.base_url,
                api_url=payload.api_url,
                access_token=(payload.access_token or "").strip(),
                enabled=payload.enabled,
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail=web_app._friendly_error(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json({"id": host_id, "message": "Git host added."}, status_code=201)

    @app.put("/api/admin/git-hosts/{host_id}")
    def admin_update_git_host(
        host_id: int,
        payload: GitHostPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_git_host(connection, host_id) is None:
            raise HTTPException(status_code=404, detail="Git host not found.")
        try:
            update_git_host(
                connection,
                host_id,
                name=payload.name,
                provider=payload.provider,
                base_url=payload.base_url,
                api_url=payload.api_url,
                access_token=(
                    payload.access_token.strip()
                    if payload.access_token is not None and payload.access_token.strip()
                    else None
                ),
                clear_access_token=payload.clear_access_token,
                enabled=payload.enabled,
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail=web_app._friendly_error(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json({"id": host_id, "message": "Git host updated."})

    @app.delete("/api/admin/git-hosts/{host_id}")
    def admin_delete_git_host(
        host_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_git_host(connection, host_id) is None:
            raise HTTPException(status_code=404, detail="Git host not found.")
        try:
            delete_git_host(connection, host_id)
            connection.commit()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json({"message": "Git host deleted."})

    @app.get("/api/admin/repositories/search")
    def admin_search_repositories(
        q: str,
        host_id: list[int] | None = Query(default=None),
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        query = q.strip()
        if len(query) < 2 or len(query) > 200:
            raise HTTPException(status_code=422, detail="Enter at least two characters.")
        hosts = list_git_hosts(connection, enabled_only=True)
        if host_id is not None:
            selected_host_ids = set(host_id)
            hosts = tuple(host for host in hosts if host.id in selected_host_ids)
        if not hosts:
            raise HTTPException(status_code=422, detail="Select at least one enabled Git host.")
        try:
            results = search_repositories(hosts, query)
        except SourceServiceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return _json({"repositories": results})

    @app.get("/api/admin/git-hosts")
    def admin_list_git_hosts(connection: sqlite3.Connection = Depends(web_app._database)):
        return _json(
            {
                "git_hosts": [
                    {"id": host.id, "name": host.name, "provider": host.provider}
                    for host in list_git_hosts(connection, enabled_only=True)
                ]
            }
        )

    @app.get("/api/admin/repositories/releases")
    def admin_repository_releases(
        host_id: int,
        full_name: str,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        host = get_git_host(connection, host_id)
        if host is None or not host.enabled:
            raise HTTPException(status_code=404, detail="Git host is unavailable.")
        try:
            releases = list_releases(host, full_name)
        except SourceServiceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return _json({"releases": releases})

    @app.get("/api/admin/badges")
    def admin_badges(connection: sqlite3.Connection = Depends(web_app._database)):
        badges = list_badges(connection)
        engine_counts = {
            int(row["badge_id"]): int(row["count"])
            for row in connection.execute(
                "SELECT badge_id, COUNT(*) AS count FROM engine_badges GROUP BY badge_id"
            )
        }
        return _json(
            {
                "badges": [
                    {
                        **jsonable_encoder(badge),
                        "engine_count": engine_counts.get(badge.id, 0),
                    }
                    for badge in badges
                ]
            }
        )

    @app.get("/api/admin/badges/form")
    def admin_badge_form(
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        return _json(
            {
                "badge": None,
                "engine_ids": [],
                "engines": list_engine_families(connection),
            }
        )

    @app.get("/api/admin/badges/{badge_id}")
    def admin_badge(
        badge_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        badge = get_badge(connection, badge_id)
        if badge is None:
            raise HTTPException(status_code=404, detail="Badge not found.")
        return _json(
            {
                "badge": badge,
                "engine_ids": list_badge_engine_ids(connection, badge_id),
                "engines": list_engine_families(connection),
            }
        )

    @app.post("/api/admin/badges")
    def admin_create_badge(
        payload: BadgePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            badge_id = create_badge(
                connection,
                name=payload.name,
                emoji=payload.emoji,
                description=payload.description,
            )
            replace_badge_engines(connection, badge_id, payload.engine_ids)
            connection.commit()
        except sqlite3.IntegrityError as exc:
            detail = (
                "A badge with that name already exists."
                if "badges_name_key" in str(exc)
                else web_app._friendly_error(exc)
            )
            raise HTTPException(status_code=409, detail=detail) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {"id": badge_id, "message": "Badge created."},
            status_code=201,
        )

    @app.put("/api/admin/badges/{badge_id}")
    def admin_update_badge(
        badge_id: int,
        payload: BadgePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_badge(connection, badge_id) is None:
            raise HTTPException(status_code=404, detail="Badge not found.")
        try:
            update_badge(
                connection,
                badge_id,
                name=payload.name,
                emoji=payload.emoji,
                description=payload.description,
            )
            replace_badge_engines(connection, badge_id, payload.engine_ids)
            connection.commit()
        except sqlite3.IntegrityError as exc:
            detail = (
                "A badge with that name already exists."
                if "badges_name_key" in str(exc)
                else web_app._friendly_error(exc)
            )
            raise HTTPException(status_code=409, detail=detail) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json({"id": badge_id, "message": "Badge updated."})

    @app.delete("/api/admin/badges/{badge_id}")
    def admin_delete_badge(
        badge_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        badge = get_badge(connection, badge_id)
        if badge is None:
            raise HTTPException(status_code=404, detail="Badge not found.")
        delete_badge(connection, badge_id)
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({"message": "Badge deleted."})

    # Engines

    @app.get("/api/admin/engines")
    def admin_engines(connection: sqlite3.Connection = Depends(web_app._database)):
        engines = list_engine_families(connection)
        versions = list_engine_records(connection)
        versions_by_family = {engine.id: [] for engine in engines}
        for version in versions:
            versions_by_family.setdefault(version.engine_id, []).append(version)
        return _json(
            {
                "engines": [
                    {
                        **jsonable_encoder(engine),
                        "versions": [
                            _engine_version_admin_payload(version)
                            for version in versions_by_family[engine.id]
                        ],
                    }
                    for engine in engines
                ],
                "game_counts": list_engine_game_counts(connection),
            }
        )

    @app.get("/api/admin/engines/form")
    def admin_engine_form():
        return _json(
            {
                "engine": None,
                "defaults": {
                    "name": "",
                    "author": "",
                    "active": True,
                },
            }
        )

    @app.get("/api/admin/engines/{engine_id}")
    def admin_engine(
        engine_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        engine = get_engine_family(connection, engine_id)
        if engine is None:
            raise HTTPException(status_code=404, detail="Engine not found.")
        versions = list_engine_versions(connection, engine_id)
        return _json(
            {
                "engine": engine,
                "versions": [_engine_version_admin_payload(version) for version in versions],
                "game_counts": list_engine_game_counts(
                    connection,
                    (version.id for version in versions),
                ),
            }
        )

    @app.post("/api/admin/engines")
    def admin_create_engine(
        payload: EnginePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            engine_id = create_engine(
                connection, name=payload.name.strip(), author=payload.author.strip(), active=payload.active
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail=web_app._friendly_error(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {"id": engine_id, "message": "Engine registered."},
            status_code=201,
        )

    @app.put("/api/admin/engines/{engine_id}")
    def admin_update_engine(
        engine_id: int,
        payload: EnginePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_engine_family(connection, engine_id) is None:
            raise HTTPException(status_code=404, detail="Engine not found.")
        try:
            update_engine(
                connection,
                engine_id,
                name=payload.name.strip(),
                author=payload.author.strip(),
                active=payload.active,
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail=web_app._friendly_error(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json({"id": engine_id, "message": "Engine updated."})

    @app.delete("/api/admin/engines/{engine_id}")
    def admin_delete_engine(
        engine_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_engine_family(connection, engine_id) is None:
            raise HTTPException(status_code=404, detail="Engine not found.")
        try:
            delete_engine(connection, engine_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({"message": "Engine deleted."})

    @app.post("/api/admin/engines/{engine_id}/versions")
    def admin_create_engine_version(
        engine_id: int,
        payload: EngineVersionCreatePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_engine_family(connection, engine_id) is None:
            raise HTTPException(status_code=404, detail="Engine not found.")
        if payload.distribution == "worker_local":
            git_host_id = None
            repository_url = ""
            repository_full_name = ""
            source_ref = ""
            source_kind = "commit"
            dockerfile_path = ""
            dockerfile = ""
        else:
            try:
                host = get_git_host(connection, payload.git_host_id)
                if host is None or not host.enabled:
                    raise HTTPException(status_code=404, detail="Git host is unavailable.")
                if payload.source_kind == "commit":
                    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", payload.source_ref):
                        raise HTTPException(status_code=422, detail="Enter a valid commit hash.")
                else:
                    release_tags = {
                        release["tag"]
                        for release in list_releases(host, payload.repository_full_name)
                    }
                    if payload.source_ref not in release_tags:
                        raise HTTPException(status_code=422, detail="Choose a public release.")
                repository_url = canonical_repository_url(
                    host,
                    payload.repository_full_name,
                )
            except SourceServiceError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            git_host_id = host.id
            repository_full_name = payload.repository_full_name
            source_ref = payload.source_ref
            source_kind = payload.source_kind
            dockerfile_path = payload.dockerfile_path
            dockerfile = _load_engine_dockerfile(payload.dockerfile_path)
        try:
            version_id = create_engine_version(
                connection,
                engine_id=engine_id,
                version=payload.version,
                git_host_id=git_host_id,
                repository_url=repository_url,
                repository_full_name=repository_full_name,
                source_ref=source_ref,
                source_kind=source_kind,
                dockerfile_path=dockerfile_path,
                dockerfile=dockerfile,
                distribution=payload.distribution,
                worker_local_key=payload.worker_local_key,
                uci_options=payload.uci_options,
                active=True,
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail=web_app._friendly_error(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {"id": version_id, "message": f"Version {payload.version} created."},
            status_code=201,
        )

    @app.get("/api/admin/engine-versions/{version_id}")
    def admin_engine_version(
        version_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        version = get_engine_version_record(connection, version_id)
        if version is None:
            raise HTTPException(status_code=404, detail="Engine version not found.")
        return _json({
            "version": _engine_version_admin_payload(version),
            "benchmarks": [
                _benchmark_job_admin_payload(item)
                for item in list_engine_benchmark_jobs(connection, engine_version_id=version_id)
            ],
        })

    @app.put("/api/admin/engine-versions/{version_id}")
    def admin_update_engine_version(
        version_id: int,
        payload: EngineVersionUpdatePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        version = get_engine_version_record(connection, version_id)
        if version is None:
            raise HTTPException(status_code=404, detail="Engine version not found.")
        options = payload.uci_options
        if any(not str(name).strip() for name in options):
            raise HTTPException(status_code=422, detail="Default UCI options must be an object with non-empty names.")
        if version.distribution == "worker_local":
            dockerfile_path = ""
            dockerfile = ""
        else:
            if not payload.dockerfile_path:
                raise HTTPException(status_code=422, detail="Choose a Dockerfile.")
            dockerfile_path = payload.dockerfile_path
            dockerfile = _load_engine_dockerfile(payload.dockerfile_path)
        try:
            update_engine_version(
                connection,
                version_id,
                version=payload.version,
                dockerfile_path=dockerfile_path,
                dockerfile=dockerfile,
                uci_options=options,
                active=True,
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail=web_app._friendly_error(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json({"id": version_id, "message": "Engine version updated."})

    @app.put("/api/admin/engine-versions/{version_id}/artifact")
    async def admin_upload_engine_artifact(
        version_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        version = get_engine_version_record(connection, version_id)
        if version is None:
            raise HTTPException(status_code=404, detail="Engine version not found.")
        if version.distribution == "worker_local":
            raise HTTPException(
                status_code=409,
                detail="Worker-local engines do not accept uploaded artifacts.",
            )
        if not version.dockerfile_path:
            raise HTTPException(
                status_code=409,
                detail="Select and save a Dockerfile before uploading an artifact.",
            )
        dockerfile = _load_engine_dockerfile(version.dockerfile_path)
        if dockerfile != version.dockerfile:
            raise HTTPException(
                status_code=409,
                detail="The selected Dockerfile changed in data/engines. Save the version before uploading an artifact.",
            )
        artifact = await _store_engine_artifact_upload(
            request,
            connection,
            build_hash=version.build_hash,
        )
        _publish_admin_change(web_app, request)
        return _json({
            "artifact": _engine_artifact_spec(artifact),
            "message": "Engine artifact uploaded.",
        })

    @app.get("/api/admin/engine-versions/{version_id}/events")
    async def admin_engine_version_events(version_id: int, request: Request):
        """Stream benchmark state without replacing or reloading the edit form."""
        def version_exists() -> bool:
            connection = connect_database(request.app.state.db_path)
            try:
                return get_engine_version_record(connection, version_id) is not None
            finally:
                connection.close()

        if not await asyncio.to_thread(version_exists):
            raise HTTPException(status_code=404, detail="Engine version not found.")

        def snapshot() -> dict[str, Any]:
            current = connect_database(request.app.state.db_path)
            try:
                version = get_engine_version_record(current, version_id)
                if version is None:
                    return {
                        "artifact": None,
                        "benchmark_current": False,
                        "active": False,
                        "worker_local_count": 0,
                        "benchmarks": [],
                    }
                return {
                    "artifact": jsonable_encoder(version.artifact),
                    "benchmark_current": version.benchmark_current,
                    "active": version.active,
                    "worker_local_count": version.worker_local_count,
                    "benchmarks": [
                        _benchmark_job_admin_payload(item)
                        for item in list_engine_benchmark_jobs(
                            current, engine_version_id=version_id
                        )
                    ]
                }
            finally:
                current.close()

        async def stream():
            previous = ""
            while not await request.is_disconnected():
                payload = await asyncio.to_thread(snapshot)
                encoded = json.dumps(payload, separators=(",", ":"))
                if encoded != previous:
                    previous = encoded
                    yield f"event: engine-version.snapshot\ndata: {encoded}\n\n"
                else:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/admin/engine-versions/{version_id}/benchmarks/reschedule")
    def admin_reschedule_engine_benchmarks(
        version_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        version = get_engine_version_record(connection, version_id)
        if version is None:
            raise HTTPException(status_code=404, detail="Engine version not found.")
        if version.distribution == "worker_local":
            raise HTTPException(status_code=409, detail="Worker-local engines cannot be benchmarked.")
        if not version.dockerfile_path:
            raise HTTPException(
                status_code=409,
                detail="Select and save a Dockerfile from data/engines before requesting a benchmark.",
            )
        dockerfile = _load_engine_dockerfile(version.dockerfile_path)
        if dockerfile != version.dockerfile:
            raise HTTPException(
                status_code=409,
                detail="The selected Dockerfile changed in data/engines. Save the version before benchmarking.",
            )
        engine = next((item for item in list_engines(connection) if item.engine_id == version_id), None)
        if engine is None:
            raise HTTPException(status_code=404, detail="Engine version not found.")
        try:
            count = reschedule_engine_benchmarks(connection, engine=engine)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        connection.commit()
        _publish_admin_change(web_app, request)
        if count:
            return _json({"message": f"Queued {count} benchmark {'job' if count == 1 else 'jobs'}."})
        return _json({"message": "No benchmark hardware is registered yet. Connect a benchmarker, then request the benchmark again."})

    @app.get("/api/admin/benchmark-hardware")
    def admin_benchmark_hardware(
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        return _json({
            "hardware": [
                {
                    "hardware_key": item.hardware_key,
                    "machine_id": item.machine_id,
                    "hardware": jsonable_encoder(item.hw),
                    "created_at": item.created_at,
                }
                for item in list_benchmark_hardware(connection)
            ]
        })

    @app.post("/api/admin/engine-versions/{version_id}/benchmarks/manual")
    def admin_record_manual_benchmark(
        version_id: int,
        payload: ManualBenchmarkPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        version = get_engine_version_record(connection, version_id)
        if version is None:
            raise HTTPException(status_code=404, detail="Engine version not found.")
        if version.distribution == "worker_local":
            raise HTTPException(status_code=409, detail="Worker-local engines cannot be benchmarked.")
        if not version.dockerfile_path:
            raise HTTPException(
                status_code=409,
                detail="Select and save a Dockerfile from data/engines before recording a benchmark.",
            )
        dockerfile = _load_engine_dockerfile(version.dockerfile_path)
        if dockerfile != version.dockerfile:
            raise HTTPException(
                status_code=409,
                detail="The selected Dockerfile changed in data/engines. Save the version before recording a benchmark.",
            )
        engine = next((item for item in list_engines(connection) if item.engine_id == version_id), None)
        if engine is None:
            raise HTTPException(status_code=404, detail="Engine version not found.")
        try:
            record_manual_benchmark(
                connection,
                engine=engine,
                nps=payload.nps,
                elapsed_ms=payload.elapsed_ms,
                hardware_key=payload.hardware_key,
                machine_id=payload.machine_id,
                hw=payload.hardware,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({"message": "Manual benchmark recorded."}, status_code=201)

    @app.delete("/api/admin/benchmark-jobs/{job_id}")
    def admin_forget_failed_benchmark_job(
        job_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if not forget_failed_benchmark_job(connection, job_id=job_id):
            raise HTTPException(status_code=404, detail="Failed benchmark job not found.")
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({"message": "Failed benchmark forgotten."})

    @app.delete("/api/admin/engine-versions/{version_id}/benchmarks")
    def admin_forget_engine_benchmarks(
        version_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        engine = next((item for item in list_engines(connection) if item.engine_id == version_id), None)
        if engine is None:
            raise HTTPException(status_code=404, detail="Engine version not found.")
        try:
            count = forget_engine_benchmarks(connection, engine=engine)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({
            "message": (
                f"Forgot {count} benchmark {'job' if count == 1 else 'jobs'} and hardware assignment."
                if count
                else "No benchmark or hardware assignment was stored."
            )
        })

    @app.delete("/api/admin/engine-versions/{version_id}")
    def admin_delete_engine_version(
        version_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            delete_engine_version(connection, version_id)
            connection.commit()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json({"message": "Engine version deleted."})

    # Opening suites

    @app.get("/api/admin/openings")
    def admin_openings(connection: sqlite3.Connection = Depends(web_app._database)):
        suites = list_opening_suites(connection)
        tournaments = list_tournaments(connection)
        opening_counts = {
            int(row["suite_id"]): int(row["count"])
            for row in connection.execute(
                "SELECT suite_id, COUNT(*) AS count FROM openings GROUP BY suite_id"
            )
        }
        usage_counts = {suite.id: 0 for suite in suites}
        for tournament in tournaments:
            suite_id = tournament.config.opening_suite_id
            if suite_id in usage_counts:
                usage_counts[suite_id] += 1
        return _json(
            {
                "suites": suites,
                "opening_counts": opening_counts,
                "usage_counts": usage_counts,
            }
        )

    @app.get("/api/admin/openings/form")
    def admin_opening_form():
        return _json(
            {
                "suite": None,
                "openings": [],
                "positions_text": "",
                "usage_count": 0,
                "limits": {
                    "accepted_extensions": [".pgn", ".epd", ".fen", ".txt"],
                },
            }
        )

    @app.get("/api/admin/openings/{suite_id}")
    def admin_opening(
        suite_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        suite = get_opening_suite(connection, suite_id)
        if suite is None:
            raise HTTPException(status_code=404, detail="Opening suite not found.")
        position_count = suite_opening_count(connection, suite_id)
        positions_truncated = position_count > OPENING_EDITOR_POSITION_LIMIT
        openings = () if positions_truncated else list_suite_openings(connection, suite_id)
        return _json(
            {
                "suite": suite,
                "openings": openings,
                "position_count": position_count,
                "positions_truncated": positions_truncated,
                "positions_text": "\n".join(
                    format_opening(
                        OpeningLine(
                            name=opening.name,
                            start_fen=opening.start_fen,
                            moves=opening.moves,
                            fen=opening.fen,
                        )
                    )
                    for opening in openings
                ),
                "usage_count": sum(
                    tournament.config.opening_suite_id == suite_id
                    for tournament in list_tournaments(connection)
                ),
            }
        )

    @app.post("/api/admin/openings")
    async def admin_create_opening(
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        values, files = await _read_opening_form(request)
        name = values.get("name", "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="Suite name is required.")
        try:
            openings = await asyncio.to_thread(
                _opening_values,
                values.get("positions", ""),
                files,
            )
            suite_id = await asyncio.to_thread(
                _create_opening_import,
                connection,
                name=name,
                description=values.get("description", "").strip(),
                openings=openings,
            )
        except (ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(status_code=409, detail=web_app._friendly_error(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "id": suite_id,
                "position_count": len(openings),
                "message": "Opening suite created.",
            },
            status_code=201,
        )

    @app.put("/api/admin/openings/{suite_id}")
    async def admin_update_opening(
        suite_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if await asyncio.to_thread(get_opening_suite, connection, suite_id) is None:
            raise HTTPException(status_code=404, detail="Opening suite not found.")
        values, files = await _read_opening_form(request)
        name = values.get("name", "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="Suite name is required.")
        mode = values.get("mode", "replace")
        if mode not in {"replace", "append", "keep"}:
            raise HTTPException(status_code=422, detail="Choose a valid import mode.")
        try:
            openings = []
            if mode != "keep":
                openings = await asyncio.to_thread(
                    _opening_values,
                    values.get("positions", ""),
                    files,
                )
            position_count = await asyncio.to_thread(
                _update_opening_import,
                connection,
                suite_id,
                name=name,
                description=values.get("description", "").strip(),
                openings=openings,
                mode=mode,
            )
        except (ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(status_code=409, detail=web_app._friendly_error(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "id": suite_id,
                "position_count": position_count,
                "message": "Opening suite updated.",
            }
        )

    @app.delete("/api/admin/openings/{suite_id}")
    def admin_delete_opening(
        suite_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_opening_suite(connection, suite_id) is None:
            raise HTTPException(status_code=404, detail="Opening suite not found.")
        if any(
            tournament.config.opening_suite_id == suite_id
            for tournament in list_tournaments(connection)
        ):
            raise HTTPException(
                status_code=409,
                detail="This opening suite is used by a tournament.",
            )
        delete_opening_suite(connection, suite_id)
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({"message": "Opening suite deleted."})

    @app.get("/api/admin/tools")
    def admin_tools(connection: sqlite3.Connection = Depends(web_app._database)):
        jobs = list_tool_jobs(connection, limit=12)
        workers = list_workers(connection)
        return _json(
            {
                "tools": [
                    {
                        "name": "who_has_this",
                        "label": "Who Has This",
                        "description": "Inspect engine UCI handshakes across any selection of versions.",
                        "href": "/admin/tools/who-has-this",
                        "status": "available",
                    },
                    {
                        "name": "puzzle_suite_manager",
                        "label": "Puzzle Suite Manager",
                        "description": "Verify unique solutions, measure solve effort, finetune misses, and order puzzle suites by engine-rated difficulty.",
                        "href": "/admin/tools/puzzle-suite-manager",
                        "status": "available",
                    },
                    {
                        "name": "invalidate_rating_list_engine",
                        "label": "Invalidate engine games",
                        "description": "Uncommit one engine's games from every affected rating list, then invalidate them.",
                        "href": "/admin/tools/invalidate-engine-games",
                        "status": "available",
                    },
                    {
                        "name": "tournament_creator",
                        "label": "Tournament creator",
                        "description": "Create rating divisions or build a balanced Elo gauntlet as editable tournament drafts.",
                        "href": "/admin/tools/tournament-creator",
                        "status": "available",
                    },
                    {
                        "name": "clone_environment",
                        "label": "Clone environment",
                        "description": "Pull a dependency-complete, verified selection of data and engine artifacts from another Cope host.",
                        "href": "/admin/tools/clone-environment",
                        "status": "available",
                    },
                ],
                "recent_jobs": [
                    _tool_job_api_payload(connection, job, include_items=False)
                    for job in jobs
                ],
                "connected_workers": sum(
                    worker.status in {"connected", "ready", "busy"}
                    for worker in workers
                ),
            }
        )

    @app.get("/api/admin/tools/environment-clone")
    def admin_environment_clone(
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        return _json(
            {
                **clone_catalog_payload(),
                "instance_id": environment_instance_id(connection),
                "inventory": environment_inventory(connection),
                "recent_jobs": list_clone_jobs(connection),
            }
        )

    @app.post("/api/admin/tools/environment-clone/preflight")
    async def admin_environment_clone_preflight(
        payload: EnvironmentClonePreflightPayload,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        destination_instance = environment_instance_id(connection)

        def inspect_source():
            source_url = normalize_source_url(payload.source)
            opener = authenticated_source_opener(source_url, payload.admin_token)
            result = remote_json(opener, source_url, "/api/admin/environment-export/capabilities")
            result.pop("csrf_token", None)
            result["source_url"] = source_url
            return result

        try:
            source = await asyncio.to_thread(inspect_source)
        except (ValueError, RemoteCloneError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if source.get("instance_id") == destination_instance:
            raise HTTPException(status_code=409, detail="The source and destination are the same Cope installation.")
        source["compatible"] = (
            int(source.get("protocol_version", 0)) == CLONE_PROTOCOL_VERSION
            and int(source.get("schema_version", 0)) == database_schema_version(connection)
        )
        source["destination_inventory"] = environment_inventory(connection)
        return _json(source)

    @app.post("/api/admin/tools/environment-clone")
    async def admin_create_environment_clone(payload: EnvironmentClonePayload):
        def create_job():
            connection = connect_database(app.state.db_path)
            try:
                return create_clone_from_source(
                    connection,
                    payload.source,
                    payload.admin_token,
                    payload.datasets,
                )
            finally:
                connection.close()

        try:
            job = await asyncio.to_thread(create_job)
        except (ValueError, RemoteCloneError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _json({"job": job, "message": "Environment clone queued."}, status_code=201)

    @app.get("/api/admin/tools/environment-clone/jobs/{job_id}")
    def admin_environment_clone_job(
        job_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        job = clone_job_payload(connection, job_id, include_events=True)
        if job is None:
            raise HTTPException(status_code=404, detail="Environment clone job not found.")
        return _json({"job": job})

    @app.post("/api/admin/tools/environment-clone/jobs/{job_id}/cancel")
    def admin_cancel_environment_clone_job(
        job_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if clone_job_payload(connection, job_id, include_events=False) is None:
            raise HTTPException(status_code=404, detail="Environment clone job not found.")
        if not cancel_clone_job(connection, job_id):
            raise HTTPException(status_code=409, detail="This environment clone is no longer cancellable.")
        return _json({"message": "Environment clone cancellation requested."})

    @app.post("/api/admin/tools/environment-clone/jobs/{job_id}/resume")
    def admin_resume_environment_clone_job(
        job_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if clone_job_payload(connection, job_id, include_events=False) is None:
            raise HTTPException(status_code=404, detail="Environment clone job not found.")
        if not resume_clone_job(connection, job_id):
            raise HTTPException(status_code=409, detail="This environment clone cannot be resumed.")
        return _json({"message": "Environment clone queued to resume."})

    @app.get("/api/admin/tools/environment-clone/jobs/{job_id}/events")
    async def admin_environment_clone_events(job_id: int, request: Request):
        def snapshot():
            connection = connect_database(request.app.state.db_path)
            try:
                return clone_job_payload(connection, job_id, include_events=True)
            finally:
                connection.close()

        if await asyncio.to_thread(snapshot) is None:
            raise HTTPException(status_code=404, detail="Environment clone job not found.")

        async def stream():
            last_signature = ""
            while True:
                job = await asyncio.to_thread(snapshot)
                if job is None:
                    break
                signature = f"{job['status']}|{job['updated_at']}|{len(job['events'])}"
                if signature != last_signature:
                    yield f"event: clone.snapshot\ndata: {json.dumps(job, separators=(',', ':'))}\n\n"
                    last_signature = signature
                if job["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(1)
            yield ": complete\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/admin/tools/tournament-creator")
    def admin_tournament_creator(
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        engine_records = {engine.id: engine for engine in list_engine_records(connection)}
        eligible_engine_ids = {
            engine.id
            for engine in engine_records.values()
            if _engine_is_tournament_ready(connection, engine)
        }
        rating_lists = []
        for rating_list in list_rating_lists(connection):
            engines = []
            unavailable_engines = 0
            for row in list_rating_rows(connection, rating_list.id):
                engine_id = row.engine.engine_id
                if engine_id not in eligible_engine_ids:
                    unavailable_engines += 1
                    continue
                engines.append(
                    {
                        "id": engine_id,
                        "name": row.engine.name,
                        "version": row.engine.version,
                        "elo": row.elo,
                        "games_played": row.games_played,
                    }
                )
            rating_lists.append(
                {
                    "id": rating_list.id,
                    "name": rating_list.name,
                    "engines": engines,
                    "unavailable_engines": unavailable_engines,
                }
            )
        form = _tournament_form_payload(web_app, request, connection)
        form["engine_options"] = [
            engine for engine in form["engine_options"]
            if engine.id in eligible_engine_ids
        ]
        return _json(
            {
                "form": form,
                "rating_lists": rating_lists,
            }
        )

    @app.post("/api/admin/tools/tournament-creator/gauntlet-preview")
    def admin_tournament_creator_gauntlet_preview(
        payload: GauntletPreviewPayload,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        rating_list = get_rating_list(connection, payload.rating_list_id)
        if rating_list is None:
            raise HTTPException(status_code=404, detail="Rating list not found.")
        hero = get_engine_record(connection, payload.hero_engine_id)
        if (
            hero is None
            or not _engine_is_tournament_ready(connection, hero)
        ):
            raise HTTPException(status_code=422, detail="Choose an available hero engine.")
        engine_records = {engine.id: engine for engine in list_engine_records(connection)}
        candidates = []
        for row in list_rating_rows(connection, payload.rating_list_id):
            engine_id = row.engine.engine_id
            record = engine_records.get(engine_id)
            if engine_id == payload.hero_engine_id or record is None:
                continue
            if not _engine_is_tournament_ready(connection, record):
                continue
            candidates.append(row)
        opponent_count = payload.gauntlet_size - 1
        if len(candidates) < opponent_count:
            raise HTTPException(
                status_code=422,
                detail=f"This list has only {len(candidates)} available opponent{'s' if len(candidates) != 1 else ''} for the selected hero.",
            )
        rank_denominator = max(1, len(candidates) - 1)
        closeness_ranks = {
            row.engine.engine_id: rank / rank_denominator
            for rank, row in enumerate(
                sorted(
                    candidates,
                    key=lambda item: (
                        abs(item.elo - payload.elo_estimate),
                        item.games_played,
                        item.engine.engine_id,
                    ),
                )
            )
        }
        coverage_ranks = {
            row.engine.engine_id: rank / rank_denominator
            for rank, row in enumerate(
                sorted(
                    candidates,
                    key=lambda item: (
                        item.games_played,
                        abs(item.elo - payload.elo_estimate),
                        item.engine.engine_id,
                    ),
                )
            )
        }

        def selection_score(row) -> tuple[float, int, float, int]:
            rating_distance = abs(row.elo - payload.elo_estimate)
            return (
                0.7 * closeness_ranks[row.engine.engine_id]
                + 0.3 * coverage_ranks[row.engine.engine_id],
                row.games_played,
                rating_distance,
                row.engine.engine_id,
            )

        selected = sorted(candidates, key=selection_score)[:opponent_count]
        return _json(
            {
                "hero": {
                    "id": hero.id,
                    "name": hero.name,
                    "version": hero.version,
                },
                "opponents": [
                    {
                        "id": row.engine.engine_id,
                        "name": row.engine.name,
                        "version": row.engine.version,
                        "elo": row.elo,
                        "games_played": row.games_played,
                        "rating_distance": abs(row.elo - payload.elo_estimate),
                        "selection_score": selection_score(row)[0],
                    }
                    for row in selected
                ],
            }
        )

    @app.post("/api/admin/tools/tournament-creator/batch")
    def admin_tournament_creator_batch(
        payload: TournamentCreatorBatchPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            tournaments = [
                (item.name, _validated_tournament_config(connection, item.config))
                for item in payload.tournaments
            ]
            created = [
                {
                    "id": create_tournament(connection, name, config, status="draft"),
                    "name": name,
                    "participants": len(config.participants),
                }
                for name, config in tournaments
            ]
            connection.commit()
        except HTTPException:
            connection.rollback()
            raise
        except ValidationError as exc:
            connection.rollback()
            raise HTTPException(
                status_code=422,
                detail=[error["msg"] for error in exc.errors()],
            ) from exc
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise HTTPException(
                status_code=409,
                detail="Tournament data changed while the drafts were being created. Reload the tool and try again.",
            ) from exc
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            LOG.exception("tournament creator batch failed")
            raise HTTPException(
                status_code=503,
                detail="The database could not save the tournament drafts. Try again.",
            ) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "tournaments": created,
                "message": f"Created {len(created)} tournament draft{'s' if len(created) != 1 else ''}.",
            },
            status_code=201,
        )

    @app.get("/api/admin/tools/invalidate-engine-games")
    def admin_invalidate_engine_games_context(
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        engine_ids_by_list = list_rating_list_engine_ids(connection)
        relevant_engine_ids = {
            engine_id
            for engine_ids in engine_ids_by_list.values()
            for engine_id in engine_ids
        }
        return _json(
            {
                "rating_lists": [
                    {
                        "id": rating_list.id,
                        "name": rating_list.name,
                        "engine_ids": engine_ids_by_list.get(rating_list.id, ()),
                    }
                    for rating_list in list_rating_lists(connection)
                ],
                "engines": [
                    {
                        "id": engine.id,
                        "name": engine.name,
                        "version": engine.version,
                        "author": engine.author,
                    }
                    for engine in list_engine_records(connection)
                    if engine.id in relevant_engine_ids
                ],
            }
        )

    @app.post("/api/admin/tools/invalidate-engine-games")
    def admin_invalidate_engine_games(
        payload: InvalidateRatingListEnginePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        engine = get_engine_record(connection, payload.engine_id)
        if engine is None:
            raise HTTPException(status_code=404, detail="Engine version not found.")
        rating_list = get_rating_list(connection, payload.rating_list_id)
        if rating_list is None:
            raise HTTPException(status_code=404, detail="Rating list not found.")
        try:
            result = invalidate_rating_list_engine_games(
                connection,
                payload.rating_list_id,
                payload.engine_id,
            )
            connection.commit()
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            LOG.exception(
                "rating-list engine invalidation failed rating_list_id=%s engine_id=%s",
                payload.rating_list_id,
                payload.engine_id,
            )
            raise HTTPException(
                status_code=503,
                detail="The database could not invalidate the engine games. Try again.",
            ) from exc
        _publish_admin_change(web_app, request)
        game_count = len(result.game_ids)
        return _json(
            {
                "message": f"Invalidated {game_count} game{'s' if game_count != 1 else ''} for {engine.name} {engine.version}.",
                "games_invalidated": game_count,
                "tournaments_affected": len(result.tournament_ids),
                "rating_lists_affected": len(result.rating_list_ids),
                "list_memberships_removed": result.list_memberships_removed,
            }
        )

    @app.get("/api/admin/tools/who-has-this")
    def admin_who_has_this(
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        workers = list_workers(connection)
        return _json(
            {
                "engines": [
                    {
                        "id": engine.id,
                        "family_id": engine.engine_id,
                        "name": engine.name,
                        "author": engine.author,
                        "version": engine.version,
                        "repository": engine.repository_full_name,
                        "distribution": engine.distribution,
                        "artifact_ready": engine.artifact is not None,
                        "active": engine.engine_active,
                    }
                    for engine in list_engine_records(connection)
                ],
                "workers": [
                    {
                        "id": worker.id,
                        "label": worker.label,
                        "status": worker.status,
                    }
                    for worker in workers
                    if worker.status in {"connected", "ready", "busy"}
                ],
                "recent_jobs": [
                    _tool_job_api_payload(connection, job, include_items=False)
                    for job in list_tool_jobs(
                        connection,
                        tool_name="who_has_this",
                        limit=12,
                    )
                ],
            }
        )

    @app.get("/api/admin/tools/puzzle-suite-manager")
    def admin_puzzle_suite_manager(
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        workers = list_workers(connection)
        return _json(
            {
                "engines": [
                    {
                        "id": engine.id,
                        "family_id": engine.engine_id,
                        "name": engine.name,
                        "author": engine.author,
                        "version": engine.version,
                        "distribution": engine.distribution,
                        "artifact_ready": engine.artifact is not None,
                        "active": engine.engine_active,
                    }
                    for engine in list_engine_records(connection)
                ],
                "rating_lists": [
                    {
                        "id": rating_list.id,
                        "name": rating_list.name,
                        "ratings": [
                            {"engine_id": row.engine.engine_id, "elo": row.elo}
                            for row in list_rating_rows(connection, rating_list.id)
                        ],
                    }
                    for rating_list in list_rating_lists(connection)
                ],
                "suites": [
                    _puzzle_suite_summary_payload(connection, suite)
                    for suite in list_puzzle_suites(connection)
                ],
                "workers": [
                    {
                        "id": worker.id,
                        "label": worker.label,
                        "status": worker.status,
                        "threads": None if worker.capacity is None else worker.capacity.threads,
                        "hash_mb": None if worker.capacity is None else worker.capacity.hash_mb,
                    }
                    for worker in workers
                    if worker.status in {"connected", "ready", "busy"}
                ],
            }
        )

    @app.post("/api/admin/tools/puzzle-suite-manager/suites")
    def admin_create_puzzle_suite(
        payload: PuzzleSuiteCreatePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            puzzles = _parse_puzzle_suite_block(payload.puzzles)
            suite = create_puzzle_suite(connection, name=payload.name, puzzles=puzzles)
            connection.commit()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "suite": _puzzle_suite_detail_payload(connection, suite),
                "message": f"Imported {len(puzzles)} puzzles.",
            },
            status_code=201,
        )

    @app.get("/api/admin/tools/puzzle-suite-manager/suites/{suite_id}")
    def admin_puzzle_suite(
        suite_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        suite = get_puzzle_suite(connection, suite_id)
        if suite is None:
            raise HTTPException(status_code=404, detail="Puzzle suite not found.")
        return _json({"suite": _puzzle_suite_detail_payload(connection, suite)})

    @app.post("/api/admin/tools/puzzle-suite-manager/suites/{suite_id}/uniqueness")
    def admin_start_puzzle_suite_uniqueness(
        suite_id: int,
        payload: PuzzleSuiteUniquenessPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        suite = get_puzzle_suite(connection, suite_id)
        if suite is None:
            raise HTTPException(status_code=404, detail="Puzzle suite not found.")
        _ensure_puzzle_suite_idle(connection, suite_id)
        puzzles = list_puzzle_suite_puzzles(connection, suite_id)
        puzzle_input = [
            {
                "id": puzzle.id,
                "fen": puzzle.fen,
                "solutions": list(puzzle.solutions),
            }
            for puzzle in puzzles
        ]
        settings = payload.model_dump(mode="json")
        try:
            job = create_tool_job(
                connection,
                tool_name="puzzle_suite_uniqueness",
                input_data={
                    "suite_id": suite_id,
                    "stage": "uniqueness",
                    "puzzles": puzzle_input,
                    **settings,
                },
                engine_version_ids=(payload.engine_id,),
                required_threads=payload.threads,
                required_hash_mb=payload.hash_mb,
            )
            prepare_puzzle_suite_run(
                connection,
                suite_id=suite_id,
                job_id=job.id,
                stage="uniqueness",
                rating_list_id=None,
                settings=settings,
            )
            connection.commit()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "job": _tool_job_api_payload(connection, job, include_items=True),
                "message": f"Uniqueness analysis queued for {len(puzzles)} puzzles.",
            },
            status_code=201,
        )

    @app.post("/api/admin/tools/puzzle-suite-manager/suites/{suite_id}/difficulty")
    def admin_start_puzzle_suite_difficulty(
        suite_id: int,
        payload: PuzzleSuiteDifficultyPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        suite = get_puzzle_suite(connection, suite_id)
        if suite is None:
            raise HTTPException(status_code=404, detail="Puzzle suite not found.")
        _ensure_puzzle_suite_idle(connection, suite_id)
        rating_list = get_rating_list(connection, payload.rating_list_id)
        if rating_list is None:
            raise HTTPException(status_code=422, detail="Rating list not found.")
        ratings = {row.engine.engine_id: row.elo for row in list_rating_rows(connection, rating_list.id)}
        missing = [engine_id for engine_id in payload.engine_ids if engine_id not in ratings]
        if missing:
            raise HTTPException(
                status_code=422,
                detail="Every selected engine must have an Elo in the selected rating list.",
            )
        suite_puzzles = list_puzzle_suite_puzzles(connection, suite_id)
        unfiltered = bool(suite_puzzles) and all(
            puzzle.uniqueness_status == "pending" for puzzle in suite_puzzles
        )
        if unfiltered:
            puzzles = tuple(
                (puzzle, puzzle.solutions[0])
                for puzzle in suite_puzzles
                if len(puzzle.solutions) == 1
            )
            if len(puzzles) != len(suite_puzzles):
                raise HTTPException(
                    status_code=422,
                    detail="Every unfiltered puzzle must have exactly one supplied solution before rating.",
                )
        else:
            puzzles = tuple(
                (puzzle, puzzle.verified_solution)
                for puzzle in suite_puzzles
                if puzzle.included
                and puzzle.uniqueness_status == "unique"
                and puzzle.verified_solution
            )
        if not puzzles:
            raise HTTPException(
                status_code=422,
                detail="Add an unfiltered suite with one supplied solution per puzzle, or include at least one verified unique puzzle.",
            )
        engine_elos = {str(engine_id): ratings[engine_id] for engine_id in payload.engine_ids}
        settings = {
            **payload.model_dump(mode="json"),
            "engine_elos": engine_elos,
            "rating_list_name": rating_list.name,
        }
        puzzle_input = [
            {
                "id": puzzle.id,
                "fen": puzzle.fen,
                "solutions": [solution],
            }
            for puzzle, solution in puzzles
        ]
        try:
            job = create_tool_job(
                connection,
                tool_name="puzzle_suite_difficulty",
                input_data={
                    "suite_id": suite_id,
                    "stage": "difficulty",
                    "puzzles": puzzle_input,
                    "multipv": 1,
                    **settings,
                },
                engine_version_ids=payload.engine_ids,
                required_threads=payload.threads,
                required_hash_mb=payload.hash_mb,
            )
            prepare_puzzle_suite_run(
                connection,
                suite_id=suite_id,
                job_id=job.id,
                stage="difficulty",
                rating_list_id=rating_list.id,
                settings=settings,
            )
            connection.commit()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "job": _tool_job_api_payload(connection, job, include_items=True),
                "message": f"Difficulty analysis queued across {len(payload.engine_ids)} engines.",
            },
            status_code=201,
        )

    @app.post("/api/admin/tools/puzzle-suite-manager/suites/{suite_id}/miss-finetuning")
    def admin_start_puzzle_suite_miss_finetuning(
        suite_id: int,
        payload: PuzzleSuiteDifficultyPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        suite = get_puzzle_suite(connection, suite_id)
        if suite is None:
            raise HTTPException(status_code=404, detail="Puzzle suite not found.")
        _ensure_puzzle_suite_idle(connection, suite_id)
        rating_list = get_rating_list(connection, payload.rating_list_id)
        if rating_list is None:
            raise HTTPException(status_code=422, detail="Rating list not found.")
        ratings = {row.engine.engine_id: row.elo for row in list_rating_rows(connection, rating_list.id)}
        missing = [engine_id for engine_id in payload.engine_ids if engine_id not in ratings]
        if missing:
            raise HTTPException(
                status_code=422,
                detail="Every selected engine must have an Elo in the selected rating list.",
            )
        miss_selection = _puzzle_suite_missed_puzzle_ids(connection, suite_id)
        if miss_selection is None:
            raise HTTPException(
                status_code=422,
                detail="Complete a difficulty rating run before miss finetuning.",
            )
        source_difficulty_run_id, missed_puzzle_ids = miss_selection
        if not missed_puzzle_ids:
            raise HTTPException(
                status_code=422,
                detail="Every puzzle was solved in the latest completed difficulty run.",
            )
        puzzles = []
        for puzzle in list_puzzle_suite_puzzles(connection, suite_id):
            if puzzle.id not in missed_puzzle_ids:
                continue
            solution = puzzle.verified_solution
            if not solution and len(puzzle.solutions) == 1:
                solution = puzzle.solutions[0]
            if solution:
                puzzles.append((puzzle, solution))
        if len(puzzles) != len(missed_puzzle_ids):
            raise HTTPException(
                status_code=422,
                detail="One or more missed puzzles no longer has exactly one target solution.",
            )
        thread_elo_bonus = 80.0 * math.log2(payload.threads)
        base_engine_elos = {
            str(engine_id): ratings[engine_id] for engine_id in payload.engine_ids
        }
        engine_elos = {
            engine_id: round(float(elo) + thread_elo_bonus, 3)
            for engine_id, elo in base_engine_elos.items()
        }
        settings = {
            **payload.model_dump(mode="json"),
            "engine_elos": engine_elos,
            "base_engine_elos": base_engine_elos,
            "thread_elo_bonus": round(thread_elo_bonus, 3),
            "thread_elo_per_doubling": 80,
            "rating_list_name": rating_list.name,
            "source_difficulty_run_id": source_difficulty_run_id,
            "suite_stage": "miss_finetuning",
        }
        puzzle_input = [
            {
                "id": puzzle.id,
                "fen": puzzle.fen,
                "solutions": [solution],
            }
            for puzzle, solution in puzzles
        ]
        try:
            job = create_tool_job(
                connection,
                tool_name="puzzle_suite_difficulty",
                input_data={
                    "suite_id": suite_id,
                    "stage": "difficulty",
                    "suite_stage": "miss_finetuning",
                    "puzzles": puzzle_input,
                    "multipv": 1,
                    **settings,
                },
                engine_version_ids=payload.engine_ids,
                required_threads=payload.threads,
                required_hash_mb=payload.hash_mb,
            )
            prepare_puzzle_suite_run(
                connection,
                suite_id=suite_id,
                job_id=job.id,
                stage="difficulty",
                rating_list_id=rating_list.id,
                settings=settings,
            )
            connection.commit()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "job": _tool_job_api_payload(connection, job, include_items=True),
                "message": (
                    f"Miss finetuning queued for {len(puzzles)} puzzles with "
                    f"a {thread_elo_bonus:.1f} Elo thread adjustment."
                ),
            },
            status_code=201,
        )

    @app.patch("/api/admin/tools/puzzle-suite-manager/suites/{suite_id}/puzzles/{puzzle_id}")
    def admin_update_puzzle_suite_puzzle(
        suite_id: int,
        puzzle_id: int,
        payload: PuzzleSuitePuzzleIncludePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_puzzle_suite(connection, suite_id) is None:
            raise HTTPException(status_code=404, detail="Puzzle suite not found.")
        try:
            set_puzzle_suite_puzzle_included(
                connection,
                suite_id=suite_id,
                puzzle_id=puzzle_id,
                included=payload.included,
            )
            connection.commit()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json({"message": "Puzzle selection updated."})

    @app.post("/api/admin/tools/who-has-this")
    def admin_create_who_has_this(
        payload: WhoHasThisPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            job = create_tool_job(
                connection,
                tool_name="who_has_this",
                input_data={"option_name": payload.option_name},
                engine_version_ids=payload.engine_ids,
            )
            connection.commit()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "job": _tool_job_api_payload(connection, job, include_items=True),
                "message": "UCI option inspection queued.",
            },
            status_code=201,
        )

    @app.get("/api/admin/tools/jobs/{job_id}")
    def admin_tool_job(
        job_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        job = get_tool_job(connection, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Tool job not found.")
        return _json({"job": _tool_job_api_payload(connection, job, include_items=True)})

    @app.post("/api/admin/tools/jobs/{job_id}/cancel")
    def admin_cancel_tool_job(
        job_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        job = get_tool_job(connection, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Tool job not found.")
        if not cancel_tool_job(connection, job_id):
            raise HTTPException(
                status_code=409,
                detail="Only queued or running tool jobs can be cancelled.",
            )
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({"message": "Tool job cancelled. Its worker will be released shortly."})

    # Workers

    @app.get("/api/admin/workers")
    def admin_workers(
        request: Request,
        page: int = 1,
        per_page: int = 100,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        page = max(page, 1)
        per_page = min(max(per_page, 1), 200)
        return _json(
            web_app._workers_snapshot_payload(
                connection,
                worker_server_url=web_app._request_worker_server_url(request, connection),
                worker_limit=per_page,
                worker_offset=(page - 1) * per_page,
            )
        )

    @app.post("/api/admin/workers")
    def admin_create_worker(
        payload: WorkerPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        worker_id = create_worker(
            connection,
            label=payload.label.strip(),
        )
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json(
            {"id": worker_id, "message": "Worker created."},
            status_code=201,
        )

    @app.get("/api/admin/workers/{worker_id}")
    def admin_worker(
        worker_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        row = web_app._worker_admin_row(connection, worker_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Worker not found.")
        return _json(
            web_app._worker_admin_api_payload(
                row,
                connection=connection,
                worker_server_url=web_app._request_worker_server_url(request, connection),
            )
        )

    @app.post("/api/admin/workers/{worker_id}/token")
    def admin_worker_token(
        worker_id: int,
        payload: WorkerTokenPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_worker(connection, worker_id) is None:
            raise HTTPException(status_code=404, detail="Worker not found.")
        try:
            minted = mint_worker_token_for_worker(
                connection,
                worker_id=worker_id,
                ttl_seconds=payload.ttl_seconds,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        connection.commit()
        _publish_admin_change(web_app, request)
        worker = get_worker(connection, worker_id)
        if worker is None:
            raise HTTPException(status_code=404, detail="Worker not found.")
        command = (
            f"cope worker --server-url "
            f"{web_app._command_arg(web_app._request_worker_server_url(request, connection))} "
            f"--token {web_app._command_arg(minted.token)}"
        )
        response = _json(
            {
                "token": minted.token,
                "expires_at": minted.expires_at,
                "start_command": command,
                "message": "One-time worker token generated.",
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.put("/api/admin/workers/{worker_id}/label")
    def admin_worker_label(
        worker_id: int,
        payload: WorkerPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_worker(connection, worker_id) is None:
            raise HTTPException(status_code=404, detail="Worker not found.")
        update_worker_label(connection, worker_id, payload.label.strip())
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({"message": "Worker renamed."})

    @app.put("/api/admin/workers/{worker_id}/settings")
    def admin_worker_settings(
        worker_id: int,
        payload: WorkerSettingsPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_worker(connection, worker_id) is None:
            raise HTTPException(status_code=404, detail="Worker not found.")
        try:
            update_worker_assignment_settings(
                connection,
                worker_id,
                core_limit=payload.core_limit,
                tournament_scope=payload.tournament_scope,
                tournament_ids=payload.tournament_ids,
                event_ids=payload.event_ids,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({"message": "Worker assignment limits updated."})

    @app.post("/api/admin/workers/{worker_id}/revoke")
    def admin_worker_revoke(
        worker_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_worker(connection, worker_id) is None:
            raise HTTPException(status_code=404, detail="Worker not found.")
        revoke_worker(connection, worker_id)
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({"message": "Worker revoked and removed."})

    @app.delete("/api/admin/workers/{worker_id}")
    def admin_worker_delete(
        worker_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_worker(connection, worker_id) is None:
            raise HTTPException(status_code=404, detail="Worker not found.")
        try:
            delete_worker(connection, worker_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({"message": "Worker deleted."})

    @app.delete("/api/admin/benchmarkers/{benchmarker_id}")
    def admin_benchmarker_forget(
        benchmarker_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_benchmarker(connection, benchmarker_id) is None:
            raise HTTPException(status_code=404, detail="Benchmarker not found.")
        forget_benchmarker(connection, benchmarker_id)
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({"message": "Benchmarker revoked."})

    @app.post("/api/admin/benchmarkers")
    def admin_create_benchmarker(
        payload: BenchmarkerPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        minted = mint_benchmarker_token(
            connection,
            label=payload.label,
            ttl_seconds=payload.ttl_seconds,
        )
        connection.commit()
        _publish_admin_change(web_app, request)
        command = (
            f"cope benchmarker --server-url "
            f"{web_app._command_arg(web_app._request_benchmarker_server_url(request, connection))} "
            f"--token {web_app._command_arg(minted.token)}"
        )
        response = _json(
            {
                "id": minted.benchmarker_id,
                "token": minted.token,
                "expires_at": minted.expires_at,
                "start_command": command,
                "message": "One-time benchmarker token generated.",
            },
            status_code=201,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    # Chat moderation

    @app.get("/api/admin/chat")
    def admin_chat(connection: sqlite3.Connection = Depends(web_app._database)):
        return _json(
            {
                "messages": list_chat_messages(connection, limit=100),
                "tournament_names": web_app._tournament_names(connection),
                "event_names": web_app._event_names(connection),
                "settings": get_chat_settings(connection),
            }
        )

    @app.put("/api/admin/chat/settings")
    def admin_chat_settings(
        payload: ChatSettingsPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        current = get_chat_settings(connection)
        settings = ChatSettingsRecord(
            enabled=payload.enabled,
            slowmode_seconds=current.slowmode_seconds,
            max_message_length=payload.max_message_length,
            allow_anonymous_names=payload.allow_anonymous_names,
            retention_days=current.retention_days,
        )
        update_chat_settings(connection, settings)
        connection.commit()
        web_app._publish_chat_settings_change(request, connection, settings)
        _publish_admin_change(web_app, request)
        return _json({"settings": settings, "message": "Chat settings updated."})

    @app.delete("/api/admin/chat/messages/{message_id}")
    def admin_chat_delete(
        message_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        deleted = delete_chat_message(connection, message_id)
        if deleted is None:
            raise HTTPException(status_code=404, detail="Message not found.")
        connection.commit()
        web_app._publish_chat_deletion(
            request,
            tournament_id=deleted.tournament_id,
            event_id=deleted.event_id,
            message_id=deleted.id,
        )
        _publish_admin_change(web_app, request)
        return _json({"message": "Message deleted."})

    register_event_api_routes(app)


def _require_viewable_event(
    connection: sqlite3.Connection,
    slug: str,
    request: Request,
) -> EventRecord:
    from cope.web import app as web_app

    event = get_event_by_slug(connection, slug)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found.")
    if web_app._event_is_public(event):
        return event
    if not web_app._admin_request_authenticated(request):
        raise HTTPException(status_code=401, detail="Admin session required.")
    return event


def _event_summary_payload(
    connection: sqlite3.Connection,
    event: EventRecord,
    *,
    admin: bool,
) -> dict[str, Any]:
    module = get_event_module(event.handler_key)
    sessions = list_event_sessions(connection, event.id)
    next_session = next(
        (
            session
            for session in sessions
            if session.status in {"live", "intermission", "scheduled", "pending", "postponed"}
        ),
        None,
    )
    payload = {
        "record": event if admin else _public_event_record(event),
        "counts": event_resource_counts(connection, event.id),
        "next_session": (
            next_session
            if admin or next_session is None
            else _public_event_child(next_session)
        ),
        "handler": {
            "key": event.handler_key,
            "required_version": event.handler_version,
            "available": module is not None,
            "current": module is not None and module.version == event.handler_version,
            "label": module.label if module is not None else event.handler_key,
            "installed_version": module.version if module is not None else None,
        },
    }
    return payload


def _event_detail_payload(
    connection: sqlite3.Connection,
    event: EventRecord,
    *,
    admin: bool,
) -> dict[str, Any]:
    module = get_event_module(event.handler_key)
    defaults = get_chat_settings(connection)
    chat_settings = get_event_chat_settings(connection, event.id, defaults=defaults)
    stages = list_event_stages(connection, event.id)
    sessions = list_event_sessions(connection, event.id)
    cast = list_event_cast(connection, event.id)
    contests = list_event_contests(connection, event.id)
    contest_cast = list_event_contest_cast(connection, event.id)
    updates = list_event_updates(connection, event.id, public_only=not admin)
    awards = list_event_awards(connection, event.id)
    if not admin:
        stages = tuple(_public_event_child(item) for item in stages)
        sessions = tuple(_public_event_child(item) for item in sessions)
        cast = tuple(_public_event_child(item) for item in cast)
        contests = tuple(_public_event_child(item, remove=("state",)) for item in contests)
        contest_cast = tuple(_public_event_child(item) for item in contest_cast)
        awards = tuple(_public_event_child(item) for item in awards)
    return {
        "server_time": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "event": event if admin else _public_event_record(event),
        "handler": {
            "key": event.handler_key,
            "required_version": event.handler_version,
            "available": module is not None,
            "current": module is not None and module.version == event.handler_version,
            "label": module.label if module is not None else event.handler_key,
            "installed_version": module.version if module is not None else None,
        },
        "stages": stages,
        "sessions": sessions,
        "cast": cast,
        "contests": contests,
        "contest_cast": contest_cast,
        "updates": updates,
        "awards": awards,
        "counts": event_resource_counts(connection, event.id),
        "chat_messages": (
            list_chat_messages(connection, limit=None, event_id=event.id, system=False)
            + list_chat_messages(connection, limit=100, event_id=event.id, system=True)
        ),
        "chat_settings": chat_settings,
        "custom": event_extension_payload(connection, event, admin=admin),
    }


def _public_event_record(event: EventRecord) -> dict[str, Any]:
    payload = jsonable_encoder(event)
    payload.pop("config", None)
    payload.pop("state", None)
    return payload


def _public_event_child(item: Any, *, remove: tuple[str, ...] = ()) -> dict[str, Any]:
    payload = jsonable_encoder(item)
    payload.pop("metadata", None)
    for key in remove:
        payload.pop(key, None)
    return payload


def _public_engine_ratings(
    connection: sqlite3.Connection,
    engine_id: int,
) -> list[dict[str, Any]]:
    ratings = connection.execute(
        """
        WITH ranked AS (
          SELECT rating.*,
                 ROW_NUMBER() OVER (
                   PARTITION BY rating.rating_list_id
                   ORDER BY rating.elo DESC, engine.name, version.version
                 ) AS rating_rank,
                 COUNT(*) OVER (
                   PARTITION BY rating.rating_list_id
                 ) AS field_size
          FROM rating_list_ratings rating
          JOIN engine_versions version ON version.id = rating.engine_id
          JOIN engines engine ON engine.id = version.engine_id
        )
        SELECT rating_list.*, ranked.elo, ranked.games_played,
               ranked.updated_at, ranked.rating_rank, ranked.field_size,
               ranked.error_margin
        FROM ranked
        JOIN rating_lists rating_list ON rating_list.id = ranked.rating_list_id
        WHERE ranked.engine_id = ?
        ORDER BY rating_list.name, rating_list.id
        """,
        (engine_id,),
    ).fetchall()
    raw_history = connection.execute(
        """
        SELECT rating_list_id, elo, calculated_at
        FROM engine_elo_history
        WHERE engine_id = ?
        ORDER BY rating_list_id, id
        """,
        (engine_id,),
    )
    history_by_list: dict[int, list[dict[str, Any]]] = {}
    for item in raw_history:
        rating_list_id = int(item["rating_list_id"])
        history = history_by_list.setdefault(rating_list_id, [])
        elo = float(item["elo"])
        history.append(
            {
                "elo": elo,
                "change": elo - history[-1]["elo"] if history else 0.0,
                "at": item["calculated_at"],
            }
        )
    result: list[dict[str, Any]] = []
    for row in ratings:
        history = history_by_list.get(int(row["id"]), [])
        elo = float(row["elo"])
        result.append(
            {
                "rating_list": {
                    "id": int(row["id"]),
                    "name": str(row["name"]),
                    "anchor_engine_id": row["anchor_engine_id"],
                    "anchor_elo": float(row["anchor_elo"]),
                    "created_at": str(row["created_at"]),
                },
                "elo": elo,
                "rank": int(row["rating_rank"]),
                "field_size": int(row["field_size"]),
                "games_played": int(row["games_played"]),
                "error_margin": (
                    float(row["error_margin"])
                    if row["error_margin"] is not None
                    else None
                ),
                "updated_at": row["updated_at"],
                "peak_elo": max(
                    (point["elo"] for point in history),
                    default=elo,
                ),
                "history": history,
            }
        )
    return result


def _json(
    payload: Any,
    *,
    status_code: int = 200,
) -> JSONResponse:
    return JSONResponse(jsonable_encoder(payload), status_code=status_code)


def _settings_rows(rows: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"label": label, "value": value} for label, value in rows]


def _require_tournament(connection: sqlite3.Connection, tournament_id: int):
    tournament = get_tournament(connection, tournament_id)
    if tournament is None:
        raise HTTPException(status_code=404, detail="Tournament not found.")
    return tournament


def _require_tournament_game(
    connection: sqlite3.Connection,
    tournament_id: int,
    game_id: int,
):
    game = get_game(connection, game_id)
    if game is None or game.tournament_id != tournament_id:
        raise HTTPException(status_code=404, detail="Game not found in this tournament.")
    return game


def _ensure_tournament_games_mutable(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> None:
    locked = next(
        (
            commit
            for commit in list_tournament_rating_commits(connection, tournament_id)
            if commit.status in {"pending", "claimed", "applied"}
        ),
        None,
    )
    if locked is not None:
        raise HTTPException(
            status_code=409,
            detail="Uncommit the tournament ratings before changing its games.",
        )


def _validate_live_participant_engine(
    connection: sqlite3.Connection,
    engine_id: int,
) -> None:
    record = get_engine_record(connection, engine_id)
    if record is None:
        raise HTTPException(status_code=422, detail="Choose an existing engine version.")
    if not _engine_is_tournament_ready(connection, record):
        raise HTTPException(
            status_code=422,
            detail="Choose an active engine version that is ready to run.",
        )


def _engine_is_tournament_ready(
    connection: sqlite3.Connection,
    record,
) -> bool:
    if not record.active or not record.engine_active or not record.version.strip():
        return False
    if record.distribution == "worker_local":
        return True
    return engine_build_is_benchmarked(
        connection,
        engine_version_id=record.id,
        build_hash=record.build_hash,
    )


def _live_tournament_roster_payload(
    connection: sqlite3.Connection,
    tournament,
) -> dict[str, Any]:
    supported = tournament.config.format.value in {"round_robin", "gauntlet"}
    editable = tournament.status in {"running", "paused"} and supported
    if tournament.status not in {"running", "paused"}:
        reason = "Live roster changes are available while the tournament is running or paused."
    elif not supported:
        reason = "Swiss pairings and knockout brackets are fixed after the tournament starts."
    else:
        reason = ""
    participant_ids = set(tournament.config.participants)
    available_engines = (
        [
            engine
            for engine in list_engine_records(connection)
            if engine.id not in participant_ids
            and _engine_is_tournament_ready(connection, engine)
        ]
        if editable
        else []
    )
    summaries = {
        engine_id: {
            "engine_id": engine_id,
            "total": 0,
            "pending": 0,
            "assigned": 0,
            "live": 0,
            "finished": 0,
            "abandoned": 0,
        }
        for engine_id in tournament.config.participants
    }
    rows = connection.execute(
        """
        SELECT engine_id, status, COUNT(*) AS count
        FROM (
          SELECT white_engine_id AS engine_id, status
          FROM games WHERE tournament_id = ?
          UNION ALL
          SELECT black_engine_id AS engine_id, status
          FROM games WHERE tournament_id = ?
        ) participant_games
        GROUP BY engine_id, status
        """,
        (tournament.id, tournament.id),
    )
    for row in rows:
        engine_id = int(row["engine_id"])
        if engine_id not in summaries:
            continue
        count = int(row["count"])
        summaries[engine_id][str(row["status"])] = count
        summaries[engine_id]["total"] += count
    format_options = tournament.config.format_options.model_dump(mode="json")
    return {
        "editable": editable,
        "reason": reason,
        "available_engines": available_engines,
        "participants": [
            summaries[engine_id]
            for engine_id in tournament.config.participants
        ],
        "hero_engine_id": format_options.get("hero_engine_id"),
    }


def _category_settings(value: dict[str, Any]) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "format": "round_robin",
        "format_options": {"cycles": 1},
        "time_control": {
            "category": "increment",
            "initial_ms": 60_000,
            "increment_ms": 1_000,
        },
        "concurrency": 1,
        "opening_suite_id": None,
        "adjudication": {
            "draw": None,
            "resign": None,
            "max_moves": None,
        },
        "rated": True,
        "lag_compensation_ms": 50,
        "engine_threads": 1,
        "engine_hash_mb": 16,
    }
    category_keys = {
        "time_control",
        "adjudication",
        "rated",
        "lag_compensation_ms",
        "engine_threads",
        "engine_hash_mb",
    }
    merged = {**defaults, **{key: item for key, item in value.items() if key in category_keys}}
    try:
        config = TournamentConfig(
            participants=[1, 2],
            **merged,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=[error["msg"] for error in exc.errors()],
        ) from exc
    serialized = config.model_dump(mode="json")
    for key in ("participants", "uci_options"):
        serialized.pop(key, None)
    return serialized


def _validated_tournament_config(
    connection: sqlite3.Connection,
    submitted: TournamentConfig,
) -> TournamentConfig:
    records = {engine.id: engine for engine in list_engine_records(connection)}
    missing = [engine_id for engine_id in submitted.participants if engine_id not in records]
    if missing:
        raise HTTPException(status_code=422, detail="One or more selected engines no longer exist.")
    unavailable = [
        engine_id for engine_id in submitted.participants
        if not _engine_is_tournament_ready(connection, records[engine_id])
    ]
    if unavailable:
        raise HTTPException(status_code=422, detail="Every participant must be active and ready to run.")

    _validate_opening_suite_reference(connection, submitted.opening_suite_id)
    return submitted


def _validate_opening_suite_reference(
    connection: sqlite3.Connection,
    suite_id: int | None,
) -> None:
    if suite_id is not None and get_opening_suite(connection, suite_id) is None:
        raise HTTPException(status_code=422, detail="Choose an existing opening suite.")


def _tournament_form_payload(
    web_app,
    request: Request,
    connection: sqlite3.Connection,
    *,
    tournament=None,
) -> dict[str, Any]:
    if tournament is not None:
        config = tournament.config.model_dump(mode="json")
        name = tournament.name
        participants = list(tournament.config.participants)
    else:
        default_settings = _category_settings({})
        config = {
            "participants": [],
            "engine_threads": 1,
            "engine_hash_mb": 16,
            "uci_options": {},
            **default_settings,
        }
        name = ""
        participants = []

    participant_ids = set(participants)
    engines = [
        engine
        for engine in list_engine_records(connection)
        if (
            _engine_is_tournament_ready(connection, engine)
        ) or engine.id in participant_ids
    ]
    return {
        "name": name,
        "config": config,
        "participants": participants,
        "engine_options": engines,
        "opening_suites": list_opening_suites(connection),
    }


def _create_opening_import(
    connection: sqlite3.Connection,
    *,
    name: str,
    description: str,
    openings: list[OpeningLine],
) -> int:
    try:
        suite_id = create_opening_suite(
            connection,
            name=name,
            description=description,
        )
        replace_suite_openings(connection, suite_id, openings)
        connection.commit()
        return suite_id
    except Exception:
        connection.rollback()
        raise


def _update_opening_import(
    connection: sqlite3.Connection,
    suite_id: int,
    *,
    name: str,
    description: str,
    openings: list[OpeningLine],
    mode: str,
) -> int:
    try:
        update_opening_suite(
            connection,
            suite_id,
            name=name,
            description=description,
        )
        if mode == "append":
            append_suite_openings(connection, suite_id, openings)
        elif mode == "replace":
            replace_suite_openings(connection, suite_id, openings)
        position_count = suite_opening_count(connection, suite_id)
        connection.commit()
        return position_count
    except Exception:
        connection.rollback()
        raise


async def _read_opening_form(
    request: Request,
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    form = await request.form()
    values: dict[str, str] = {}
    files: list[tuple[str, str]] = []
    for key, value in form.multi_items():
        if not isinstance(value, UploadFile):
            values[key] = str(value)
            continue
        if not value.filename:
            continue
        content = await value.read()
        files.append(
            (
                value.filename,
                content.decode("utf-8-sig", errors="replace"),
            )
        )
    return values, files


def _opening_values(
    positions: str,
    files: list[tuple[str, str]],
) -> list[OpeningLine]:
    try:
        openings = parse_opening_input(positions, files)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    openings = _deduplicate_openings(openings)
    if not openings:
        raise HTTPException(
            status_code=422,
            detail="Add at least one valid opening position.",
        )
    return openings


def _deduplicate_openings(
    openings: list[OpeningLine],
) -> list[OpeningLine]:
    result: list[OpeningLine] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for opening in openings:
        normalized = opening.start_fen.strip()
        key = (normalized, opening.moves)
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(opening)
    return result


def _tool_job_api_payload(connection, job, *, include_items: bool) -> dict[str, Any]:
    input_payload = dict(job.input)
    puzzles = input_payload.pop("puzzles", None)
    if isinstance(puzzles, list):
        input_payload["puzzle_count"] = len(puzzles)
    payload: dict[str, Any] = {
        "id": job.id,
        "tool_name": job.tool_name,
        "status": job.status,
        "input": input_payload,
        "worker": (
            None
            if job.worker_id is None
            else {"id": job.worker_id, "label": job.worker_label or f"Worker {job.worker_id}"}
        ),
        "total_items": job.total_items,
        "completed_items": job.completed_items,
        "required_threads": job.required_threads,
        "required_hash_mb": job.required_hash_mb,
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
        "progress_detail": job.progress_detail,
        "attempt": job.attempt,
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }
    if include_items:
        payload["items"] = [
            {
                "id": item.id,
                "engine_id": item.engine_version_id,
                "engine_name": item.engine_name,
                "engine_version": item.engine_version,
                "position": item.position,
                "status": item.status,
                "result": item.result,
                "error": item.error,
                "started_at": item.started_at,
                "finished_at": item.finished_at,
            }
            for item in list_tool_job_items(connection, job.id)
        ]
    return payload


def _parse_puzzle_suite_block(value: str) -> list[dict[str, Any]]:
    lines = tuple(
        (number, line.strip())
        for number, line in enumerate(value.splitlines(), start=1)
        if line.strip()
    )
    if not lines:
        raise ValueError("paste at least one puzzle")
    if len(lines) > 5000:
        raise ValueError("a puzzle suite can contain at most 5,000 puzzles")
    puzzles: list[dict[str, Any]] = []
    for number, line in lines:
        if line.count("|") != 1:
            raise ValueError(f"line {number} must use fen|solution")
        fen, solution_text = (part.strip() for part in line.split("|", 1))
        raw_solutions = [part.strip() for part in re.split(r"[,/]+", solution_text) if part.strip()]
        if not fen:
            raise ValueError(f"line {number} is missing a FEN")
        if not raw_solutions:
            raise ValueError(f"line {number} is missing a solution")
        try:
            board = chess.Board(fen)
        except ValueError as exc:
            raise ValueError(f"line {number} has an invalid FEN: {exc}") from exc
        if board.is_game_over() or not any(board.legal_moves):
            raise ValueError(f"line {number} must contain a position with a legal move")
        solutions: list[str] = []
        for raw in raw_solutions:
            try:
                if re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", raw, re.IGNORECASE):
                    move = chess.Move.from_uci(raw.lower())
                    if move not in board.legal_moves:
                        raise ValueError
                else:
                    move = board.parse_san(raw)
            except ValueError as exc:
                raise ValueError(f"line {number}: {raw!r} is not a legal solution move") from exc
            if move.uci() not in solutions:
                solutions.append(move.uci())
        puzzles.append({"fen": board.fen(), "solutions": solutions, "title": ""})
    return puzzles


def _ensure_puzzle_suite_idle(connection, suite_id: int) -> None:
    row = connection.execute(
        """
        SELECT job.id
        FROM puzzle_suite_runs run
        JOIN tool_jobs job ON job.id = run.job_id
        WHERE run.suite_id = ? AND job.status IN ('queued', 'running')
        LIMIT 1
        """,
        (suite_id,),
    ).fetchone()
    if row is not None:
        raise HTTPException(status_code=409, detail="This suite already has an active stage.")


def _puzzle_suite_missed_puzzle_ids(
    connection,
    suite_id: int,
) -> tuple[int, set[int]] | None:
    for run in list_puzzle_suite_runs(connection, suite_id, limit=200):
        if (
            run.stage != "difficulty"
            or run.settings.get("suite_stage") == "miss_finetuning"
        ):
            continue
        job = get_tool_job(connection, run.job_id)
        if job is None or job.status != "completed":
            continue
        results = list_puzzle_suite_engine_results(connection, run.id)
        searched = {result.puzzle_id for result in results}
        solved = {
            result.puzzle_id for result in results if result.status == "solved"
        }
        return run.id, searched - solved
    return None


def _puzzle_suite_summary_payload(connection, suite) -> dict[str, Any]:
    puzzles = list_puzzle_suite_puzzles(connection, suite.id)
    runs = list_puzzle_suite_runs(connection, suite.id, limit=1)
    active_job = None
    if runs:
        job = get_tool_job(connection, runs[0].job_id)
        if job is not None and job.status in {"queued", "running"}:
            active_job = _tool_job_api_payload(connection, job, include_items=False)
    return {
        "id": suite.id,
        "name": suite.name,
        "puzzle_count": len(puzzles),
        "unique_count": sum(puzzle.uniqueness_status == "unique" for puzzle in puzzles),
        "included_count": sum(puzzle.included for puzzle in puzzles),
        "rated_count": sum(puzzle.difficulty_elo is not None for puzzle in puzzles),
        "active_job": active_job,
        "created_at": suite.created_at,
        "updated_at": suite.updated_at,
    }


def _puzzle_suite_detail_payload(connection, suite) -> dict[str, Any]:
    puzzles = list_puzzle_suite_puzzles(connection, suite.id)
    runs = list_puzzle_suite_runs(connection, suite.id)
    run_payloads = []
    latest_difficulty_results: tuple[Any, ...] = ()
    latest_miss_results: tuple[Any, ...] = ()
    difficulty_results_selected = False
    miss_results_selected = False
    miss_selection = _puzzle_suite_missed_puzzle_ids(connection, suite.id)
    source_difficulty_run_id = None if miss_selection is None else miss_selection[0]
    for run in runs:
        job = get_tool_job(connection, run.job_id)
        if job is None:
            continue
        run_payloads.append(
            {
                "id": run.id,
                "stage": run.settings.get("suite_stage", run.stage),
                "rating_list_id": run.rating_list_id,
                "settings": run.settings,
                "created_at": run.created_at,
                "job": _tool_job_api_payload(connection, job, include_items=True),
            }
        )
        if (
            run.stage == "difficulty"
            and run.id == source_difficulty_run_id
            and not difficulty_results_selected
        ):
            latest_difficulty_results = list_puzzle_suite_engine_results(connection, run.id)
            difficulty_results_selected = True
        if (
            run.stage == "difficulty"
            and run.settings.get("suite_stage") == "miss_finetuning"
            and not miss_results_selected
            and run.settings.get("source_difficulty_run_id") == source_difficulty_run_id
        ):
            latest_miss_results = list_puzzle_suite_engine_results(connection, run.id)
            miss_results_selected = True
    miss_result_puzzle_ids = {result.puzzle_id for result in latest_miss_results}
    displayed_engine_results = (
        tuple(
            result
            for result in latest_difficulty_results
            if result.puzzle_id not in miss_result_puzzle_ids
        )
        + latest_miss_results
    )
    return {
        **_puzzle_suite_summary_payload(connection, suite),
        "difficulty_run_id": source_difficulty_run_id,
        "miss_count": 0 if miss_selection is None else len(miss_selection[1]),
        "puzzles": [
            {
                "id": puzzle.id,
                "position": puzzle.position,
                "title": puzzle.title,
                "fen": puzzle.fen,
                "solutions": list(puzzle.solutions),
                "included": puzzle.included,
                "uniqueness_status": puzzle.uniqueness_status,
                "verified_solution": puzzle.verified_solution,
                "best_move": puzzle.best_move,
                "second_move": puzzle.second_move,
                "best_sigmoid": puzzle.best_sigmoid,
                "second_sigmoid": puzzle.second_sigmoid,
                "sigmoid_gap": puzzle.sigmoid_gap,
                "uniqueness_depth": puzzle.uniqueness_depth,
                "uniqueness_nodes": puzzle.uniqueness_nodes,
                "uniqueness_time_ms": puzzle.uniqueness_time_ms,
                "uniqueness_error": puzzle.uniqueness_error,
                "difficulty_elo": puzzle.difficulty_elo,
            }
            for puzzle in puzzles
        ],
        "runs": run_payloads,
        "engine_results": [
            {
                "id": result.id,
                "run_id": result.run_id,
                "puzzle_id": result.puzzle_id,
                "engine_id": result.engine_version_id,
                "engine_name": result.engine_name,
                "engine_version": result.engine_version,
                "engine_elo": result.engine_elo,
                "estimate_elo": result.estimate_elo,
                "status": result.status,
                "best_move": result.best_move,
                "solution_nodes": result.solution_nodes,
                "final_nodes": result.final_nodes,
                "depth": result.depth,
                "time_ms": result.time_ms,
                "error": result.error,
            }
            for result in displayed_engine_results
        ],
    }


def _publish_admin_change(web_app, request: Request) -> None:
    try:
        web_app._publish_admin_post_streams(request)
    except Exception:
        LOG.exception("admin change committed but live publication failed path=%s", request.url.path)
