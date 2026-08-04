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
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from starlette.datastructures import UploadFile

from cope.chat import announce_tournament_finished
from cope.core.models import EngineArtifactSpec, HardwareInfo, OpeningLine, TournamentConfig
from cope.db import (
    ChatSettingsRecord,
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
    database_stats,
    database_schema_version,
    delete_chat_message,
    delete_engine,
    delete_engine_version,
    delete_git_host,
    delete_opening_suite,
    delete_rating_list,
    delete_tournament,
    delete_worker,
    engine_game_count,
    engine_build_is_benchmarked,
    engine_result_summary,
    forget_benchmarker,
    forget_engine_benchmarks,
    forget_failed_benchmark_job,
    get_benchmarker,
    get_benchmarker_by_session_id,
    get_deployment_job,
    get_chat_settings,
    get_engine_record,
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
    latest_dockerfile_pull_job,
    list_benchmarkers,
    list_benchmark_hardware,
    list_chat_messages,
    list_engine_games,
    list_engine_records,
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
    list_rating_rows,
    list_service_heartbeats,
    list_suite_openings,
    list_tournaments,
    list_tournament_rating_commits,
    list_uncommitted_finished_tournaments,
    mint_worker_token_for_worker,
    replace_suite_openings,
    replay_game,
    record_manual_benchmark,
    register_engine_artifact,
    reschedule_engine_benchmarks,
    request_tournament_rating_commit,
    revoke_worker,
    schedule_tournament,
    set_tournament_concurrency,
    set_tournament_status,
    suite_opening_count,
    update_chat_settings,
    update_engine,
    update_engine_version,
    update_git_host,
    update_opening_suite,
    update_rating_list_anchor,
    update_tournament,
    update_worker_assignment_settings,
    update_worker_label,
    unschedule_tournament,
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


def _bearer_credential(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, credential = authorization.partition(" ")
    return credential if scheme.lower() == "bearer" else ""


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


def _admin_ratings_payload(connection: sqlite3.Connection) -> dict[str, Any]:
    rating_lists = list_rating_lists(connection)
    tournaments: list[dict[str, Any]] = []
    commits = connection.execute(
        """
        SELECT rating_commit.*, tournament.name AS tournament_name,
               tournament.status AS tournament_status,
               rating_list.name AS rating_list_name
        FROM tournament_rating_list_commits rating_commit
        JOIN tournaments tournament ON tournament.id = rating_commit.tournament_id
        JOIN rating_lists rating_list ON rating_list.id = rating_commit.rating_list_id
        WHERE rating_commit.status = 'applied'
        ORDER BY COALESCE(rating_commit.applied_at, rating_commit.requested_at) DESC,
                 rating_commit.tournament_id DESC
        """
    )
    for commit in commits:
        games = connection.execute(
            """
            SELECT id FROM games
            WHERE tournament_id = ? AND status = 'finished'
              AND result IN ('1-0', '0-1', '1/2-1/2')
            ORDER BY id
            """,
            (commit["tournament_id"],),
        ).fetchall()
        game_ids = tuple(int(game["id"]) for game in games)
        complete_hardware_games = 0
        if game_ids:
            placeholders = ", ".join("?" for _ in game_ids)
            complete_hardware_games = int(
                connection.execute(
                    f"""
                    SELECT COUNT(*) AS count FROM (
                      SELECT game_id FROM game_hardware_scores
                      WHERE game_id IN ({placeholders})
                      GROUP BY game_id HAVING COUNT(*) = 2
                    ) scores
                    """,
                    game_ids,
                ).fetchone()["count"]
            )
        tournaments.append(
            {
                "tournament_id": commit["tournament_id"],
                "tournament_name": commit["tournament_name"],
                "tournament_status": commit["tournament_status"],
                "rating_list_id": commit["rating_list_id"],
                "rating_list_name": commit["rating_list_name"],
                "requested_at": commit["requested_at"],
                "applied_at": commit["applied_at"],
                "games": len(game_ids),
                "hardware_games": complete_hardware_games,
                "missing_hardware_games": len(game_ids) - complete_hardware_games,
            }
        )
    return {
        "rating_lists": jsonable_encoder(rating_lists),
        "tournaments": tournaments,
        "ratings": {
            str(rating_list.id): jsonable_encoder(list_rating_rows(connection, rating_list.id))
            for rating_list in rating_lists
        },
    }


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


class EngineVersionUpdatePayload(BaseModel):
    version: str = Field(min_length=1, max_length=80)
    dockerfile_path: str = Field(min_length=1, max_length=500)
    uci_options: dict[str, str | int | bool] = Field(default_factory=dict)

    @field_validator("version", "dockerfile_path")
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
    git_host_id: int = Field(gt=0)
    repository_full_name: str = Field(min_length=3, max_length=300)
    source_ref: str = Field(min_length=1, max_length=200)
    source_kind: str = Field(pattern=r"^(release|commit)$")
    dockerfile_path: str = Field(min_length=1, max_length=500)
    uci_options: dict[str, str | int | bool] = Field(default_factory=dict)

    @field_validator("version", "repository_full_name", "source_ref", "dockerfile_path")
    @classmethod
    def strip_value(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value


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


class WorkerSettingsPayload(BaseModel):
    core_limit: int | None = Field(default=None, ge=1)
    tournament_scope: Literal["all", "selected"] = "all"
    tournament_ids: list[int] = Field(default_factory=list)

    @field_validator("tournament_ids")
    @classmethod
    def validate_tournament_ids(cls, value: list[int]) -> list[int]:
        if any(tournament_id <= 0 for tournament_id in value):
            raise ValueError("tournament ids must be positive")
        return list(dict.fromkeys(value))


class DeploymentPayload(BaseModel):
    ref: str = Field(default="", max_length=200)

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@+-]{0,199}", cleaned) is None:
            raise ValueError("Git ref contains unsupported characters")
        return cleaned


class ChatSettingsPayload(BaseModel):
    enabled: bool
    max_message_length: int = Field(ge=1, le=2_000)
    allow_anonymous_names: bool


def register_api_routes(app: FastAPI) -> None:
    from cope.web import app as web_app

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    @app.get("/api/session")
    def session(request: Request):
        token = web_app._admin_token(request)
        authenticated = bool(token and web_app._admin_session_valid(request, token))
        response = _json(
            {
                "admin_configured": bool(token),
                "authenticated": authenticated,
                "secure_context": web_app._request_is_secure_or_local(request),
                "csrf_token": (
                    web_app._csrf_token(request, token)
                    if token and authenticated
                    else ""
                ),
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/session")
    async def create_session(request: Request):
        token = web_app._admin_token(request)
        if not token:
            raise HTTPException(
                status_code=503,
                detail="Admin access is not configured.",
            )
        if not web_app._request_is_secure_or_local(request):
            raise HTTPException(status_code=403, detail="Admin access requires HTTPS.")

        form = await read_form(request)
        supplied = form_value(form, "token")
        if not hmac.compare_digest(supplied, token):
            raise HTTPException(status_code=401, detail="Invalid admin token.")

        nonce = secrets.token_urlsafe(32)
        response = _json(
            {
                "authenticated": True,
                "csrf_token": web_app._csrf_for_nonce(token, nonce),
                "message": "Signed in.",
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
            if artifact_sha256 != expected_sha256:
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
            return _json({"artifact": _engine_artifact_spec(artifact)})
        finally:
            temporary.unlink(missing_ok=True)

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
    def public_home(connection: sqlite3.Connection = Depends(web_app._database)):
        engines = web_app._engine_names(connection)
        return _json(
            {
                "running_tournaments": web_app._home_tournament_cards(connection, engines),
                "upcoming_rows": web_app._upcoming_rows(connection, engines, limit=16),
                "recent_games": list_games_by_status(connection, "finished", limit=16),
                "engines": engines,
                "tournament_names": web_app._tournament_names(connection),
            }
        )

    @app.get("/api/tournaments")
    def public_tournaments(connection: sqlite3.Connection = Depends(web_app._database)):
        engines = web_app._engine_names(connection)
        estimator = TournamentEstimator(connection)
        items = [
            web_app._tournament_summary(
                connection,
                tournament,
                engines,
                estimator=estimator,
            )
            for tournament in list_tournaments(connection)
            if tournament.status != "draft"
        ]
        return _json(
            {
                "tournaments": items,
                "tournament_stats": web_app._tournament_index_stats(items),
            }
        )

    @app.get("/api/tournaments/{tournament_id}")
    def public_tournament(
        tournament_id: int,
        request: Request,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=100, ge=25, le=200),
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        tournament = _require_tournament(connection, tournament_id)
        if tournament.status == "draft":
            raise HTTPException(status_code=404, detail="Tournament not found.")
        engines = web_app._engine_names(connection)
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
            limit=500,
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
            web_app.list_moves(connection, viewer_game.id) if viewer_game else ()
        )
        viewer_locked = bool(
            request.query_params.get("game_id") is not None
            and viewer_game is not None
            and viewer_game.status not in {"assigned", "live"}
        )
        chat_settings = get_chat_settings(connection)
        game_live = (
            request.app.state.stream_hub.tournament_live(tournament.id, viewer_game.id)
            if viewer_game
            else None
        )
        engine_data = web_app._engine_data(viewer_game, viewer_moves)
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
        estimate = TournamentEstimator(connection).estimate(
            tournament,
            list_games(connection, tournament.id),
        )
        return _json(
            {
                "tournament": tournament,
                "estimate": estimate.to_dict(),
                "games": [
                    web_app._game_payload(game, engines, live=True)
                    for game in games
                ],
                "active_games": [
                    web_app._game_payload(game, engines, live=True)
                    for game in active_games
                ],
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
                "viewer_moves": [web_app._move_payload(move) for move in viewer_moves],
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
                "chat_messages": list_chat_messages(
                    connection,
                    limit=30,
                    tournament_id=tournament_id,
                ),
                "chat_settings": chat_settings,
                "opening": (
                    web_app._opening_view(connection, viewer_game.opening_id)
                    if viewer_game
                    else None
                ),
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
        return _json(
            {
                "rating_list": rating_list,
                "rating_lists": rating_lists,
                "ratings": list_rating_rows(connection, rating_list.id) if rating_list else [],
            }
        )

    @app.get("/api/engines/{engine_id}")
    def public_engine(
        engine_id: int,
        result: Literal["win", "draw", "loss"] | None = Query(default=None),
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        engine = get_engine_record(connection, engine_id)
        if engine is None:
            raise HTTPException(status_code=404, detail="Engine not found.")
        games = list_engine_games(connection, engine_id, result_filter=result)
        return _json(
            {
                "engine": engine,
                "games": games,
                "engines": web_app._engine_names(connection),
                "record": engine_result_summary(connection, engine_id),
            }
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

    @app.get("/api/games/{game_id}/pgn")
    def public_game_pgn(
        game_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        game = get_game(connection, game_id)
        if game is None:
            raise HTTPException(status_code=404, detail="Game not found.")
        tournament = get_tournament(connection, game.tournament_id)
        if tournament is None or tournament.status == "draft":
            raise HTTPException(status_code=404, detail="Game not found.")
        if game.status != "finished" or not game.pgn:
            raise HTTPException(status_code=409, detail="PGN is not available until the game finishes.")
        return Response(
            content=game.pgn,
            media_type="application/x-chess-pgn; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="cope-game-{game.id}.pgn"',
            },
        )

    # ------------------------------------------------------------------
    # Admin reads and writes
    # ------------------------------------------------------------------

    @app.get("/api/admin/dashboard")
    def admin_dashboard(connection: sqlite3.Connection = Depends(web_app._database)):
        tournaments = list_tournaments(connection)
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
        payload = _admin_ratings_payload(connection)
        return _json({
            "rating_list": rating_list,
            "ratings": list_rating_rows(connection, rating_list_id),
            "engine_versions": [
                {"id": version.id, "name": version.name, "version": version.version}
                for version in list_engine_records(connection)
            ],
            "tournaments": [
                item for item in payload["tournaments"]
                if item["rating_list_id"] == rating_list_id
            ],
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
        heartbeats = {
            item["service"]: item
            for item in list_service_heartbeats(connection)
        }
        jobs = list_deployment_jobs(connection, limit=25)
        return _json(
            {
                "current_version": app_version(),
                "default_ref": os.environ.get("COPE_UPDATE_REF", "main"),
                "updater": heartbeats.get("updater"),
                "dockerfile_pull": jsonable_encoder(latest_dockerfile_pull_job(connection)),
                "jobs": [
                    {
                        **jsonable_encoder(job),
                        "targets": jsonable_encoder(
                            list_deployment_targets(connection, job.id)
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
        requested_ref = payload.ref or os.environ.get("COPE_UPDATE_REF", "main")
        try:
            job_id = create_deployment_job(
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
                "message": f"Deployment {job_id} queued for {requested_ref}.",
            },
            status_code=202,
        )

    @app.post("/api/admin/dockerfile-pulls")
    def admin_create_dockerfile_pull(
        payload: DeploymentPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        requested_ref = payload.ref or os.environ.get("COPE_UPDATE_REF", "main")
        updater = next(
            (item for item in list_service_heartbeats(connection) if item["service"] == "updater"),
            None,
        )
        if updater is not None and updater["app_version"] != app_version():
            raise HTTPException(
                status_code=409,
                detail="The updater is running an older release and must be restarted first.",
            )
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
        items = [
            web_app._tournament_summary(
                connection,
                tournament,
                engines,
                estimator=estimator,
            )
            for tournament in list_tournaments(connection)
            if not status or tournament.status == status
        ]
        return _json(
            {
                "tournaments": items,
                "status_filter": status,
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
        all_games = list_games(connection, tournament.id)
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
            "game_summary": web_app._tournament_game_summary(
                connection,
                tournament.id,
            ),
            "estimate": TournamentEstimator(connection).estimate(
                tournament,
                all_games,
            ).to_dict(),
            "engines": web_app._engine_names(connection),
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
                detail="Only draft tournaments and the concurrency of running or paused tournaments can be edited.",
            )
        try:
            if tournament.status in {"running", "paused"}:
                unchanged_config = payload.config.model_dump(
                    mode="json",
                    exclude={"concurrency"},
                )
                current_config = tournament.config.model_dump(
                    mode="json",
                    exclude={"concurrency"},
                )
                if payload.name != tournament.name or unchanged_config != current_config:
                    raise HTTPException(
                        status_code=409,
                        detail="Only game concurrency can be changed while a tournament is running or paused.",
                    )
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
        message = (
            "Tournament concurrency updated."
            if tournament.status in {"running", "paused"}
            else "Tournament updated."
        )
        return _json({"id": tournament_id, "message": message})

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
        set_tournament_status(connection, tournament_id, target)
        connection.commit()
        _publish_admin_change(web_app, request)
        return _json(
            {
                "status": target,
                "message": f"Tournament {target}.",
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

    @app.get("/api/admin/tournaments/{tournament_id}/pgn")
    def admin_tournament_pgn(
        tournament_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        tournament = _require_tournament(connection, tournament_id)
        pgns = [
            game.pgn.strip()
            for game in list_games(connection, tournament_id, include_pgn=True)
            if game.pgn
        ]
        if not pgns:
            raise HTTPException(status_code=409, detail="No completed game PGNs are available.")
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", tournament.name).strip("-") or f"tournament-{tournament_id}"
        return Response(
            content="\n\n".join(pgns) + "\n",
            media_type="application/x-chess-pgn; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.pgn"'},
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
    def admin_settings(connection: sqlite3.Connection = Depends(web_app._database)):
        hosts = list_git_hosts(connection)
        return _json(
            {
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

    # Engines

    @app.get("/api/admin/engines")
    def admin_engines(connection: sqlite3.Connection = Depends(web_app._database)):
        engines = list_engine_families(connection)
        return _json(
            {
                "engines": [
                    {
                        **jsonable_encoder(engine),
                        "versions": [_engine_version_admin_payload(version) for version in list_engine_versions(connection, engine.id)],
                    }
                    for engine in engines
                ],
                "game_counts": {version.id: engine_game_count(connection, version.id)
                                for version in list_engine_records(connection)},
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
        return _json(
            {
                "engine": engine,
                "versions": [_engine_version_admin_payload(version) for version in list_engine_versions(connection, engine_id)],
                "game_counts": {
                    version.id: engine_game_count(connection, version.id)
                    for version in list_engine_versions(connection, engine_id)
                },
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
        host = get_git_host(connection, payload.git_host_id)
        if host is None or not host.enabled:
            raise HTTPException(status_code=404, detail="Git host is unavailable.")
        if payload.source_kind == "commit":
            if not re.fullmatch(r"[0-9a-fA-F]{7,64}", payload.source_ref):
                raise HTTPException(status_code=422, detail="Enter a valid commit hash.")
        else:
            try:
                release_tags = {
                    release["tag"]
                    for release in list_releases(host, payload.repository_full_name)
                }
            except SourceServiceError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            if payload.source_ref not in release_tags:
                raise HTTPException(status_code=422, detail="Choose a public release.")
        try:
            repository_url = canonical_repository_url(host, payload.repository_full_name)
        except SourceServiceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        dockerfile = _load_engine_dockerfile(payload.dockerfile_path)
        try:
            version_id = create_engine_version(
                connection,
                engine_id=engine_id,
                version=payload.version,
                git_host_id=host.id,
                repository_url=repository_url,
                repository_full_name=payload.repository_full_name,
                source_ref=payload.source_ref,
                source_kind=payload.source_kind,
                dockerfile_path=payload.dockerfile_path,
                dockerfile=dockerfile,
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
        dockerfile = _load_engine_dockerfile(payload.dockerfile_path)
        try:
            update_engine_version(
                connection,
                version_id,
                version=payload.version,
                dockerfile_path=payload.dockerfile_path,
                dockerfile=dockerfile,
                uci_options=options,
                active=True,
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail=web_app._friendly_error(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json({"id": version_id, "message": "Engine version updated."})

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
                return {
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
        return _json(
            {
                "suites": suites,
                "opening_counts": {
                    suite.id: suite_opening_count(connection, suite.id) for suite in suites
                },
                "usage_counts": {
                    suite.id: sum(
                        tournament.config.opening_suite_id == suite.id
                        for tournament in tournaments
                    )
                    for suite in suites
                },
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

    # Chat moderation

    @app.get("/api/admin/chat")
    def admin_chat(connection: sqlite3.Connection = Depends(web_app._database)):
        return _json(
            {
                "messages": list_chat_messages(connection, limit=100),
                "tournament_names": web_app._tournament_names(connection),
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
            deleted.tournament_id,
            deleted.id,
        )
        _publish_admin_change(web_app, request)
        return _json({"message": "Message deleted."})


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
    if (
        not record.active
        or not record.engine_active
        or not record.benchmark_current
    ):
        raise HTTPException(
            status_code=422,
            detail="Choose an active engine version with a current benchmark.",
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
            and engine.active
            and engine.engine_active
            and engine.benchmark_current
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
        if not records[engine_id].active
        or not engine_build_is_benchmarked(
            connection,
            engine_version_id=engine_id,
            build_hash=records[engine_id].build_hash,
        )
    ]
    if unavailable:
        raise HTTPException(status_code=422, detail="Every participant must be active.")

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
            engine.active
            and engine_build_is_benchmarked(
                connection,
                engine_version_id=engine.id,
                build_hash=engine.build_hash,
            )
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


def _publish_admin_change(web_app, request: Request) -> None:
    try:
        web_app._publish_admin_post_streams(request)
    except Exception:
        LOG.exception("admin change committed but live publication failed path=%s", request.url.path)
