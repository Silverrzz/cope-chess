from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import urllib.parse
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import psycopg
from cope.adapters.http.environment_clone import register_environment_clone_routes
from cope.adapters.http.events import _require_viewable_event, register_event_routes
from cope.adapters.http.puzzle_tools import register_puzzle_tools_routes
from cope.adapters.http.responses import _json, _publish_admin_change
from cope.adapters.http.tools import register_tools_routes
from cope.adapters.http.tournament_config import (
    TournamentPayload,
    _engine_is_tournament_ready,
    _tournament_form_payload,
    _validate_engine_variant,
    _validated_tournament_config,
)
from cope.adapters.http.tournament_tools import register_tournament_tools_routes
from cope.application import ApplicationError, Principal as ApplicationPrincipal
from cope.application.commands import execute_compatibility
from cope.application.identity import bootstrap_authority_lock,bootstrap_secret,current_principal,revoke_browser_credential
from cope.adapters.http.browser_identity import cookie_browser_principal,request_browser_principal
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from starlette.datastructures import UploadFile

from cope.chat import announce_tournament_finished
from cope.core.models import EngineArtifactSpec, HardwareInfo, OpeningLine
from cope.db import engine_version_tags
from cope.db.donors import list_donor_workers, set_donor_group
from cope.environment_clone import (
    authorize_environment_export,
    clone_catalog_payload,
    create_environment_export,
    engine_artifact_root,
    environment_export_artifact,
    environment_export_dataset_path,
    environment_export_payload,
    environment_instance_id,
    environment_inventory,
    remove_clone_transfer_tree,
)
from cope.db import (
    ChatSettingsRecord,
    CopeBuildSettingsRecord,
    PlatformSettingsRecord,
    append_suite_openings,
    create_deployment_job,
    create_dockerfile_pull_job,
    create_engine,
    create_engine_version,
    create_git_host,
    create_opening_suite,
    create_rating_list,
    create_tournament,
    create_worker,
    connect_database,
    count_games,
    create_badge,
    database_stats,
    database_schema_version,
    delete_chat_message,
    delete_badge,
    delete_engine,
    delete_engine_version,
    delete_git_host,
    delete_opening_suite,
    delete_rating_list,
    delete_tournament,
    delete_worker,
    engine_game_filter_options,
    engine_result_summary,
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
    get_engine,
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
    invalidate_game_pair,
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
    list_engine_records,
    list_engine_result_summaries,
    list_engine_benchmark_jobs,
    list_engines,
    list_engine_families,
    list_engine_versions,
    list_git_hosts,
    list_games_page,
    list_games_by_status,
    list_active_games,
    list_opening_suites,
    list_rating_lists,
    list_rating_rows,
    list_service_heartbeats,
    list_suite_openings,
    list_tournaments,
    list_tournament_rating_commits,
    list_uncommitted_finished_tournaments,
    mint_benchmarker_token,
    mint_worker_token_for_worker,
    replace_suite_openings,
    replace_badge_engines,
    replay_game,
    record_manual_benchmark,
    register_engine_artifact,
    reschedule_engine_benchmarks,
    schedule_benchmark_jobs,
    request_tournament_rating_commit,
    restore_tournament,
    resume_paused_tournament_games,
    revoke_worker,
    schedule_tournament,
    set_tournament_concurrency,
    set_tournament_status,
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
from cope.variants import VariantId
from cope.db.repo import get_tournament_game, reorder_rating_lists, set_rating_list_variant, update_rating_list
from cope.web.forms import form_value
from cope.web.requests import read_form
from cope.version import app_version
from cope.ratings import (
    create_historical_rating_list,
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


def _current_bootstrap_admin(request, connection, *, exclusive=False):
    try:
        principal = request_browser_principal(request)
        raw = connection.native_connection()
        bootstrap_authority_lock(raw,exclusive=exclusive)
        identity = current_principal(raw,principal)
        identity.require('*')
        return raw
    except ApplicationError as error:
        raise HTTPException(error.status,error.payload()) from error
    except (psycopg.errors.LockNotAvailable,psycopg.errors.DeadlockDetected,psycopg.errors.SerializationFailure) as error:
        raise HTTPException(409,{'code':'concurrent_change','message':'Credential authority changed. Refresh before retrying.'}) from error


LOG = logging.getLogger("cope.web.api")
OPENING_EDITOR_POSITION_LIMIT = 10_000
ADMIN_GAME_RESULT_TYPES = frozenset(
    {
        "win_checkmate",
        "win_king_capture",
        "win_no_legal_move",
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


def _engine_version_admin_payload(version, tags) -> dict[str, Any]:
    payload = jsonable_encoder(version)
    payload["benchmark_current"] = version.benchmark_current
    payload["active"] = version.active
    payload["tags"] = tags.get(version.id, [])
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
        ORDER BY rating_list.display_order NULLS LAST, rating_list.name, rating_list.id
        """
    )
    return [
        {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "description": row["description"],
            "display_order": row["display_order"],
            "variant": row["variant"],
            "anchor_engine_id": row["anchor_engine_id"],
            "anchor_elo": float(row["anchor_elo"]),
            "hardware_scaled": row["hardware_scaled"],
            "monthly_refresh": row["monthly_refresh"],
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
        selected_games AS MATERIALIZED (
          SELECT game.id, game.tournament_id
          FROM tournament_games game
          WHERE game.status = 'finished'
            AND game.record_eligible = 1
            AND game.result IN ('1-0', '0-1', '1/2-1/2')
            AND game.tournament_id IN (
              SELECT tournament_id FROM selected_commits
            )
        ),
        hardware_by_game AS (
          SELECT score.game_id
          FROM game_hardware_scores score
          JOIN (SELECT DISTINCT id FROM selected_games) game ON game.id = score.game_id
          GROUP BY score.game_id
          HAVING COUNT(*) = 2
        ),
        game_stats AS (
          SELECT game.tournament_id,
                 COUNT(*) AS games,
                 COUNT(*) FILTER (WHERE hardware.game_id IS NOT NULL) AS hardware_games
          FROM selected_games game
          LEFT JOIN hardware_by_game hardware ON hardware.game_id = game.id
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


def _engine_queues_payload(connection: sqlite3.Connection) -> dict[str, Any]:
    from cope.control.intake import queue_snapshot

    snapshot = queue_snapshot(connection)
    return {
        "revision": snapshot["revision"],
        "acknowledged": [{key: item[key] for key in ("id", "name", "version", "submitted_at")} for item in snapshot["acknowledged"]],
        "waiting_for_test": [{key: item[key] for key in (
            "id", "engine_version_id", "rating_list_id", "engine_name", "engine_version", "rating_list_name",
            "preset_key", "status", "reason", "tournament_id", "tournament_status", "submitted_at",
        )} for item in snapshot["waiting_for_test"]],
    }


class TournamentNamePayload(BaseModel):
    name: str = Field(min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("tournament name cannot be blank")
        return value


class TournamentParticipantPayload(BaseModel):
    engine_id: int = Field(gt=0)


class RatingCommitPayload(BaseModel):
    rating_list_ids: list[int] = Field(min_length=1)


class RatingListVariantPayload(BaseModel):
    variant: VariantId


class RatingListPayload(BaseModel):
    variant: VariantId = "standard"
    name: str = Field(min_length=1, max_length=120)
    hardware_scaled: bool = False
    monthly_refresh: bool = False
    description: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("rating list name cannot be blank")
        return value


class RatingListUpdatePayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    variant: VariantId | None = None
    hardware_scaled: bool | None = None
    monthly_refresh: bool | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        return None if value is None else RatingListPayload.strip_name(value)


class RatingListOrderPayload(BaseModel):
    rating_list_ids: list[int] = Field(min_length=1)


class HistoricalRatingListPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tournament_name: str = Field(min_length=1, max_length=160)
    source_rating_list_id: int = Field(gt=0)
    engine_version_ids: list[int] = Field(min_length=2)
    expected_game_count: int = Field(gt=0)

    @field_validator("name", "tournament_name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return RatingListPayload.strip_name(value)


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


class AcknowledgedEngineQueueItemPayload(BaseModel):
    id: int | None = Field(default=None, gt=0)
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=120)

    @field_validator("name", "version")
    @classmethod
    def strip_value(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("queue values cannot be blank")
        return value


class WaitingEngineQueueItemPayload(BaseModel):
    id: int | None = Field(default=None, gt=0)
    preset_key: str = "legacy"
    engine_version_id: int = Field(gt=0)
    rating_list_id: int = Field(gt=0)


class EngineQueuesPayload(BaseModel):
    revision: int = Field(ge=0)
    acknowledged: list[AcknowledgedEngineQueueItemPayload] = Field(max_length=1000)
    waiting_for_test: list[WaitingEngineQueueItemPayload] = Field(max_length=1000)

    @model_validator(mode="after")
    def unique_waiting_plans(self) -> EngineQueuesPayload:
        plans = {
            (item.engine_version_id, item.rating_list_id, item.preset_key)
            for item in self.waiting_for_test
        }
        if len(plans) != len(self.waiting_for_test):
            raise ValueError("waiting test plans must be unique")
        return self


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


class EngineVersionTagPayload(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class EngineVersionMetadataPayload(BaseModel):
    public_hidden: bool | None = None
    tag_ids: list[int] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_metadata(self):
        if self.public_hidden is None and self.tag_ids is None:
            raise ValueError("Specify visibility or tags.")
        return self


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


class ScheduleEngineBenchmarkPayload(BaseModel):
    hardware_key: str = Field(pattern=r"^[0-9a-f]{64}$")


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


class DonorGroupPayload(BaseModel):
    worker_ids: list[int] = Field(min_length=1, max_length=1000)
    name: str | None = Field(default=None, max_length=80)


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


class EnvironmentExportPayload(BaseModel):
    datasets: list[str] = Field(min_length=1, max_length=100)


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
    exclude_worker_ids: list[int] = Field(default_factory=list, max_length=100)

    @field_validator("exclude_worker_ids")
    @classmethod
    def validate_excluded_workers(cls, value: list[int]) -> list[int]:
        if any(not 0 < worker_id < 2**63 for worker_id in value):
            raise ValueError("excluded worker ids must be valid positive integers")
        return value

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


class ViewerIdentityPayload(BaseModel):
    viewer_id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=40)
    previous_name: str = Field(default="", max_length=40)


def register_api_routes(app: FastAPI) -> None:
    from cope.web import app as web_app
    from cope.web.public_urls import public_url_catalog, rename_public_slug

    @app.get("/api/public-urls")
    def public_urls(connection: sqlite3.Connection = Depends(web_app._database)):
        return _json(public_url_catalog(connection))

    @app.put("/api/admin/public-urls/{kind}/{resource_id}")
    def admin_rename_public_slug(
        kind: Literal["rating", "engine", "version", "tournament"],
        resource_id: int,
        slug: str = Body(embed=True, min_length=1, max_length=160),
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            rename_public_slug(connection, kind, resource_id, slug)
            connection.commit()
        except LookupError as exc:
            connection.rollback()
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _json({"slug": slug})

    @app.post("/api/viewer-identity")
    def update_viewer_identity(payload: ViewerIdentityPayload, request: Request):
        name = payload.name.strip()
        previous_name = payload.previous_name.strip()
        request.app.state.stream_hub.update_viewer_identity(
            payload.viewer_id,
            name if name.casefold() != "system" else "",
            previous_name if previous_name.casefold() != "system" else "",
        )
        return _json({"ok": True})

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
        cookie = web_app._signed_value(token,nonce)
        try:
            current_principal(connection.native_connection(),cookie_browser_principal(role,token,cookie))
            connection.commit()
        except ApplicationError as error:
            connection.rollback()
            raise HTTPException(error.status,error.payload()) from error
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
            cookie,
            httponly=True,
            secure=web_app._request_is_secure(request),
            samesite="lax",
            max_age=web_app.ADMIN_SESSION_MAX_AGE_SECONDS,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.delete("/api/session")
    def delete_session(request: Request,connection: sqlite3.Connection=Depends(web_app._database)):
        from cope.adapters.http.forms import require_origin

        require_origin(request)
        role = web_app._admin_session_role(request)
        principal = None
        if role is not None:
            secret = web_app._admin_token(request) if role=='admin' else web_app._manager_token(request)
            if not web_app._request_is_secure_or_local(request) or not web_app._csrf_token_valid(request,secret,request.headers.get('x-csrf-token','')):
                raise HTTPException(403,'CSRF validation failed')
            principal = request_browser_principal(request)
        try:
            revoke_browser_credential(connection.native_connection(),request.cookies.get('cope_admin_session',''),principal)
            connection.commit()
        except ApplicationError as error:
            connection.rollback()
            raise HTTPException(error.status,error.payload()) from error
        except (psycopg.errors.LockNotAvailable,psycopg.errors.DeadlockDetected,psycopg.errors.SerializationFailure) as error:
            connection.rollback()
            raise HTTPException(409,{'code':'concurrent_change','message':'The session could not be revoked. Retry signing out.'}) from error
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
        running_tournaments = web_app._home_tournament_cards(
            connection,
            engines,
            estimate_cache=request.app.state.tournament_estimate_cache,
        )
        for item in running_tournaments:
            summary = item["tournament"]
            summary["spectator_count"] = request.app.state.stream_hub.tournament_spectator_count(
                summary["record"].id
            )
        return _json(
            {
                **web_app._home_stats(connection),
                "running_tournaments": running_tournaments,
                "upcoming_rows": web_app._upcoming_rows(connection, engines, limit=16),
                "recent_games": list_games_by_status(connection, "finished", limit=16),
                "engines": engines,
                "tournament_names": web_app._tournament_names(connection),
                "engine_queues": _engine_queues_payload(connection),
            }
        )

    @app.get("/api/donors")
    def public_donors():
        from cope.web.donor_snapshot import read_donor_snapshot

        snapshot = read_donor_snapshot()
        if snapshot is None:
            raise HTTPException(status_code=503, detail="The donor leaderboard is being prepared.")
        return _json(snapshot)

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
            estimate_cache=request.app.state.tournament_estimate_cache,
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

    register_event_routes(app)


    @app.get("/api/events/{slug}/tournaments/{tournament_id}")
    @app.get("/api/tournaments/{tournament_id}")
    def public_tournament(
        tournament_id: int,
        request: Request,
        slug: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=100, ge=25, le=200),
        cross_table: bool = Query(default=False),
        result: Literal["draw", "decisive", "white-win", "black-win"] | None = Query(default=None),
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        tournament = _require_tournament(connection, tournament_id)
        if slug is not None and request.url.path.startswith("/api/events/"):
            event = _require_viewable_event(connection, slug, request)
            if not web_app._event_arena_is_public(event) and not web_app._admin_request_authenticated(request):
                raise HTTPException(status_code=403, detail="The arena opens when the event goes live.")
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
            estimate_cache=request.app.state.tournament_estimate_cache,
        )[0]
        result_types = (result,) if result else None
        total_games = count_games(connection, tournament.id, status="finished", result_types=result_types)
        games = list_games_page(
            connection,
            tournament.id,
            page=page,
            page_size=page_size,
            status="finished",
            result_types=result_types,
        )
        active_games = list_active_games(
            connection,
            tournament_id=tournament.id,
            limit=None if cross_table else 500,
        )
        raw_game_id = request.query_params.get("game_id")
        if raw_game_id is not None:
            try:
                viewer_game = get_tournament_game(connection, tournament.id, int(raw_game_id))
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
        engine_data = web_app._engine_data(viewer_game, viewer_moves, opening, variant=tournament.config.variant)
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
                "viewer_moves": web_app._move_payloads(viewer_moves, opening, variant=tournament.config.variant),
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
        rows = list_rating_rows(connection, rating_list.id) if rating_list else []
        ratings = jsonable_encoder(rows)
        if rows:
            tags = engine_version_tags.tags_by_version(connection)
            hidden = {row["id"] for row in connection.execute(
                "SELECT id FROM engine_versions WHERE public_hidden AND id = ANY(?)",
                ([row.engine.engine_id for row in rows],),
            )}
            for rating in ratings:
                engine = rating["engine"]
                version_id = engine["engine_id"]
                engine["tags"] = tags.get(version_id, [])
                engine["public_hidden"] = version_id in hidden
        return _json(
            {
                "rating_list": rating_list,
                "rating_lists": rating_lists,
                "ratings": ratings,
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
        versions = [version for version in list_engine_records(connection) if not version.public_hidden]
        tags = engine_version_tags.tags_by_version(connection)
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
                            "tags": tags.get(version.id, []),
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
        if engine is None or engine.public_hidden:
            raise HTTPException(status_code=404, detail="Engine not found.")
        family = get_engine_family(connection, engine.engine_id)
        engine_records = list_engine_records(connection)
        versions = tuple(
            version for version in engine_records
            if version.engine_id == engine.engine_id and not version.public_hidden
        )
        tags = engine_version_tags.tags_by_version(connection)
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
                "engine": {**jsonable_encoder(engine), "tags": tags.get(engine.id, [])},
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
                    if not version.public_hidden
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
                sender_ip_hash=web_app._chat_address_hash(request),
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


    @app.post("/api/admin/rating-lists")
    def admin_create_rating_list(
        payload: RatingListPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            rating_list_id = create_rating_list(
                connection, payload.name, payload.variant,
                hardware_scaled=payload.hardware_scaled, monthly_refresh=payload.monthly_refresh,
                description=payload.description,
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail="A rating list with that name already exists.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json({"id": rating_list_id, "message": "Rating list created."}, status_code=201)

    @app.put("/api/admin/rating-lists/order")
    def admin_reorder_rating_lists(
        payload: RatingListOrderPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            reorder_rating_lists(connection, payload.rating_list_ids)
            connection.commit()
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json({"rating_list_ids": payload.rating_list_ids, "message": "Rating list order saved."})

    @app.patch("/api/admin/rating-lists/{rating_list_id}")
    def admin_update_rating_list(
        rating_list_id: int,
        payload: RatingListUpdatePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if get_rating_list(connection, rating_list_id) is None:
            raise HTTPException(status_code=404, detail="Rating list not found.")
        try:
            update_rating_list(connection, rating_list_id, **payload.model_dump(exclude_none=True))
            rating_list = get_rating_list(connection, rating_list_id)
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail="A rating list with that name already exists.") from exc
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json({"rating_list": rating_list, "message": "Rating list settings saved."})

    @app.post("/api/admin/rating-lists/historical")
    def admin_create_historical_rating_list(
        payload: HistoricalRatingListPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            rating_list_id, tournament_id, result = create_historical_rating_list(
                connection, **payload.model_dump(),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail="A rating list with that name already exists.") from exc
        except (RatingCommitError, ValueError) as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json({
            "rating_list_id": rating_list_id, "tournament_id": tournament_id,
            "result": result, "message": "Historical rating list created.",
        }, status_code=201)

    @app.put("/api/admin/rating-lists/{rating_list_id}/variant")
    def admin_set_rating_list_variant(
        rating_list_id: int,
        payload: RatingListVariantPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            set_rating_list_variant(connection, rating_list_id, payload.variant)
            connection.commit()
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json({"message": "Rating list variant updated."})

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
                exclude_worker_ids=tuple(payload.exclude_worker_ids),
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
        request: Request,
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
            estimate_cache=request.app.state.tournament_estimate_cache,
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

    @app.get("/api/admin/tournaments/{tournament_id}/viewers")
    def admin_tournament_viewers(
        tournament_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _require_tournament(connection, tournament_id)
        return _json({"viewers": request.app.state.stream_hub.tournament_viewers(tournament_id)})

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
            estimate_cache=request.app.state.tournament_estimate_cache,
        )[0]
        total_games = count_games(
            connection,
            tournament.id,
            result_types=result_type,
        )
        payload: dict[str, Any] = {
            "tournament": tournament,
            "viewers": request.app.state.stream_hub.tournament_viewers(tournament_id),
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
            "rating_lists": [
                item for item in list_rating_lists(connection)
                if (item.variant, item.rules_revision) == (tournament.config.variant, tournament.config.rules_revision)
                and item.hardware_scaled == (tournament.config.clock_reference_hardware_key is not None)
            ],
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
        role = web_app._admin_session_role(request)
        internal = request.scope.get("cope.control.internal")
        identity = internal["principal"] if internal else ApplicationPrincipal(f"browser:{role}", ("tournaments:write",))
        try:
            execute_compatibility(
                connection.native_connection(), identity, "competitions.rename.v1", str(tournament_id),
                {"name": payload.name}, request.headers.get("idempotency-key") or str(uuid.uuid4()),
            )
            connection.commit()
        except ApplicationError as error:
            connection.rollback()
            raise HTTPException(error.status, error.payload()) from error
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
        _validate_live_participant_engine(connection, payload.engine_id, tournament.config.variant)
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
    def access_token(role: Literal["admin", "manager"], request: Request,connection: sqlite3.Connection=Depends(web_app._database)):
        raw = _current_bootstrap_admin(request,connection)
        token = bootstrap_secret(raw,role,configured_admin=request.app.state.configured_admin_token)
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
        _current_bootstrap_admin(request,connection,exclusive=True)
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
        tags = engine_version_tags.tags_by_version(connection)
        versions_by_family = {engine.id: [] for engine in engines}
        for version in versions:
            versions_by_family.setdefault(version.engine_id, []).append(version)
        return _json(
            {
                "engines": [
                    {
                        **jsonable_encoder(engine),
                        "versions": [
                            _engine_version_admin_payload(version, tags)
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

    @app.get("/api/admin/engines/queue")
    def admin_engine_queue(
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        versions = list_engine_records(connection)
        return _json(
            {
                **_engine_queues_payload(connection),
                "engine_versions": [
                    {
                        "id": version.id,
                        "engine_id": version.engine_id,
                        "name": version.name,
                        "version": version.version,
                        "author": version.author,
                        "active": version.active and version.engine_active,
                    }
                    for version in versions
                ],
                "rating_lists": [
                    {"id": rating_list.id, "name": rating_list.name}
                    for rating_list in list_rating_lists(connection)
                ],
            }
        )

    @app.put("/api/admin/engines/queue")
    def admin_update_engine_queue(
        payload: EngineQueuesPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        version_ids = {version.id for version in list_engine_records(connection)}
        rating_list_ids = {rating_list.id for rating_list in list_rating_lists(connection)}
        if any(item.engine_version_id not in version_ids for item in payload.waiting_for_test):
            raise HTTPException(status_code=422, detail="Choose a valid engine version.")
        if any(item.rating_list_id not in rating_list_ids for item in payload.waiting_for_test):
            raise HTTPException(status_code=422, detail="Choose a valid rating list.")
        from cope.control.queues import replace_queues

        try:
            replace_queues(connection, payload)
        except ValueError as error:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({**_engine_queues_payload(connection), "message": "Engine queues updated."})

    @app.get("/api/admin/engines/{engine_id}")
    def admin_engine(
        engine_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        engine = get_engine_family(connection, engine_id)
        if engine is None:
            raise HTTPException(status_code=404, detail="Engine not found.")
        versions = list_engine_versions(connection, engine_id)
        tags = engine_version_tags.tags_by_version(connection)
        return _json(
            {
                "engine": engine,
                "versions": [_engine_version_admin_payload(version, tags) for version in versions],
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
            "version": _engine_version_admin_payload(version, engine_version_tags.tags_by_version(connection)),
            "benchmarks": [
                _benchmark_job_admin_payload(item)
                for item in list_engine_benchmark_jobs(connection, engine_version_id=version_id)
            ],
        })

    @app.get("/api/admin/engine-version-tags")
    def admin_engine_version_tags(connection: sqlite3.Connection = Depends(web_app._database)):
        return _json({"tags": engine_version_tags.list_tags(connection)})

    def save_engine_version_tag(connection, payload, tag_id=None):
        try:
            tag = engine_version_tags.save_tag(connection, payload.name, payload.color.lower(), tag_id)
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail="An engine version tag with that name already exists.") from exc
        if tag is None:
            raise HTTPException(status_code=404, detail="Engine version tag not found.")
        connection.commit()
        return tag

    @app.post("/api/admin/engine-version-tags")
    def admin_create_engine_version_tag(
        payload: EngineVersionTagPayload,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        return _json({"tag": save_engine_version_tag(connection, payload)}, status_code=201)

    @app.put("/api/admin/engine-version-tags/{tag_id}")
    def admin_update_engine_version_tag(
        tag_id: int,
        payload: EngineVersionTagPayload,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        return _json({"tag": save_engine_version_tag(connection, payload, tag_id)})

    @app.delete("/api/admin/engine-version-tags/{tag_id}")
    def admin_delete_engine_version_tag(
        tag_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if not engine_version_tags.delete_tag(connection, tag_id):
            raise HTTPException(status_code=404, detail="Engine version tag not found.")
        connection.commit()
        return _json({"message": "Engine version tag deleted."})

    @app.put("/api/admin/engine-versions/{version_id}/metadata")
    def admin_update_engine_version_metadata(
        version_id: int,
        payload: EngineVersionMetadataPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            updated = engine_version_tags.update_metadata(connection, version_id, payload.public_hidden, payload.tag_ids)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not updated:
            raise HTTPException(status_code=404, detail="Engine version not found.")
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({"id": version_id, "message": "Engine version metadata saved."})

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

    def benchmarkable_engine(connection, version_id):
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
        engine = get_engine(connection, version_id)
        if engine is None:
            raise HTTPException(status_code=404, detail="Engine version not found.")
        if engine.build_hash != version.build_hash:
            raise HTTPException(status_code=409, detail="The engine build changed. Reload the version and try again.")
        return engine

    @app.post("/api/admin/engine-versions/{version_id}/benchmarks/schedule")
    def admin_schedule_engine_benchmark(
        version_id: int,
        payload: ScheduleEngineBenchmarkPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        engine = benchmarkable_engine(connection, version_id)
        if connection.execute(
            "SELECT 1 FROM benchmarkers WHERE hardware_key = ? AND status <> 'revoked'",
            (payload.hardware_key,),
        ).fetchone() is None:
            raise HTTPException(status_code=422, detail="Choose hardware with a registered benchmarker.")
        schedule_benchmark_jobs(connection, hardware_key=payload.hardware_key, engines=(engine,))
        job = connection.execute(
            "SELECT id, status FROM benchmark_jobs WHERE build_hash = ? AND hardware_key = ?",
            (engine.build_hash, payload.hardware_key),
        ).fetchone()
        if job is None:
            raise HTTPException(status_code=409, detail="The benchmark queue changed. Reload and try again.")
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json({"job_id": job["id"], "status": job["status"]})

    @app.post("/api/admin/engine-versions/{version_id}/benchmarks/reschedule")
    def admin_reschedule_engine_benchmarks(
        version_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        engine = benchmarkable_engine(connection, version_id)
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
                values.get("variant", "standard"),
            )
            suite_id = await asyncio.to_thread(
                _create_opening_import,
                connection,
                name=name,
                description=values.get("description", "").strip(),
                variant=values.get("variant", "standard"),
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
                    values.get("variant", "standard"),
                )
            position_count = await asyncio.to_thread(
                _update_opening_import,
                connection,
                suite_id,
                name=name,
                description=values.get("description", "").strip(),
                variant=values.get("variant", "standard"),
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

    register_tools_routes(app)
    register_environment_clone_routes(app)
    register_tournament_tools_routes(app)
    register_puzzle_tools_routes(app)


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

    @app.get("/api/admin/workers/donors")
    def admin_donor_workers(
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if web_app._admin_session_role(request) != "admin":
            raise HTTPException(status_code=403, detail="Admin access is required to manage donor groups.")
        return _json({"workers": list_donor_workers(connection)})

    @app.put("/api/admin/workers/donors")
    def admin_update_donor_group(
        payload: DonorGroupPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if web_app._admin_session_role(request) != "admin":
            raise HTTPException(status_code=403, detail="Admin access is required to manage donor groups.")
        worker_ids = sorted(set(payload.worker_ids))
        if any(not 0 < worker_id < 2**63 for worker_id in worker_ids):
            raise HTTPException(status_code=422, detail="Select valid workers.")
        name = (payload.name or "").strip() or None
        if not set_donor_group(connection, worker_ids, name):
            connection.rollback()
            raise HTTPException(status_code=404, detail="One or more selected workers could not be found.")
        connection.commit()
        return _json({"workers": list_donor_workers(connection), "message": "Donor grouping updated."})

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

    @app.post("/api/admin/chat/messages/{message_id}/block")
    def admin_chat_block(
        message_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if web_app._admin_session_role(request) != "admin":
            raise HTTPException(status_code=403, detail="Admin access is required to block IP addresses.")
        row = connection.execute(
            "SELECT sender_ip_hash FROM chat_messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Message not found.")
        sender_ip_hash = row["sender_ip_hash"]
        if not sender_ip_hash:
            raise HTTPException(status_code=409, detail="This message has no recorded IP address.")
        web_app._lock_chat_address(connection, sender_ip_hash)
        connection.execute(
            "INSERT INTO chat_ip_blocks (sender_ip_hash) VALUES (?) ON CONFLICT DO NOTHING",
            (sender_ip_hash,),
        )
        connection.commit()
        return _json({"message": "IP address blocked from chat."})

    register_event_api_routes(app)


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
        ORDER BY rating_list.display_order NULLS LAST, rating_list.name, rating_list.id
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
    if _require_tournament(connection, tournament_id).historical:
        raise HTTPException(status_code=409, detail="Historical tournaments reference games from their original tournaments.")
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
    variant: str = "standard",
) -> None:
    record = get_engine_record(connection, engine_id)
    if record is None:
        raise HTTPException(status_code=422, detail="Choose an existing engine version.")
    if not _engine_is_tournament_ready(connection, record):
        raise HTTPException(
            status_code=422,
            detail="Choose an active engine version that is ready to run.",
        )
    _validate_engine_variant(connection, record, variant)


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
    elif tournament.config.clock_reference_hardware_key is not None or getattr(tournament.config.format_options, "games_per_engine", None) is not None:
        editable = False
        reason = "This tournament uses a fixed roster."
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
          FROM tournament_games WHERE tournament_id = ?
          UNION ALL
          SELECT black_engine_id AS engine_id, status
          FROM tournament_games WHERE tournament_id = ?
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


def _create_opening_import(
    connection: sqlite3.Connection,
    *,
    name: str,
    description: str,
    openings: list[OpeningLine],
    variant: str = "standard",
) -> int:
    try:
        suite_id = create_opening_suite(
            connection,
            name=name,
            description=description,
            variant=variant,
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
    variant: str = "standard",
) -> int:
    try:
        if mode == "replace":
            if connection.execute("SELECT 1 FROM games JOIN openings ON openings.id = games.opening_id WHERE openings.suite_id = ? LIMIT 1", (suite_id,)).fetchone() is not None:
                raise ValueError("This suite has scheduled or recorded games. Append positions or create a new suite to preserve their starting positions.")
            replace_suite_openings(connection, suite_id, openings)
        update_opening_suite(
            connection,
            suite_id,
            name=name,
            description=description,
            variant=variant,
        )
        if mode == "append":
            append_suite_openings(connection, suite_id, openings)
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
    variant: str = "standard",
) -> list[OpeningLine]:
    try:
        openings = parse_opening_input(positions, files, variant)
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
