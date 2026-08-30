from __future__ import annotations

import base64
import gzip
import hashlib
import http.client
import http.cookiejar
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from cryptography.fernet import Fernet, InvalidToken

from cope.db.connection import SCHEMA_VERSION, connect_database
from cope.db.repo import touch_service_heartbeat
from cope.engine_artifacts import ARTIFACT_FORMAT, ARTIFACT_PLATFORM, sha256_file, validate_artifact_archive
from cope.network import default_admin_token
from cope.version import app_version


LOG = logging.getLogger("cope.environment_clone")
CLONE_PROTOCOL_VERSION = 3
DEFAULT_EXPORT_TTL_HOURS = 72
DEFAULT_IMPORT_TTL_HOURS = 72
DEFAULT_REMOTE_JSON_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_TRANSFER_MAX_BYTES = 10 * 1024 * 1024 * 1024
DEFAULT_UNCOMPRESSED_MAX_BYTES = 40 * 1024 * 1024 * 1024
DEFAULT_ROW_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_SOURCE_WAIT_TIMEOUT_S = 3600
DEFAULT_ENGINE_ARTIFACT_MAX_BYTES = 1024 * 1024 * 1024
CLONE_RUNNER_LOCK_KEY = 0x434F5045434C4F4E
SENSITIVE_URL_QUERY_KEY = re.compile(
    r"(?:token|password|passwd|secret|api.?key|signature|credential|authorization|auth)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CloneDataset:
    key: str
    label: str
    description: str
    group: str
    table: str | None
    dependencies: tuple[str, ...] = ()
    default: bool = False
    serial: bool = False
    available: bool = True
    warning: str = ""
    companions: tuple[str, ...] = ()


DATASETS = (
    CloneDataset("git_hosts", "Git host definitions", "Provider URLs and host configuration without access tokens.", "Engines", "git_hosts", default=True, serial=True),
    CloneDataset("engines", "Engine families", "Names, authors, enabled state, and family identities.", "Engines", "engines", default=True, serial=True),
    CloneDataset("engine_versions", "Engine versions", "Versions, repositories, refs, UCI defaults, distribution, and build identity.", "Engines", "engine_versions", ("engines", "git_hosts"), True, True, companions=("engine_dockerfiles",)),
    CloneDataset("engine_dockerfiles", "Dockerfile recipes", "Materialize exact build recipes into a collision-safe cloned namespace.", "Engines", None, ("engine_versions",), True),
    CloneDataset("badges", "Badges", "Engine badge definitions.", "Engines", "badges", default=True, serial=True),
    CloneDataset("engine_badges", "Badge assignments", "Assignments of badges to engine families.", "Engines", "engine_badges", ("badges", "engines"), True),
    CloneDataset("engine_artifacts", "Artifact descriptors", "Content-addressed managed-engine artifact metadata.", "Artifacts", "engine_artifacts", ("engine_versions",), True, companions=("artifact_files",)),
    CloneDataset("artifact_files", "Artifact archives", "Verified compressed engine archives used by game workers.", "Artifacts", None, ("engine_artifacts",), True),
    CloneDataset("benchmark_hardware", "Benchmark hardware", "Immutable benchmark hardware profiles.", "Benchmarks", "benchmark_hardware", default=True),
    CloneDataset("benchmarkers", "Benchmarker inventory", "Sanitized benchmarker identities imported offline without credentials.", "Benchmarks", "benchmarkers", ("benchmark_hardware",), serial=True),
    CloneDataset("benchmark_jobs", "Benchmark jobs", "Successful, failed, and historical build benchmark jobs.", "Benchmarks", "benchmark_jobs", ("engine_versions", "benchmark_hardware"), True, True),
    CloneDataset("benchmark_job_output", "Benchmark output", "Full captured benchmark console output.", "Benchmarks", None, ("benchmark_jobs",), True),
    CloneDataset("engine_benchmarks", "Benchmark results", "NPS results and their exact artifact bindings.", "Benchmarks", "engine_benchmarks", ("benchmark_jobs", "engine_versions", "engine_artifacts", "benchmark_hardware"), True, True),
    CloneDataset("opening_suites", "Opening suites", "Opening suite names and descriptions.", "Openings", "opening_suites", serial=True),
    CloneDataset("openings", "Opening positions", "FENs, move sequences, and ordering for opening suites.", "Openings", "openings", ("opening_suites",), serial=True),
    CloneDataset("tournaments", "Tournament definitions", "Tournament configuration, state, and scheduling fields.", "Tournaments", "tournaments", ("engine_versions", "opening_suites"), serial=True),
    CloneDataset("participants", "Tournament participants", "Seeded engine participants.", "Tournaments", "participants", ("tournaments", "engine_versions")),
    CloneDataset("tournament_matches", "Tournament matches", "Knockout and pairing match structure.", "Tournaments", "tournament_matches", ("tournaments", "engine_versions"), serial=True),
    CloneDataset("games", "Games and PGNs", "Game records, results, PGNs, and hardware descriptions.", "Games", "games", ("tournaments", "tournament_matches", "engine_versions", "openings"), serial=True),
    CloneDataset("moves", "Moves and analysis", "Moves, evaluations, PVs, nodes, clocks, and engine telemetry.", "Games", "moves", ("games", "engine_versions")),
    CloneDataset("game_pause_checkpoints", "Pause checkpoints", "Serialized pause state for games.", "Games", "game_pause_checkpoints", ("games",)),
    CloneDataset("workers", "Worker inventory", "Sanitized worker identities imported offline without credentials.", "Workers", "workers", serial=True),
    CloneDataset("worker_tournament_permissions", "Tournament permissions", "Worker-to-tournament assignment scopes.", "Workers", "worker_tournament_permissions", ("workers", "tournaments")),
    CloneDataset("worker_event_permissions", "Event permissions", "Worker-to-event assignment scopes.", "Workers", "worker_event_permissions", ("workers", "events")),
    CloneDataset("worker_engine_discoveries", "Local engine discoveries", "Historical worker-local engine discovery records.", "Workers", "worker_engine_discoveries", ("workers",), warning="Worker-local binaries live on worker machines and cannot be siphoned from the Cope host; use managed artifacts for portable executables."),
    CloneDataset("event_fixture_workers", "Active event worker claims", "Current exclusive worker claims for event fixtures.", "Workers", "event_fixture_workers", ("workers", "tournaments", "events"), available=False, warning="Runtime claims are deliberately not portable because importing them could activate destination scheduling state."),
    CloneDataset("game_assignments", "Game assignments", "Sanitized historical game assignment records.", "Workers", "game_assignments", ("games", "workers"), serial=True),
    CloneDataset("game_assignment_progress", "Assignment progress", "Detailed server and worker workflow progress.", "Workers", "game_assignment_progress", ("game_assignments", "games", "engine_versions"), serial=True),
    CloneDataset("worker_failures", "Worker failures", "Engine and assignment failure history.", "Workers", "worker_failures", ("workers", "game_assignments", "games", "engine_versions"), serial=True),
    CloneDataset("worker_resource_samples", "Worker resource samples", "CPU, memory, disk, and engine resource history.", "Workers", "worker_resource_samples", ("workers",), serial=True),
    CloneDataset("game_hardware_scores", "Game hardware scores", "Per-game normalized hardware results.", "Games", "game_hardware_scores", ("games", "game_assignments", "workers", "engine_versions", "benchmark_hardware")),
    CloneDataset("rating_lists", "Rating list definitions", "Lists, anchors, and baseline Elo settings.", "Ratings", "rating_lists", ("engine_versions",), serial=True),
    CloneDataset("rating_list_ratings", "Current ratings", "Current Elo, game counts, and error margins.", "Ratings", "rating_list_ratings", ("rating_lists", "engine_versions")),
    CloneDataset("engine_elo_history", "Elo history", "Historical rating samples for charts and analysis.", "Ratings", "engine_elo_history", ("rating_lists", "engine_versions"), serial=True),
    CloneDataset("rating_list_history", "Per-game rating history", "Every applied rating change and its game references.", "Ratings", "rating_list_history", ("rating_lists", "engine_versions", "tournaments", "games"), serial=True),
    CloneDataset("tournament_rating_list_commits", "Rating commit history", "Tournament-to-rating-list commit state.", "Ratings", "tournament_rating_list_commits", ("tournaments", "rating_lists")),
    CloneDataset("events", "Event definitions", "Event identity, theme, configuration, state, and visibility.", "Events", "events", serial=True),
    CloneDataset("event_stages", "Event stages", "Ordered stages and schedule state.", "Events", "event_stages", ("events",), serial=True),
    CloneDataset("event_sessions", "Event sessions", "Ordered sessions and schedule state.", "Events", "event_sessions", ("events", "event_stages"), serial=True),
    CloneDataset("event_cast_members", "Event cast", "Engine, team, person, and other event cast entries.", "Events", "event_cast_members", ("events", "engine_versions"), serial=True),
    CloneDataset("event_contests", "Event contests", "Contest structure, results, and live state.", "Events", "event_contests", ("events", "event_stages", "event_sessions"), serial=True),
    CloneDataset("event_contest_cast", "Contest cast", "Cast membership and sides for event contests.", "Events", "event_contest_cast", ("event_contests", "event_cast_members")),
    CloneDataset("event_updates", "Event updates", "Announcements, incidents, results, and milestones.", "Events", "event_updates", ("events",), serial=True),
    CloneDataset("event_awards", "Event awards", "Awards and their recipients.", "Events", "event_awards", ("events", "event_cast_members"), serial=True),
    CloneDataset("event_chat_settings", "Event chat settings", "Per-event chat configuration.", "Events", "event_chat_settings", ("events",)),
    CloneDataset("engine_relay_fixtures", "Relay fixtures", "Engine relay fixtures, anchors, and kibitzer configuration.", "Events", "engine_relay_fixtures", ("events", "tournaments", "event_cast_members", "engine_versions"), serial=True),
    CloneDataset("engine_relay_fixture_teams", "Relay fixture teams", "Team and engine placement in relay fixtures.", "Events", "engine_relay_fixture_teams", ("engine_relay_fixtures", "event_cast_members", "engine_versions")),
    CloneDataset("puzzle_gauntlet_events", "Puzzle gauntlet settings", "Event, tournament, suite, and time configuration.", "Events", "puzzle_gauntlet_events", ("events", "tournaments", "opening_suites")),
    CloneDataset("puzzle_gauntlet_puzzles", "Puzzle gauntlet puzzles", "Puzzle positions and solution data.", "Events", "puzzle_gauntlet_puzzles", ("puzzle_gauntlet_events", "openings"), serial=True),
    CloneDataset("puzzle_gauntlet_attempts", "Puzzle attempts", "Cast outcomes and linked games.", "Events", "puzzle_gauntlet_attempts", ("puzzle_gauntlet_puzzles", "event_cast_members", "games"), serial=True),
    CloneDataset("chat_settings", "Global chat settings", "Global moderation and retention configuration.", "Chat", "chat_settings"),
    CloneDataset("chat_messages", "Chat messages", "Tournament and event chat history.", "Chat", "chat_messages", ("tournaments", "events"), serial=True),
    CloneDataset("system_chat_events", "System chat markers", "System message de-duplication and metadata.", "Chat", "system_chat_events", ("tournaments", "chat_messages")),
    CloneDataset("tool_jobs", "Tool job history", "Sanitized historical admin tool runs.", "Operations", "tool_jobs", ("workers",), serial=True),
    CloneDataset("tool_job_items", "Tool job item history", "Detailed engine results from historical tool runs.", "Operations", "tool_job_items", ("tool_jobs", "engine_versions"), serial=True),
    CloneDataset("runner_commands", "Runner command history", "Terminalized scheduler command audit history.", "Operations", "runner_commands", serial=True),
    CloneDataset("deployment_jobs", "Deployment history", "Terminalized platform and web deployment audit history.", "Operations", "deployment_jobs", serial=True),
    CloneDataset("deployment_targets", "Deployment target history", "Sanitized server, worker, and benchmarker deployment results.", "Operations", "deployment_targets", ("deployment_jobs",), serial=True),
    CloneDataset("dockerfile_pull_jobs", "Dockerfile update history", "Terminalized Dockerfile synchronization audit history.", "Operations", "dockerfile_pull_jobs", serial=True),
    CloneDataset("service_endpoints", "Live service endpoints", "Current internal service discovery addresses.", "Operations", "service_endpoints", available=False, warning="Service discovery is rebuilt by each installation and cannot safely be imported."),
    CloneDataset("service_heartbeats", "Live service heartbeats", "Current service health and revision signals.", "Operations", "service_heartbeats", available=False, warning="Ephemeral service health is deliberately regenerated on the destination."),
)


DATASET_BY_KEY = {item.key: item for item in DATASETS}


PRESETS = {
    "engine_development": {
        "label": "Engine development seed",
        "description": "Engines, exact recipes, managed artifacts, badges, and benchmark results.",
        "datasets": [item.key for item in DATASETS if item.default],
    },
    "engine_catalog": {
        "label": "Engine catalog only",
        "description": "Engine definitions and build recipes without binary artifacts or benchmarks.",
        "datasets": ["git_hosts", "engines", "engine_versions", "engine_dockerfiles", "badges", "engine_badges"],
    },
    "competition_archive": {
        "label": "Competition archive",
        "description": "Openings, tournaments, games, moves, hardware scores, and ratings.",
        "datasets": ["openings", "participants", "tournament_matches", "games", "moves", "game_hardware_scores", "rating_list_ratings", "engine_elo_history", "rating_list_history", "tournament_rating_list_commits"],
    },
    "events_and_chat": {
        "label": "Events and broadcasts",
        "description": "Events, relay and puzzle modules, schedules, awards, and chat.",
        "datasets": [item.key for item in DATASETS if item.group in {"Events", "Chat"}],
    },
    "safe_full": {
        "label": "Safe full clone",
        "description": "Every portable dataset with credentials removed and runnable state neutralized.",
        "datasets": [item.key for item in DATASETS if item.available],
    },
}


class CloneCancelled(RuntimeError):
    pass


class RemoteCloneError(RuntimeError):
    pass


class CloneConflict(RuntimeError):
    pass


class CloneCleanupDeferred(RuntimeError):
    pass


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = self._create_validated_connection

    def _create_validated_connection(self, address, timeout, source_address=None):
        hostname, port = address
        last_error: OSError | None = None
        for candidate in validated_source_addresses(hostname, port):
            try:
                return socket.create_connection((candidate, port), timeout, source_address)
            except OSError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise OSError("The source domain did not resolve to a usable address.")


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(
            PinnedHTTPSConnection,
            request,
            context=self._context,
        )


class BoundedCloneWriter:
    def __init__(self, stream, maximum: int) -> None:
        self.stream = stream
        self.maximum = maximum
        self.written = 0

    def write(self, value: bytes) -> int:
        if self.written + len(value) > self.maximum:
            raise ValueError("Environment export exceeds the configured clone transfer size limit")
        written = self.stream.write(value)
        self.written += written
        return written

    def flush(self) -> None:
        self.stream.flush()

    def tell(self) -> int:
        return self.stream.tell()


def clone_transfer_root() -> Path:
    return Path(os.environ.get("COPE_CLONE_TRANSFER_DIR", "/var/lib/cope/clone-transfer")).expanduser().resolve()


def export_ttl_hours() -> int:
    try:
        value = int(os.environ.get("COPE_CLONE_EXPORT_TTL_HOURS", str(DEFAULT_EXPORT_TTL_HOURS)))
    except ValueError as exc:
        raise ValueError("COPE_CLONE_EXPORT_TTL_HOURS must be an integer") from exc
    if value < 1 or value > 168:
        raise ValueError("COPE_CLONE_EXPORT_TTL_HOURS must be between 1 and 168")
    return value


def import_ttl_hours() -> int:
    try:
        value = int(os.environ.get("COPE_CLONE_IMPORT_TTL_HOURS", str(DEFAULT_IMPORT_TTL_HOURS)))
    except ValueError as exc:
        raise ValueError("COPE_CLONE_IMPORT_TTL_HOURS must be an integer") from exc
    if value < 1 or value > 168:
        raise ValueError("COPE_CLONE_IMPORT_TTL_HOURS must be between 1 and 168")
    return value


def configured_positive_integer(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def remote_json_max_bytes() -> int:
    return configured_positive_integer("COPE_CLONE_REMOTE_JSON_MAX_BYTES", DEFAULT_REMOTE_JSON_MAX_BYTES)


def clone_transfer_max_bytes() -> int:
    return configured_positive_integer("COPE_CLONE_TRANSFER_MAX_BYTES", DEFAULT_TRANSFER_MAX_BYTES)


def clone_uncompressed_max_bytes() -> int:
    return configured_positive_integer("COPE_CLONE_UNCOMPRESSED_MAX_BYTES", DEFAULT_UNCOMPRESSED_MAX_BYTES)


def clone_row_max_bytes() -> int:
    return configured_positive_integer("COPE_CLONE_ROW_MAX_BYTES", DEFAULT_ROW_MAX_BYTES)


def source_wait_timeout_s() -> int:
    return configured_positive_integer("COPE_CLONE_SOURCE_WAIT_TIMEOUT_S", DEFAULT_SOURCE_WAIT_TIMEOUT_S)


def engine_artifact_max_bytes() -> int:
    return configured_positive_integer("COPE_ENGINE_ARTIFACT_MAX_BYTES", DEFAULT_ENGINE_ARTIFACT_MAX_BYTES)


def engine_artifact_root() -> Path:
    return Path(os.environ.get("COPE_ENGINE_ARTIFACT_DIR", "/var/lib/cope/engine-artifacts")).expanduser().resolve()


def engine_dockerfile_root() -> Path:
    configured = os.environ.get("COPE_ENGINE_DOCKERFILES_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "data" / "engines"


def clone_token_cipher() -> Fernet:
    secret = default_admin_token()
    if not secret:
        raise ValueError("COPE_ADMIN_TOKEN or COPE_ADMIN_TOKEN_FILE is required for environment cloning")
    digest = hashlib.sha256(b"cope-environment-clone-token-v1\0" + secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_clone_token(token: str) -> str:
    return clone_token_cipher().encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_clone_token(ciphertext: str) -> str:
    try:
        return clone_token_cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise RemoteCloneError("The stored source export credential could not be decrypted.") from exc


def clone_catalog_payload() -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in DATASETS:
        groups.setdefault(item.group, []).append(
            {
                "key": item.key,
                "label": item.label,
                "description": item.description,
                "dependencies": tuple(dict.fromkeys((*item.dependencies, *item.companions))),
                "default": item.default,
                "available": item.available,
                "warning": item.warning,
            }
        )
    return {
        "protocol_version": CLONE_PROTOCOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "groups": [{"label": name, "datasets": values} for name, values in groups.items()],
        "presets": [{"key": key, **value} for key, value in PRESETS.items()],
    }


def expanded_selection(values: Iterable[str]) -> tuple[str, ...]:
    selected = set(values)
    unknown = selected - DATASET_BY_KEY.keys()
    if unknown:
        raise ValueError(f"Unknown clone dataset: {sorted(unknown)[0]}")
    unavailable = [key for key in selected if not DATASET_BY_KEY[key].available]
    if unavailable:
        raise ValueError(f"Clone dataset is not portable: {sorted(unavailable)[0]}")
    pending = list(selected)
    while pending:
        key = pending.pop()
        for dependency in (*DATASET_BY_KEY[key].dependencies, *DATASET_BY_KEY[key].companions):
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    ordered: list[str] = []
    visiting: set[str] = set()

    def visit(key: str) -> None:
        if key in ordered:
            return
        if key in visiting:
            raise ValueError(f"Clone dataset dependency cycle at {key}")
        visiting.add(key)
        for dependency in DATASET_BY_KEY[key].dependencies:
            visit(dependency)
        visiting.remove(key)
        ordered.append(key)

    for item in DATASETS:
        if item.key in selected:
            visit(item.key)
    return tuple(ordered)


def environment_instance_id(connection) -> str:
    row = connection.execute("SELECT instance_id FROM environment_identity WHERE singleton = 1").fetchone()
    if row is not None:
        return str(row["instance_id"])
    value = secrets.token_hex(16)
    connection.execute(
        "INSERT INTO environment_identity (singleton, instance_id, created_at) VALUES (1, ?, ?)",
        (value, utc_now()),
    )
    connection.commit()
    return value


def environment_inventory(connection) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in DATASETS:
        if item.table is None:
            continue
        row = connection.execute(f"SELECT COUNT(*) AS count FROM {item.table}").fetchone()
        result[item.key] = 0 if row is None else int(row["count"])
    artifact = connection.execute("SELECT COALESCE(SUM(artifact_size), 0) AS bytes FROM engine_artifacts").fetchone()
    result["artifact_bytes"] = 0 if artifact is None else int(artifact["bytes"])
    return result


def create_environment_export(connection, datasets: Iterable[str]) -> tuple[str, str, str]:
    selected = expanded_selection(datasets)
    export_id = secrets.token_urlsafe(24)
    token = secrets.token_urlsafe(48)
    table_datasets = [DATASET_BY_KEY[key] for key in selected if DATASET_BY_KEY[key].table is not None]
    now = datetime.now(UTC)
    expires_at = (now + timedelta(hours=export_ttl_hours())).isoformat()
    connection.execute(
        """
        INSERT INTO environment_exports (
          export_id, token_hash, status, selection, total_datasets,
          created_at, expires_at
        ) VALUES (?, ?, 'queued', ?, ?, ?, ?)
        """,
        (
            export_id,
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
            json.dumps({"datasets": selected}, separators=(",", ":")),
            len(table_datasets),
            now.isoformat(),
            expires_at,
        ),
    )
    connection.executemany(
        """
        INSERT INTO environment_export_datasets (export_id, dataset_name, position, status)
        VALUES (?, ?, ?, 'pending')
        """,
        ((export_id, item.key, position) for position, item in enumerate(table_datasets)),
    )
    connection.commit()
    return export_id, token, expires_at


def environment_export_payload(connection, export_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM environment_exports WHERE export_id = ?", (export_id,)).fetchone()
    if row is None:
        return None
    datasets = connection.execute(
        "SELECT * FROM environment_export_datasets WHERE export_id = ? ORDER BY position",
        (export_id,),
    ).fetchall()
    return {
        "export_id": row["export_id"],
        "status": row["status"],
        "selection": json.loads(row["selection"] or "{}"),
        "manifest": json.loads(row["manifest"] or "{}"),
        "total_datasets": int(row["total_datasets"]),
        "completed_datasets": int(row["completed_datasets"]),
        "total_rows": int(row["total_rows"]),
        "total_bytes": int(row["total_bytes"]),
        "error": row["error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "expires_at": row["expires_at"],
        "datasets": [dict(item) for item in datasets],
    }


def authorize_environment_export(connection, export_id: str, token: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM environment_exports WHERE export_id = ?", (export_id,)).fetchone()
    if row is None:
        return None
    supplied = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(str(row["token_hash"]), supplied):
        return None
    expires = datetime.fromisoformat(str(row["expires_at"]))
    if expires <= datetime.now(UTC):
        return None
    return dict(row)


def environment_export_dataset_path(export_id: str, dataset_name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,}", export_id) or dataset_name not in DATASET_BY_KEY:
        raise ValueError("Invalid environment export path")
    return clone_transfer_root() / "exports" / export_id / f"{dataset_name}.ndjson.gz"


def environment_export_artifact(connection, export_id: str, artifact_sha256: str) -> dict[str, Any] | None:
    export = environment_export_payload(connection, export_id)
    if export is None or export["status"] != "ready":
        return None
    manifest = export["manifest"]
    return next((item for item in manifest.get("artifacts", []) if item.get("artifact_sha256") == artifact_sha256), None)


def create_clone_from_source(connection, source: str, admin_token: str, datasets: Iterable[str]) -> dict[str, Any]:
    source_url = normalize_source_url(source)
    selected = expanded_selection(datasets)
    opener = authenticated_source_opener(source_url, admin_token)
    capabilities = remote_json(opener, source_url, "/api/admin/environment-export/capabilities")
    if int(capabilities.get("protocol_version", 0)) != CLONE_PROTOCOL_VERSION:
        raise RemoteCloneError("The source uses an incompatible environment clone protocol.")
    if int(capabilities.get("schema_version", 0)) != SCHEMA_VERSION:
        raise RemoteCloneError("The source and destination database schemas must match before cloning.")
    destination_instance = environment_instance_id(connection)
    source_instance = str(capabilities.get("instance_id") or "")
    if not source_instance:
        raise RemoteCloneError("The source did not provide an installation identity.")
    if source_instance == destination_instance:
        raise RemoteCloneError("The source and destination are the same Cope installation.")
    csrf_token = str(capabilities.get("csrf_token") or "")
    export = remote_json(
        opener,
        source_url,
        "/api/admin/environment-exports",
        method="POST",
        payload={"datasets": selected},
        headers={"X-CSRF-Token": csrf_token},
    )
    source_expires_at = str(export.get("expires_at") or "")
    try:
        source_expiry = datetime.fromisoformat(source_expires_at)
    except ValueError as exc:
        raise RemoteCloneError("The source returned an invalid export expiration.") from exc
    if source_expiry.tzinfo is None or source_expiry <= datetime.now(UTC):
        raise RemoteCloneError("The source returned an expired export credential.")
    source_expires_at = source_expiry.astimezone(UTC).isoformat()
    now = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO environment_clone_jobs (
          job_key, source_url, source_instance_id, source_export_id,
          source_export_token_ciphertext, source_expires_at, selection,
          status, phase, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 'queued', ?, ?)
        """,
        (
            secrets.token_urlsafe(24),
            source_url,
            source_instance,
            export["export_id"],
            encrypt_clone_token(str(export["export_token"])),
            source_expires_at,
            json.dumps({"datasets": selected}, separators=(",", ":")),
            now,
            now,
        ),
    )
    job_id = int(cursor.lastrowid)
    add_clone_event(connection, job_id, "info", "queued", None, f"Authenticated with {source_url} and queued the clone.")
    add_clone_event(connection, job_id, "info", "queued", None, f"Selected {len(selected)} datasets after dependency expansion.")
    connection.commit()
    return clone_job_payload(connection, job_id, include_events=True)


def clone_job_payload(connection, job_id: int, *, include_events: bool = True) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM environment_clone_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    steps = connection.execute(
        "SELECT * FROM environment_clone_job_steps WHERE job_id = ? ORDER BY position",
        (job_id,),
    ).fetchall()
    events = []
    if include_events:
        events = connection.execute(
            "SELECT * FROM environment_clone_events WHERE job_id = ? ORDER BY id DESC LIMIT 1000",
            (job_id,),
        ).fetchall()[::-1]
    payload = dict(row)
    payload["selection"] = json.loads(payload["selection"] or "{}")
    payload.pop("source_export_token_ciphertext", None)
    payload["steps"] = [dict(item) for item in steps]
    payload["events"] = [
        {**dict(item), "detail": json.loads(item["detail"] or "{}")}
        for item in events
    ]
    return payload


def list_clone_jobs(connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT id FROM environment_clone_jobs ORDER BY created_at DESC, id DESC LIMIT ?",
        (max(1, min(limit, 100)),),
    ).fetchall()
    return [clone_job_payload(connection, int(row["id"]), include_events=False) for row in rows]


def add_clone_event(
    connection,
    job_id: int,
    level: str,
    phase: str,
    dataset_name: str | None,
    message: str,
    detail: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO environment_clone_events (
          job_id, level, phase, dataset_name, message, detail, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (job_id, level, phase, dataset_name, message, json.dumps(detail or {}, separators=(",", ":")), utc_now()),
    )


def cancel_clone_job(connection, job_id: int) -> bool:
    cursor = connection.execute(
        """
        UPDATE environment_clone_jobs
        SET cancel_requested = 1, updated_at = ?
        WHERE id = ? AND status IN ('queued', 'waiting_source', 'transferring', 'importing', 'verifying')
        """,
        (utc_now(), job_id),
    )
    if cursor.rowcount:
        add_clone_event(connection, job_id, "warning", "cancelling", None, "Cancellation requested. The current atomic operation will finish first.")
    connection.commit()
    return bool(cursor.rowcount)


def resume_clone_job(connection, job_id: int) -> bool:
    cursor = connection.execute(
        """
        UPDATE environment_clone_jobs
        SET status = 'queued', phase = 'queued', cancel_requested = 0,
            error = '', finished_at = NULL, updated_at = ?
        WHERE id = ? AND status IN ('failed', 'cancelled')
          AND source_export_token_ciphertext <> ''
          AND source_expires_at > ?
    """,
        (utc_now(), job_id, utc_now()),
    )
    if cursor.rowcount:
        add_clone_event(connection, job_id, "info", "queued", None, "Clone queued to resume from durable progress.")
    connection.commit()
    return bool(cursor.rowcount)


def run_environment_clone_service(database_url: str, poll_interval_s: float = 1.0) -> None:
    lock_connection = connect_database(database_url)
    raw = lock_connection._connection()
    row = raw.execute(
        "SELECT pg_try_advisory_lock(%s) AS acquired",
        (CLONE_RUNNER_LOCK_KEY,),
    ).fetchone()
    raw.commit()
    if row is None or not row["acquired"]:
        lock_connection.close()
        raise RuntimeError("another environment clone runner already owns the service lock")
    heartbeat_stop = threading.Event()
    try:
        reset_environment_clone_work(database_url)
        touch_environment_clone_heartbeat(database_url)
        heartbeat_thread = threading.Thread(
            target=environment_clone_heartbeat_loop,
            args=(database_url, heartbeat_stop),
            name="clone-runner-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()
        last_cleanup = 0.0
        while True:
            now = time.monotonic()
            if now - last_cleanup >= 300:
                expire_environment_exports(database_url)
                expire_clone_imports(database_url)
                last_cleanup = now
            worked = process_one_environment_export(database_url)
            worked = process_one_clone_job(database_url) or worked
            if not worked:
                time.sleep(max(0.2, poll_interval_s))
    finally:
        heartbeat_stop.set()
        try:
            raw.execute("SELECT pg_advisory_unlock(%s)", (CLONE_RUNNER_LOCK_KEY,))
            raw.commit()
        finally:
            lock_connection.close()


def touch_environment_clone_heartbeat(database_url: str) -> None:
    connection = connect_database(database_url)
    try:
        touch_service_heartbeat(connection, "clone-runner", app_version())
        connection.commit()
    finally:
        connection.close()


def environment_clone_heartbeat_loop(database_url: str, stopped: threading.Event) -> None:
    while not stopped.wait(5.0):
        try:
            touch_environment_clone_heartbeat(database_url)
        except Exception:
            LOG.exception("clone runner heartbeat failed")


def expire_environment_exports(database_url: str) -> None:
    connection = connect_database(database_url)
    try:
        rows = connection.execute(
            """
            UPDATE environment_exports
            SET status = 'expired', token_hash = ?, cancel_requested = 1,
                error = 'Environment export expired.', finished_at = COALESCE(finished_at, ?)
            WHERE expires_at <= ?
            RETURNING export_id
            """,
            ("0" * 64, utc_now(), utc_now()),
        ).fetchall()
        connection.commit()
    finally:
        connection.close()
    for row in rows:
        remove_clone_transfer_tree("exports", str(row["export_id"]))
    if rows:
        connection = connect_database(database_url)
        try:
            connection.executemany(
                "DELETE FROM environment_exports WHERE export_id = ? AND status = 'expired'",
                ((str(row["export_id"]),) for row in rows),
            )
            connection.commit()
        finally:
            connection.close()


def expire_clone_imports(database_url: str) -> None:
    now = datetime.now(UTC)
    cutoff = (now - timedelta(hours=import_ttl_hours())).isoformat()
    connection = connect_database(database_url)
    try:
        rows = connection.execute(
            """
            UPDATE environment_clone_jobs
            SET source_export_token_ciphertext = '', updated_at = ?
            WHERE status IN ('completed', 'failed', 'cancelled')
              AND source_export_token_ciphertext <> ''
              AND (source_expires_at <= ? OR finished_at <= ?)
            RETURNING id
            """,
            (now.isoformat(), now.isoformat(), cutoff),
        ).fetchall()
        connection.commit()
    finally:
        connection.close()
    for row in rows:
        remove_clone_transfer_tree("imports", str(row["id"]))


def remove_clone_transfer_tree(scope: str, identifier: str) -> None:
    if scope not in {"exports", "imports"} or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier) is None:
        raise ValueError("Invalid clone transfer cleanup target")
    root = (clone_transfer_root() / scope).resolve()
    target = (root / identifier).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Clone transfer cleanup target escaped its root") from exc
    if target.is_dir():
        shutil.rmtree(target)


def reset_environment_clone_work(database_url: str) -> None:
    connection = connect_database(database_url)
    try:
        connection.execute("UPDATE environment_exports SET status = 'queued' WHERE status = 'preparing'")
        connection.execute(
            """
            UPDATE environment_clone_jobs
            SET status = 'queued', phase = 'queued', updated_at = ?
            WHERE status IN ('waiting_source', 'transferring', 'importing', 'verifying')
            """,
            (utc_now(),),
        )
        connection.commit()
    finally:
        connection.close()


def process_one_environment_export(database_url: str) -> bool:
    connection = connect_database(database_url)
    try:
        connection.execute("BEGIN")
        row = connection.execute(
            """
            SELECT export_id FROM environment_exports
            WHERE status = 'queued' AND expires_at > ?
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            (utc_now(),),
        ).fetchone()
        if row is None:
            connection.rollback()
            return False
        export_id = str(row["export_id"])
        connection.execute(
            "UPDATE environment_exports SET status = 'preparing', started_at = COALESCE(started_at, ?), error = '' WHERE export_id = ?",
            (utc_now(), export_id),
        )
        connection.commit()
    finally:
        connection.close()
    try:
        build_environment_export(database_url, export_id)
    except CloneCancelled:
        set_export_terminal(database_url, export_id, "cancelled", "Export cancelled.")
        remove_clone_transfer_tree("exports", export_id)
    except Exception as exc:
        set_export_terminal(database_url, export_id, "failed", str(exc))
        remove_clone_transfer_tree("exports", export_id)
    return True


def build_environment_export(database_url: str, export_id: str) -> None:
    state = connect_database(database_url)
    try:
        export = environment_export_payload(state, export_id)
        if export is None:
            raise RuntimeError("Environment export disappeared.")
        selected = set(export["selection"].get("datasets", []))
        instance_id = environment_instance_id(state)
    finally:
        state.close()
    directory = clone_transfer_root() / "exports" / export_id
    directory.mkdir(parents=True, exist_ok=True)
    snapshot = connect_database(database_url)
    entries: list[dict[str, Any]] = []
    uncompressed_total = 0
    transfer_limit = clone_transfer_max_bytes()
    uncompressed_limit = clone_uncompressed_max_bytes()
    row_limit = clone_row_max_bytes()
    try:
        snapshot.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for position, key in enumerate(key for key in export["selection"].get("datasets", []) if DATASET_BY_KEY[key].table is not None):
            ensure_export_active(database_url, export_id)
            item = DATASET_BY_KEY[key]
            update_export_dataset(database_url, export_id, key, status="exporting", started_at=utc_now())
            destination = environment_export_dataset_path(export_id, key)
            temporary = destination.with_suffix(destination.suffix + ".part")
            temporary.unlink(missing_ok=True)
            count = 0
            uncompressed_bytes = 0
            max_id: int | None = None
            remaining_transfer = transfer_limit - sum(entry["bytes"] for entry in entries)
            with temporary.open("wb") as raw_output:
                bounded_output = BoundedCloneWriter(raw_output, remaining_transfer)
                with gzip.GzipFile(filename="", mode="wb", fileobj=bounded_output, compresslevel=6, mtime=0) as output:
                    cursor = snapshot._connection().cursor(name=f"clone_{position}_{secrets.token_hex(4)}")
                    cursor.itersize = 1000
                    try:
                        cursor.execute(f"SELECT * FROM {item.table} ORDER BY {dataset_order_expression(item)}")
                        for raw in cursor:
                            row = sanitized_export_row(key, dict(raw), selected)
                            if item.serial:
                                row_id = row.get("id")
                                if isinstance(row_id, bool) or not isinstance(row_id, int) or row_id < 1:
                                    raise ValueError("Environment export contains an invalid serial identity")
                                max_id = row_id if max_id is None else max(max_id, row_id)
                            line = (json.dumps(row, separators=(",", ":"), ensure_ascii=False, default=str) + "\n").encode("utf-8")
                            if len(line) > row_limit:
                                raise ValueError("Environment export contains a row that exceeds the configured size limit")
                            if uncompressed_total + uncompressed_bytes + len(line) > uncompressed_limit:
                                raise ValueError("Environment export exceeds the configured uncompressed size limit")
                            output.write(line)
                            uncompressed_bytes += len(line)
                            count += 1
                            if count % 1000 == 0:
                                ensure_export_active(database_url, export_id)
                    finally:
                        cursor.close()
                raw_output.flush()
                os.fsync(raw_output.fileno())
            os.replace(temporary, destination)
            digest = sha256_file(destination)
            byte_count = destination.stat().st_size
            entries.append(
                {
                    "name": key,
                    "label": item.label,
                    "position": position,
                    "rows": count,
                    "max_id": max_id,
                    "bytes": byte_count,
                    "uncompressed_bytes": uncompressed_bytes,
                    "sha256": digest,
                    "file_name": destination.name,
                }
            )
            uncompressed_total += uncompressed_bytes
            update_export_dataset(
                database_url,
                export_id,
                key,
                status="ready",
                row_count=count,
                byte_count=byte_count,
                sha256=digest,
                file_name=destination.name,
                finished_at=utc_now(),
            )
        artifacts = []
        if "artifact_files" in selected:
            for row in snapshot.execute("SELECT * FROM engine_artifacts ORDER BY created_at, build_hash"):
                if int(row["artifact_size"]) > engine_artifact_max_bytes():
                    raise ValueError("Environment export contains an artifact that exceeds the configured size limit")
                artifacts.append(dict(row))
        if sum(entry["bytes"] for entry in entries) + sum(int(item["artifact_size"]) for item in artifacts) > clone_transfer_max_bytes():
            raise ValueError("Environment export exceeds the configured clone transfer size limit")
        snapshot.rollback()
    finally:
        snapshot.close()
    manifest = {
        "protocol_version": CLONE_PROTOCOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "source_instance_id": instance_id,
        "source_version": app_version(),
        "created_at": utc_now(),
        "selection": list(export["selection"].get("datasets", [])),
        "datasets": entries,
        "artifacts": artifacts,
    }
    manifest_json = json.dumps(manifest, separators=(",", ":"))
    if len(manifest_json.encode("utf-8")) > remote_json_max_bytes():
        raise ValueError("Environment export manifest exceeds the configured response size limit")
    state = connect_database(database_url)
    try:
        cursor = state.execute(
            """
            UPDATE environment_exports
            SET status = 'ready', manifest = ?, completed_datasets = total_datasets,
                total_rows = ?, total_bytes = ?, finished_at = ?, error = ''
            WHERE export_id = ? AND status = 'preparing' AND cancel_requested = 0
            """,
            (
                manifest_json,
                sum(item["rows"] for item in entries),
                sum(item["bytes"] for item in entries) + sum(int(item["artifact_size"]) for item in artifacts),
                utc_now(),
                export_id,
            ),
        )
        if cursor.rowcount == 0:
            state.rollback()
            raise CloneCancelled()
        state.commit()
    finally:
        state.close()


def dataset_order_expression(item: CloneDataset) -> str:
    if item.serial:
        return "id"
    orders = {
        "engine_badges": "badge_id, engine_id",
        "engine_artifacts": "created_at, build_hash",
        "benchmark_hardware": "created_at, hardware_key",
        "participants": "tournament_id, seed",
        "moves": "game_id, ply",
        "worker_tournament_permissions": "worker_id, tournament_id",
        "worker_event_permissions": "worker_id, event_id",
        "worker_engine_discoveries": "worker_id, local_key",
        "game_hardware_scores": "game_id, engine_version_id",
        "rating_list_ratings": "rating_list_id, engine_id",
        "tournament_rating_list_commits": "tournament_id, rating_list_id",
        "event_contest_cast": "contest_id, position",
        "event_chat_settings": "event_id",
        "engine_relay_fixture_teams": "fixture_id, position",
        "puzzle_gauntlet_events": "event_id",
        "chat_settings": "key",
        "system_chat_events": "tournament_id, event_key",
    }
    return orders.get(item.key, "1")


def sanitized_export_row(dataset: str, row: dict[str, Any], selection: set[str]) -> dict[str, Any]:
    if dataset == "git_hosts":
        row["access_token"] = ""
        row["base_url"] = sanitized_clone_url(row.get("base_url"))
        row["api_url"] = sanitized_clone_url(row.get("api_url"))
    elif dataset == "engine_versions":
        row["repository_url"] = sanitized_clone_url(row.get("repository_url"))
    elif dataset == "workers":
        row["token_hash"] = None
        row["token_expires_at"] = None
        row["session_id"] = None
        row["status"] = "offline"
    elif dataset == "benchmarkers":
        row["token_hash"] = None
        row["token_expires_at"] = None
        row["session_id"] = None
        row["status"] = "offline"
    elif dataset == "benchmark_jobs":
        row["job_key"] = stable_clone_key("benchmark-job", row["job_key"])
        row["benchmarker_id"] = None
        if "benchmark_job_output" not in selection:
            row["output"] = ""
    elif dataset == "game_assignments":
        row["assignment_key"] = stable_clone_key("game-assignment", row["assignment_key"])
    elif dataset == "game_assignment_progress":
        row["assignment_key"] = stable_clone_key("game-assignment", row["assignment_key"])
    elif dataset == "tool_jobs":
        row["job_key"] = stable_clone_key("tool-job", row["job_key"])
        row["worker_id"] = None
        if row["status"] in {"queued", "running"}:
            row["status"] = "cancelled"
            row["finished_at"] = terminal_timestamp(row)
            row["error"] = "Cloned without active execution state"
    elif dataset == "deployment_targets":
        row["target_id"] = None
        row["repository_url"] = sanitized_clone_url(row.get("repository_url"))
    return row


def sanitized_clone_url(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw or "://" not in raw:
        return raw
    try:
        parsed = urllib.parse.urlsplit(raw)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Clone dataset contains an invalid URL") from exc
    if not parsed.scheme or not host:
        raise ValueError("Clone dataset contains an invalid URL")
    netloc_host = f"[{host}]" if ":" in host else host
    netloc = netloc_host if port is None else f"{netloc_host}:{port}"
    query = urllib.parse.urlencode(
        [
            (key, query_value)
            for key, query_value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if SENSITIVE_URL_QUERY_KEY.search(key) is None
        ],
        doseq=True,
    )
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, query, ""))


def stable_clone_key(namespace: str, value: Any) -> str:
    digest = hashlib.sha256(f"{namespace}:{value}".encode("utf-8")).hexdigest()
    return f"clone-{digest}"


def terminal_timestamp(row: dict[str, Any]) -> str:
    for field in ("finished_at", "updated_at", "started_at", "scheduled_at", "created_at", "requested_at", "sent_at", "occurred_at"):
        value = row.get(field)
        if value:
            return str(value)
    return "1970-01-01T00:00:00+00:00"


def ensure_export_active(database_url: str, export_id: str) -> None:
    connection = connect_database(database_url)
    try:
        row = connection.execute(
            "SELECT status, expires_at, cancel_requested FROM environment_exports WHERE export_id = ?",
            (export_id,),
        ).fetchone()
        if (
            row is None
            or int(row["cancel_requested"])
            or row["status"] != "preparing"
            or datetime.fromisoformat(str(row["expires_at"])) <= datetime.now(UTC)
        ):
            raise CloneCancelled()
    finally:
        connection.close()


def update_export_dataset(database_url: str, export_id: str, key: str, **values: Any) -> None:
    allowed = {"status", "row_count", "byte_count", "sha256", "file_name", "error", "started_at", "finished_at"}
    selected = [(name, value) for name, value in values.items() if name in allowed]
    connection = connect_database(database_url)
    try:
        assignments = ", ".join(f"{name} = ?" for name, _ in selected)
        connection.execute(
            f"UPDATE environment_export_datasets SET {assignments} WHERE export_id = ? AND dataset_name = ?",
            (*[value for _, value in selected], export_id, key),
        )
        completed = connection.execute(
            "SELECT COUNT(*) AS count FROM environment_export_datasets WHERE export_id = ? AND status = 'ready'",
            (export_id,),
        ).fetchone()
        connection.execute(
            "UPDATE environment_exports SET completed_datasets = ? WHERE export_id = ?",
            (int(completed["count"]), export_id),
        )
        connection.commit()
    finally:
        connection.close()


def set_export_terminal(database_url: str, export_id: str, status: str, error: str) -> None:
    connection = connect_database(database_url)
    try:
        connection.execute(
            "UPDATE environment_exports SET status = ?, error = ?, finished_at = ? WHERE export_id = ?",
            (status, error[-8000:], utc_now(), export_id),
        )
        connection.commit()
    finally:
        connection.close()


def process_one_clone_job(database_url: str) -> bool:
    connection = connect_database(database_url)
    try:
        connection.execute("BEGIN")
        row = connection.execute(
            """
            SELECT id, status FROM environment_clone_jobs
            WHERE status = 'queued'
               OR (status = 'waiting_source' AND updated_at <= ?)
               OR (status = 'cleaning_source' AND updated_at <= ?)
            ORDER BY CASE status
              WHEN 'cleaning_source' THEN 0
              WHEN 'queued' THEN 1
              ELSE 2
            END, created_at, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            (
                (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                (datetime.now(UTC) - timedelta(seconds=10)).isoformat(),
            ),
        ).fetchone()
        if row is None:
            connection.rollback()
            return False
        job_id = int(row["id"])
        claimed_status = str(row["status"])
        if claimed_status == "queued":
            connection.execute(
                """
                UPDATE environment_clone_jobs
                SET status = 'waiting_source', phase = 'waiting_source',
                    started_at = ?, updated_at = ?, error = ''
                WHERE id = ?
                """,
                (utc_now(), utc_now(), job_id),
            )
            add_clone_event(connection, job_id, "info", "waiting_source", None, "Waiting for the source to finish its consistent export snapshot.")
        else:
            connection.execute(
                "UPDATE environment_clone_jobs SET updated_at = ? WHERE id = ?",
                (utc_now(), job_id),
            )
        connection.commit()
    finally:
        connection.close()
    try:
        if claimed_status == "cleaning_source":
            run_clone_source_cleanup(database_url, job_id)
        else:
            run_clone_job(database_url, job_id)
    except CloneCleanupDeferred as exc:
        defer_clone_source_cleanup(database_url, job_id, str(exc))
    except CloneCancelled:
        set_clone_terminal(database_url, job_id, "cancelled", "Clone cancelled.")
    except CloneConflict as exc:
        set_clone_terminal(database_url, job_id, "failed", str(exc), conflict=True)
    except Exception as exc:
        set_clone_terminal(database_url, job_id, "failed", str(exc))
    return True


def run_clone_job(database_url: str, job_id: int) -> None:
    job = load_clone_job(database_url, job_id)
    source_url = str(job["source_url"])
    export_id = str(job["source_export_id"])
    token = decrypt_clone_token(str(job["source_export_token_ciphertext"]))
    headers = {"Authorization": f"Bearer {token}"}
    status_path = f"/api/environment-exports/{urllib.parse.quote(export_id)}/status"
    started_at = datetime.fromisoformat(str(job["started_at"]))
    if datetime.now(UTC) - started_at >= timedelta(seconds=source_wait_timeout_s()):
        raise RemoteCloneError("Timed out waiting for the source environment export.")
    ensure_clone_active(database_url, job_id)
    status = direct_remote_json(source_url, status_path, headers=headers)
    source_status = status.get("status")
    update_clone_from_source_status(database_url, job_id, status)
    if source_status in {"failed", "cancelled", "expired"}:
        raise RemoteCloneError(str(status.get("error") or f"Source export {source_status}."))
    if source_status != "ready":
        if source_status not in {"queued", "preparing"}:
            raise RemoteCloneError("The source returned an invalid environment export status.")
        return
    manifest = direct_remote_json(
        source_url,
        f"/api/environment-exports/{urllib.parse.quote(export_id)}/manifest",
        headers=headers,
    )
    if int(manifest.get("protocol_version", 0)) != CLONE_PROTOCOL_VERSION or int(manifest.get("schema_version", 0)) != SCHEMA_VERSION:
        raise RemoteCloneError("The completed source export is incompatible with this destination.")
    if manifest.get("source_instance_id") != job["source_instance_id"]:
        raise RemoteCloneError("The source installation identity changed during the clone.")
    validate_clone_manifest(manifest, json.loads(job["selection"] or "{}").get("datasets", []))
    prepare_clone_steps(database_url, job_id, manifest)
    directory = clone_transfer_root() / "imports" / str(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    set_clone_phase(database_url, job_id, "transferring", "transferring", "Downloading compressed dataset snapshots.")
    for dataset in manifest.get("datasets", []):
        ensure_clone_active(database_url, job_id)
        key = str(dataset["name"])
        destination = directory / f"{key}.ndjson.gz"
        set_clone_step(database_url, job_id, key, "transferring", started_at=utc_now())
        add_clone_log(database_url, job_id, "info", "transferring", key, f"Downloading {dataset['label']} ({dataset['rows']:,} rows, {dataset['bytes']:,} bytes).")
        download_remote_file(
            source_url,
            f"/api/environment-exports/{urllib.parse.quote(export_id)}/datasets/{urllib.parse.quote(key)}",
            headers,
            destination,
            int(dataset["bytes"]),
            str(dataset["sha256"]),
            lambda completed, key=key: update_clone_transfer_progress(database_url, job_id, key, completed),
        )
        set_clone_step(database_url, job_id, key, "validating", completed_bytes=int(dataset["bytes"]))
        add_clone_log(database_url, job_id, "success", "transferring", key, f"Verified dataset SHA-256 {dataset['sha256']}.")
    if manifest.get("artifacts"):
        transfer_artifacts(database_url, job_id, source_url, export_id, headers, manifest["artifacts"])
    set_clone_phase(database_url, job_id, "importing", "importing", "Importing datasets in dependency order.")
    selected = set(manifest.get("selection", []))
    for dataset in manifest.get("datasets", []):
        ensure_clone_active(database_url, job_id)
        key = str(dataset["name"])
        set_clone_step(database_url, job_id, key, "importing")
        add_clone_log(database_url, job_id, "info", "importing", key, f"Importing {dataset['rows']:,} rows into {DATASET_BY_KEY[key].table}.")
        inserted, skipped = import_clone_dataset(
            database_url,
            job_id,
            key,
            directory / f"{key}.ndjson.gz",
            selected,
            expected_rows=int(dataset["rows"]),
            expected_uncompressed_bytes=int(dataset["uncompressed_bytes"]),
            expected_max_id=dataset.get("max_id"),
        )
        set_clone_step(
            database_url,
            job_id,
            key,
            "completed",
            completed_rows=int(dataset["rows"]),
            inserted_rows=inserted,
            skipped_rows=skipped,
            finished_at=utc_now(),
        )
        add_clone_log(database_url, job_id, "success", "importing", key, f"Completed {DATASET_BY_KEY[key].label}: {inserted:,} inserted, {skipped:,} already present.")
    set_clone_phase(database_url, job_id, "verifying", "verifying", "Verifying imported artifacts.")
    materialize_clone_artifacts(job_id, manifest.get("artifacts", []))
    verify_clone_artifacts(database_url, job_id, manifest.get("artifacts", []))
    ensure_clone_active(database_url, job_id)
    set_clone_phase(
        database_url,
        job_id,
        "cleaning_source",
        "cleaning_source",
        "Revoking the completed source export credential and snapshot.",
    )
    run_clone_source_cleanup(database_url, job_id)


def run_clone_source_cleanup(database_url: str, job_id: int) -> None:
    job = load_clone_job(database_url, job_id)
    source_url = str(job["source_url"])
    export_id = str(job["source_export_id"])
    try:
        if datetime.fromisoformat(str(job["source_expires_at"])) > datetime.now(UTC):
            token = decrypt_clone_token(str(job["source_export_token_ciphertext"]))
            direct_remote_json(
                source_url,
                f"/api/environment-exports/{urllib.parse.quote(export_id)}",
                headers={"Authorization": f"Bearer {token}"},
                method="DELETE",
                missing_ok=True,
            )
        remove_clone_transfer_tree("imports", str(job_id))
    except (OSError, RemoteCloneError) as exc:
        raise CloneCleanupDeferred(f"Source export cleanup will retry: {exc}") from exc
    connection = connect_database(database_url)
    try:
        connection.execute(
            """
            UPDATE environment_clone_jobs
            SET status = 'completed', phase = 'completed', completed_datasets = total_datasets,
                completed_rows = total_rows, completed_bytes = total_bytes,
                source_export_token_ciphertext = '', updated_at = ?, finished_at = ?, error = ''
            WHERE id = ?
            """,
            (utc_now(), utc_now(), job_id),
        )
        add_clone_event(connection, job_id, "success", "cleaning_source", None, "Source export credential revoked and transfer snapshots removed.")
        add_clone_event(connection, job_id, "success", "completed", None, "Environment clone completed and passed final verification.")
        connection.commit()
    finally:
        connection.close()


def defer_clone_source_cleanup(database_url: str, job_id: int, error: str) -> None:
    connection = connect_database(database_url)
    try:
        connection.execute(
            """
            UPDATE environment_clone_jobs
            SET status = 'cleaning_source', phase = 'cleaning_source',
                error = ?, updated_at = ?
            WHERE id = ?
            """,
            (error[-8000:], utc_now(), job_id),
        )
        connection.commit()
    finally:
        connection.close()


def prepare_clone_steps(database_url: str, job_id: int, manifest: dict[str, Any]) -> None:
    datasets = manifest.get("datasets", [])
    artifacts = manifest.get("artifacts", [])
    total_bytes = sum(int(item["bytes"]) for item in datasets) + sum(int(item["artifact_size"]) for item in artifacts)
    connection = connect_database(database_url)
    try:
        connection.execute("DELETE FROM environment_clone_job_steps WHERE job_id = ?", (job_id,))
        connection.executemany(
            """
            INSERT INTO environment_clone_job_steps (
              job_id, dataset_name, position, status, total_rows, total_bytes
            ) VALUES (?, ?, ?, 'waiting', ?, ?)
            """,
            (
                (job_id, item["name"], position, int(item["rows"]), int(item["bytes"]))
                for position, item in enumerate(datasets)
            ),
        )
        connection.execute(
            """
            UPDATE environment_clone_jobs
            SET total_datasets = ?, completed_datasets = 0,
                total_rows = ?, completed_rows = 0,
                total_bytes = ?, completed_bytes = 0,
                artifacts_total = ?, artifacts_completed = 0, artifacts_skipped = 0,
                updated_at = ?
            WHERE id = ?
            """,
            (len(datasets), sum(int(item["rows"]) for item in datasets), total_bytes, len(artifacts), utc_now(), job_id),
        )
        connection.commit()
    finally:
        connection.close()


def validate_clone_manifest(manifest: dict[str, Any], expected_selection: Iterable[str]) -> None:
    expected = tuple(expanded_selection(expected_selection))
    selection = tuple(str(key) for key in manifest.get("selection", []))
    if selection != expected:
        raise RemoteCloneError("The source export selection does not match the requested clone scope.")
    expected_datasets = tuple(key for key in expected if DATASET_BY_KEY[key].table is not None)
    datasets = manifest.get("datasets")
    if not isinstance(datasets, list) or tuple(str(item.get("name")) for item in datasets if isinstance(item, dict)) != expected_datasets:
        raise RemoteCloneError("The source export dataset manifest is incomplete or out of order.")
    dataset_bytes = 0
    uncompressed_bytes = 0
    for item in datasets:
        if not isinstance(item, dict):
            raise RemoteCloneError("The source export contains an invalid dataset entry.")
        rows = manifest_integer(item, "rows", minimum=0)
        byte_count = manifest_integer(item, "bytes", minimum=0)
        expanded_byte_count = manifest_integer(item, "uncompressed_bytes", minimum=0)
        dataset = DATASET_BY_KEY[str(item["name"])]
        if dataset.serial:
            if rows == 0 and item.get("max_id") is not None:
                raise RemoteCloneError("The source export contains invalid serial identity metadata.")
            if rows > 0:
                manifest_integer(item, "max_id", minimum=1)
        elif item.get("max_id") is not None:
            raise RemoteCloneError("The source export contains unexpected serial identity metadata.")
        if re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))) is None:
            raise RemoteCloneError("The source export contains an invalid dataset digest.")
        dataset_bytes += byte_count
        uncompressed_bytes += expanded_byte_count
    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list) or (artifacts and "artifact_files" not in expected):
        raise RemoteCloneError("The source export contains unexpected artifact files.")
    seen_artifacts: set[str] = set()
    seen_storage_keys: set[str] = set()
    artifact_bytes = 0
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise RemoteCloneError("The source export contains an invalid artifact entry.")
        digest = str(artifact.get("artifact_sha256", ""))
        build_hash = str(artifact.get("build_hash", ""))
        storage_key = str(artifact.get("storage_key", ""))
        if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in (digest, build_hash, storage_key)):
            raise RemoteCloneError("The source export contains an invalid artifact identity.")
        artifact_size = manifest_integer(artifact, "artifact_size", minimum=1)
        if digest in seen_artifacts or storage_key in seen_storage_keys:
            raise RemoteCloneError("The source export contains duplicate or invalid artifact metadata.")
        if artifact_size > engine_artifact_max_bytes():
            raise RemoteCloneError("The source export contains an artifact that exceeds the configured size limit.")
        if artifact.get("artifact_format") != ARTIFACT_FORMAT or artifact.get("platform") != ARTIFACT_PLATFORM:
            raise RemoteCloneError("The source export contains an unsupported artifact format.")
        seen_artifacts.add(digest)
        seen_storage_keys.add(storage_key)
        artifact_bytes += artifact_size
    if dataset_bytes + artifact_bytes > clone_transfer_max_bytes():
        raise RemoteCloneError("The source export exceeds the configured clone transfer size limit.")
    if uncompressed_bytes > clone_uncompressed_max_bytes():
        raise RemoteCloneError("The source export exceeds the configured uncompressed size limit.")


def manifest_integer(item: dict[str, Any], field: str, *, minimum: int) -> int:
    value = item.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RemoteCloneError("The source export contains invalid numeric metadata.")
    result = value
    if result < minimum or result > 2**63 - 1:
        raise RemoteCloneError("The source export contains invalid numeric metadata.")
    return result


def transfer_artifacts(
    database_url: str,
    job_id: int,
    source_url: str,
    export_id: str,
    headers: dict[str, str],
    artifacts: list[dict[str, Any]],
) -> None:
    root = engine_artifact_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging_root = clone_transfer_root() / "imports" / str(job_id) / "artifacts"
    staging_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for index, artifact in enumerate(artifacts, start=1):
        ensure_clone_active(database_url, job_id)
        digest = str(artifact["artifact_sha256"])
        size = int(artifact["artifact_size"])
        destination = root / f"{artifact['storage_key']}.tar.gz"
        if destination.is_file() and destination.stat().st_size == size and sha256_file(destination) == digest:
            validate_artifact_archive(
                destination,
                expected_build_hash=str(artifact["build_hash"]),
                expected_entrypoint=str(artifact["entrypoint"]),
            )
            update_artifact_progress(database_url, job_id, skipped=True, completed_bytes=size)
            add_clone_log(database_url, job_id, "info", "transferring", "artifact_files", f"Reused artifact {index}/{len(artifacts)} {digest[:16]}… ({size:,} bytes).")
            continue
        if destination.exists():
            raise CloneConflict(f"Destination artifact storage key conflicts with source artifact {digest}.")
        staged = staging_root / f"{artifact['storage_key']}.tar.gz"
        add_clone_log(database_url, job_id, "info", "transferring", "artifact_files", f"Downloading artifact {index}/{len(artifacts)} {digest[:16]}… ({size:,} bytes).")
        reported = [0]

        def artifact_progress(completed: int) -> None:
            increment = max(0, completed - reported[0])
            reported[0] = completed
            update_artifact_bytes(database_url, job_id, increment)

        download_remote_file(
            source_url,
            f"/api/environment-exports/{urllib.parse.quote(export_id)}/artifacts/{digest}",
            headers,
            staged,
            size,
            digest,
            artifact_progress,
        )
        validate_artifact_archive(
            staged,
            expected_build_hash=str(artifact["build_hash"]),
            expected_entrypoint=str(artifact["entrypoint"]),
        )
        update_artifact_progress(database_url, job_id, skipped=False)
        add_clone_log(database_url, job_id, "success", "transferring", "artifact_files", f"Verified artifact {digest}.")


def import_clone_dataset(
    database_url: str,
    job_id: int,
    key: str,
    path: Path,
    selected: set[str],
    *,
    expected_rows: int,
    expected_uncompressed_bytes: int,
    expected_max_id: int | None,
) -> tuple[int, int]:
    item = DATASET_BY_KEY[key]
    if item.table is None:
        return 0, 0
    connection = connect_database(database_url)
    inserted = 0
    skipped = 0
    processed = 0
    processed_bytes = 0
    seen_rows = 0
    max_id: int | None = None
    row_limit = clone_row_max_bytes()
    try:
        if item.serial:
            reserve_clone_sequence(connection, item.table, expected_max_id)
        connection.execute("BEGIN")
        batch: list[dict[str, Any]] = []
        with gzip.open(path, "rb") as source:
            while True:
                remaining = max(expected_uncompressed_bytes - processed_bytes, 0)
                line = source.readline(min(row_limit, remaining) + 1)
                if not line:
                    break
                if len(line) > remaining:
                    raise RemoteCloneError("The source dataset expands beyond its declared size.")
                if len(line) > row_limit or not line.endswith(b"\n"):
                    raise RemoteCloneError("The source dataset contains an oversized or truncated row.")
                processed_bytes += len(line)
                if not line.strip():
                    raise RemoteCloneError("The source dataset contains an empty row.")
                try:
                    payload = json.loads(line.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise RemoteCloneError("The source dataset contains invalid JSON.") from exc
                if not isinstance(payload, dict):
                    raise RemoteCloneError("The source dataset row is not an object.")
                seen_rows += 1
                if seen_rows > expected_rows:
                    raise RemoteCloneError("The source dataset contains more rows than declared.")
                row = imported_row(key, payload, selected)
                if item.serial:
                    row_id = row.get("id")
                    if isinstance(row_id, bool) or not isinstance(row_id, int) or row_id < 1:
                        raise RemoteCloneError("The source dataset contains an invalid serial identity.")
                    max_id = row_id if max_id is None else max(max_id, row_id)
                batch.append(row)
                if len(batch) >= 250:
                    added = insert_clone_batch(connection, item.table, batch)
                    inserted += added
                    skipped += len(batch) - added
                    processed += len(batch)
                    batch.clear()
                    update_clone_row_progress(database_url, job_id, key, processed)
                    ensure_clone_active(database_url, job_id)
            if processed_bytes != expected_uncompressed_bytes or seen_rows != expected_rows:
                raise RemoteCloneError("The source dataset does not match its declared row or size totals.")
            if item.serial and max_id != expected_max_id:
                raise RemoteCloneError("The source dataset does not match its declared serial identity maximum.")
            if batch:
                added = insert_clone_batch(connection, item.table, batch)
                inserted += added
                skipped += len(batch) - added
                processed += len(batch)
        connection.commit()
        if key == "engine_versions" and "engine_dockerfiles" in selected:
            materialize_clone_dockerfiles(job_id, path, selected)
        update_clone_row_progress(database_url, job_id, key, processed)
        return inserted, skipped
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def imported_row(dataset: str, row: dict[str, Any], selected: set[str]) -> dict[str, Any]:
    terminal_at = terminal_timestamp(row)
    if dataset == "git_hosts":
        row["access_token"] = ""
        row["base_url"] = sanitized_clone_url(row.get("base_url"))
        row["api_url"] = sanitized_clone_url(row.get("api_url"))
    elif dataset == "engine_versions":
        row["repository_url"] = sanitized_clone_url(row.get("repository_url"))
    elif dataset == "workers":
        row["token_hash"] = None
        row["token_expires_at"] = None
        row["session_id"] = None
        row["status"] = "offline"
    elif dataset == "benchmarkers":
        row["token_hash"] = None
        row["token_expires_at"] = None
        row["session_id"] = None
        row["status"] = "offline"
    elif dataset == "benchmark_jobs":
        row["benchmarker_id"] = None
        if "benchmark_job_output" not in selected:
            row["output"] = ""
    elif dataset == "tool_jobs":
        row["worker_id"] = None
    elif dataset == "deployment_targets":
        row["target_id"] = None
        row["repository_url"] = sanitized_clone_url(row.get("repository_url"))
    if dataset == "engine_versions" and "engine_dockerfiles" in selected and row.get("distribution") == "managed":
        row["dockerfile_path"] = f"cloned/{row['build_hash']}.Dockerfile"
    elif dataset == "tournaments" and row.get("status") in {"scheduled", "running", "paused"}:
        row["status"] = "paused"
    elif dataset == "events" and row.get("status") in {"announced", "scheduled", "live", "intermission", "postponed"}:
        row["status"] = "draft"
        row["published_at"] = None
    elif dataset == "games" and row.get("status") in {"assigned", "live"}:
        row["status"] = "abandoned"
        row["finished_at"] = terminal_at
    elif dataset == "game_assignments" and row.get("status") in {"assigned", "acked", "live"}:
        row["status"] = "abandoned"
        row["worker_id"] = None
        row["finished_at"] = terminal_at
    elif dataset == "benchmark_jobs" and row.get("status") in {"queued", "running"}:
        row["status"] = "failed"
        row["benchmarker_id"] = None
        row["finished_at"] = terminal_at
        row["error"] = "Cloned without active execution state"
    elif dataset == "runner_commands" and row.get("status") in {"pending", "claimed"}:
        row["status"] = "failed"
        row["finished_at"] = terminal_at
        row["error"] = "Cloned without active execution state"
    elif dataset == "deployment_jobs" and row.get("status") not in {"succeeded", "failed"}:
        row["status"] = "failed"
        row["finished_at"] = terminal_at
        row["error"] = "Cloned without active execution state"
    elif dataset == "deployment_targets" and row.get("status") not in {"succeeded", "deferred", "failed"}:
        row["status"] = "failed"
        row["detail"] = "Cloned without active execution state"
    elif dataset == "dockerfile_pull_jobs" and row.get("status") not in {"succeeded", "failed"}:
        row["status"] = "failed"
        row["finished_at"] = terminal_at
        row["error"] = "Cloned without active execution state"
    elif dataset == "tool_job_items" and row.get("status") in {"pending", "running"}:
        row["status"] = "failed"
        row["finished_at"] = terminal_at
        row["error"] = "Cloned without active execution state"
    elif dataset == "tournament_rating_list_commits":
        row["command_id"] = None
        if row.get("status") in {"pending", "claimed"}:
            row["status"] = "failed"
            row["applied_at"] = None
            row["error"] = "Cloned without active execution state"
    return row


def insert_clone_batch(connection, table: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    columns = tuple(rows[0])
    if any(tuple(row) != columns for row in rows):
        raise ValueError(f"Inconsistent columns in clone dataset {table}")
    if not re.fullmatch(r"[a-z_]+", table) or any(re.fullmatch(r"[a-z_]+", column) is None for column in columns):
        raise ValueError("Invalid clone dataset identifiers")
    placeholders = ", ".join("?" for _ in columns)
    cursor = connection.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
        (tuple(row[column] for column in columns) for row in rows),
    )
    inserted = max(int(cursor.rowcount), 0)
    if inserted == len(rows):
        return inserted
    primary_key_rows = connection.execute(
        """
        SELECT attribute.attname AS name
        FROM pg_index AS idx
        JOIN LATERAL unnest(idx.indkey) WITH ORDINALITY AS key_column(attnum, position) ON TRUE
        JOIN pg_attribute AS attribute
          ON attribute.attrelid = idx.indrelid
         AND attribute.attnum = key_column.attnum
        WHERE idx.indrelid = ?::regclass
          AND idx.indisprimary
        ORDER BY key_column.position
        """,
        (table,),
    ).fetchall()
    primary_keys = tuple(str(item["name"]) for item in primary_key_rows)
    if not primary_keys:
        raise CloneConflict(f"Clone dataset {table} does not have a primary key for safe conflict detection.")
    ignored = {"access_token"} if table == "git_hosts" else set()
    compared_columns = tuple(column for column in columns if column not in ignored)
    for row in rows:
        if any(key not in row for key in primary_keys):
            raise CloneConflict(f"Clone dataset {table} is missing a primary-key field.")
        where = " AND ".join(f"{key} = ?" for key in primary_keys)
        existing = connection.execute(
            f"SELECT {', '.join(compared_columns)} FROM {table} WHERE {where}",
            tuple(row[key] for key in primary_keys),
        ).fetchone()
        identity = ", ".join(f"{key}={row[key]}" for key in primary_keys)
        if existing is None:
            raise CloneConflict(f"Destination {table} has a unique-key conflict for source row {identity}.")
        expected = {column: row[column] for column in compared_columns}
        actual = {column: existing[column] for column in compared_columns}
        if comparable_row(actual) != comparable_row(expected):
            raise CloneConflict(f"Destination {table} row {identity} differs from the source clone row.")
    return inserted


def comparable_row(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def materialize_engine_dockerfile(job_id: int, row: dict[str, Any]) -> None:
    content = str(row.get("dockerfile") or "")
    relative = str(row.get("dockerfile_path") or "")
    if not content or not relative:
        return
    root = engine_dockerfile_root()
    destination = root.joinpath(*relative.split("/"))
    resolved = destination.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("Cloned Dockerfile path escaped the configured root") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.read_text(encoding="utf-8") == content:
        return
    temporary = destination.parent / f".{destination.name}.clone-{job_id}.part"
    with temporary.open("w", encoding="utf-8", newline="") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, destination)


def materialize_clone_dockerfiles(job_id: int, path: Path, selected: set[str]) -> None:
    with gzip.open(path, "rt", encoding="utf-8") as source:
        for line in source:
            if line.strip():
                materialize_engine_dockerfile(job_id, imported_row("engine_versions", json.loads(line), selected))


def reserve_clone_sequence(connection, table: str, source_max_id: int | None) -> None:
    if source_max_id is None:
        return
    if re.fullmatch(r"[a-z_]+", table) is None:
        raise ValueError("Invalid clone dataset table")
    connection.execute("BEGIN")
    connection.execute(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE")
    floor = connection.execute(
        f"SELECT GREATEST(COALESCE((SELECT MAX(id) FROM {table}), 1), nextval(pg_get_serial_sequence('{table}', 'id')), ?) AS value",
        (source_max_id,),
    ).fetchone()
    connection.execute(
        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), ?, TRUE)",
        (int(floor["value"]),),
    )
    connection.commit()


def materialize_clone_artifacts(job_id: int, artifacts: list[dict[str, Any]]) -> None:
    root = engine_artifact_root()
    staging_root = clone_transfer_root() / "imports" / str(job_id) / "artifacts"
    for artifact in artifacts:
        digest = str(artifact["artifact_sha256"])
        size = int(artifact["artifact_size"])
        staged = staging_root / f"{artifact['storage_key']}.tar.gz"
        destination = root / f"{artifact['storage_key']}.tar.gz"
        if staged.is_file():
            validate_artifact_archive(
                staged,
                expected_build_hash=str(artifact["build_hash"]),
                expected_entrypoint=str(artifact["entrypoint"]),
            )
            if destination.exists():
                if not destination.is_file() or destination.stat().st_size != size or sha256_file(destination) != digest:
                    raise CloneConflict(f"Destination artifact storage key conflicts with source artifact {digest}.")
                staged.unlink()
                continue
            os.replace(staged, destination)
            destination.chmod(0o600)
            continue
        if not destination.is_file() or destination.stat().st_size != size or sha256_file(destination) != digest:
            raise RuntimeError(f"Staged artifact is unavailable: {digest}")


def verify_clone_artifacts(database_url: str, job_id: int, artifacts: list[dict[str, Any]]) -> None:
    for artifact in artifacts:
        ensure_clone_active(database_url, job_id)
        path = engine_artifact_root() / f"{artifact['storage_key']}.tar.gz"
        if not path.is_file() or path.stat().st_size != int(artifact["artifact_size"]):
            raise RuntimeError(f"Imported artifact is unavailable: {artifact['artifact_sha256']}")
        if sha256_file(path) != artifact["artifact_sha256"]:
            raise RuntimeError(f"Imported artifact failed digest verification: {artifact['artifact_sha256']}")
        validate_artifact_archive(
            path,
            expected_build_hash=str(artifact["build_hash"]),
            expected_entrypoint=str(artifact["entrypoint"]),
        )


def normalize_source_url(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError("Enter the source Cope domain.")
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("The source must be an HTTPS domain without embedded credentials.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Enter only the source domain, without a path, query, or fragment.")
    port = parsed.port
    if port not in {None, 443} and os.environ.get("COPE_CLONE_ALLOW_PRIVATE_ORIGINS") != "1":
        raise ValueError("The source must use the standard HTTPS port.")
    validated_source_addresses(parsed.hostname, port or 443)
    host = parsed.hostname.lower()
    netloc_host = f"[{host}]" if ":" in host else host
    netloc = netloc_host if port in {None, 443} else f"{netloc_host}:{port}"
    return f"https://{netloc}"


def validated_source_addresses(hostname: str, port: int) -> tuple[str, ...]:
    allow_private = os.environ.get("COPE_CLONE_ALLOW_PRIVATE_ORIGINS") == "1"
    try:
        addresses = tuple(
            dict.fromkeys(
                item[4][0]
                for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            )
        )
    except OSError as exc:
        raise ValueError("The source domain could not be resolved.") from exc
    if not addresses:
        raise ValueError("The source domain could not be resolved.")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not allow_private and not ip.is_global:
            raise ValueError("The source domain resolves to a non-public address.")
    return addresses


def source_opener(*handlers) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        *handlers,
        PinnedHTTPSHandler(context=ssl.create_default_context()),
        NoRedirectHandler(),
    )


def authenticated_source_opener(source_url: str, admin_token: str) -> urllib.request.OpenerDirector:
    if not admin_token:
        raise ValueError("Enter the source admin token.")
    cookies = http.cookiejar.CookieJar()
    opener = source_opener(
        urllib.request.HTTPCookieProcessor(cookies),
    )
    payload = urllib.parse.urlencode({"token": admin_token}).encode("utf-8")
    request = urllib.request.Request(
        source_url + "/api/session",
        data=payload,
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with opener.open(request, timeout=30) as response:
            body = bounded_response_json(response)
    except urllib.error.HTTPError as exc:
        raise RemoteCloneError(remote_error_detail(exc)) from exc
    except RemoteCloneError:
        raise
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise RemoteCloneError("Could not authenticate with the source Cope environment.") from exc
    if not body.get("authenticated"):
        raise RemoteCloneError("The source rejected the admin token.")
    return opener


def remote_json(
    opener: urllib.request.OpenerDirector,
    source_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(source_url + path, data=body, method=method, headers=request_headers)
    try:
        with opener.open(request, timeout=60) as response:
            return bounded_response_json(response)
    except urllib.error.HTTPError as exc:
        raise RemoteCloneError(remote_error_detail(exc)) from exc
    except RemoteCloneError:
        raise
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise RemoteCloneError("The source returned an invalid clone response.") from exc


def direct_remote_json(
    source_url: str,
    path: str,
    *,
    headers: dict[str, str],
    method: str = "GET",
    missing_ok: bool = False,
) -> dict[str, Any]:
    source_url = normalize_source_url(source_url)
    request = urllib.request.Request(
        source_url + path,
        headers={"Accept": "application/json", **headers},
        method=method,
    )
    opener = source_opener()
    try:
        with opener.open(request, timeout=60) as response:
            return bounded_response_json(response)
    except urllib.error.HTTPError as exc:
        if missing_ok and exc.code in {401, 404, 410}:
            return {}
        raise RemoteCloneError(remote_error_detail(exc)) from exc
    except RemoteCloneError:
        raise
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise RemoteCloneError("The source clone export could not be read.") from exc


def bounded_response_json(response) -> dict[str, Any]:
    maximum = remote_json_max_bytes()
    declared = response.headers.get("Content-Length", "")
    if declared.isdigit() and int(declared) > maximum:
        raise RemoteCloneError("The source clone response exceeds the configured size limit.")
    raw = response.read(maximum + 1)
    if len(raw) > maximum:
        raise RemoteCloneError("The source clone response exceeds the configured size limit.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RemoteCloneError("The source returned an invalid clone response.") from exc
    if not isinstance(payload, dict):
        raise RemoteCloneError("The source returned an invalid clone response.")
    return payload


def remote_error_detail(error: urllib.error.HTTPError) -> str:
    try:
        maximum = remote_json_max_bytes()
        raw = error.read(maximum + 1)
        if len(raw) > maximum:
            return f"The source returned HTTP {error.code}."
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            return f"The source returned HTTP {error.code}."
        detail = payload.get("detail") or payload.get("error") or payload.get("message")
        if detail:
            return str(detail)
    except Exception:
        pass
    return f"The source returned HTTP {error.code}."


def download_remote_file(
    source_url: str,
    path: str,
    headers: dict[str, str],
    destination: Path,
    expected_size: int,
    expected_sha256: str,
    progress,
) -> None:
    source_url = normalize_source_url(source_url)
    if expected_size < 0 or expected_size > clone_transfer_max_bytes():
        raise RemoteCloneError("The source file exceeds the configured clone transfer size limit.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    if destination.is_file():
        if destination.stat().st_size == expected_size and sha256_file(destination) == expected_sha256:
            progress(expected_size)
            return
        destination.unlink()
    if temporary.is_file() and temporary.stat().st_size == expected_size:
        if sha256_file(temporary) == expected_sha256:
            progress(expected_size)
            os.replace(temporary, destination)
            return
        temporary.unlink()
    elif temporary.is_file() and temporary.stat().st_size > expected_size:
        temporary.unlink()
    offset = temporary.stat().st_size if temporary.exists() else 0
    if expected_size - offset > shutil.disk_usage(destination.parent).free:
        raise RemoteCloneError("There is not enough free disk space for the clone transfer.")
    request_headers = {**headers}
    if offset:
        request_headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(source_url + path, headers=request_headers)
    opener = source_opener()
    try:
        with opener.open(request, timeout=120) as response:
            append = offset > 0 and getattr(response, "status", 200) == 206
            if not append:
                offset = 0
            mode = "ab" if append else "wb"
            completed = offset
            last_report = completed
            with temporary.open(mode) as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    if completed + len(chunk) > expected_size:
                        raise RemoteCloneError("The source sent more data than declared in the clone manifest.")
                    output.write(chunk)
                    completed += len(chunk)
                    if completed - last_report >= 4 * 1024 * 1024:
                        progress(completed)
                        last_report = completed
                output.flush()
                os.fsync(output.fileno())
            progress(completed)
    except urllib.error.HTTPError as exc:
        raise RemoteCloneError(remote_error_detail(exc)) from exc
    except RemoteCloneError:
        raise
    except OSError as exc:
        raise RemoteCloneError("The source transfer was interrupted.") from exc
    if temporary.stat().st_size != expected_size:
        raise RemoteCloneError(f"Transferred file size mismatch: expected {expected_size}, received {temporary.stat().st_size}.")
    if sha256_file(temporary) != expected_sha256:
        raise RemoteCloneError("Transferred file SHA-256 verification failed.")
    os.replace(temporary, destination)


def load_clone_job(database_url: str, job_id: int) -> dict[str, Any]:
    connection = connect_database(database_url)
    try:
        row = connection.execute("SELECT * FROM environment_clone_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise RuntimeError("Clone job disappeared.")
        return dict(row)
    finally:
        connection.close()


def ensure_clone_active(database_url: str, job_id: int) -> None:
    row = load_clone_job(database_url, job_id)
    if int(row["cancel_requested"]):
        raise CloneCancelled()


def update_clone_from_source_status(database_url: str, job_id: int, status: dict[str, Any]) -> None:
    connection = connect_database(database_url)
    try:
        connection.execute(
            "UPDATE environment_clone_jobs SET total_datasets = ?, updated_at = ? WHERE id = ?",
            (int(status.get("total_datasets", 0)), utc_now(), job_id),
        )
        connection.commit()
    finally:
        connection.close()


def set_clone_phase(database_url: str, job_id: int, status: str, phase: str, message: str) -> None:
    connection = connect_database(database_url)
    try:
        connection.execute(
            "UPDATE environment_clone_jobs SET status = ?, phase = ?, updated_at = ? WHERE id = ?",
            (status, phase, utc_now(), job_id),
        )
        add_clone_event(connection, job_id, "info", phase, None, message)
        connection.commit()
    finally:
        connection.close()


def set_clone_step(database_url: str, job_id: int, key: str, status: str, **values: Any) -> None:
    allowed = {"completed_rows", "completed_bytes", "inserted_rows", "skipped_rows", "error", "started_at", "finished_at"}
    selected = [(name, value) for name, value in values.items() if name in allowed]
    connection = connect_database(database_url)
    try:
        assignments = ["status = ?", *[f"{name} = ?" for name, _ in selected]]
        connection.execute(
            f"UPDATE environment_clone_job_steps SET {', '.join(assignments)} WHERE job_id = ? AND dataset_name = ?",
            (status, *[value for _, value in selected], job_id, key),
        )
        counts = connection.execute(
            """
            SELECT COUNT(*) FILTER (WHERE status IN ('completed', 'skipped')) AS completed,
                   COALESCE(SUM(completed_rows), 0) AS rows
            FROM environment_clone_job_steps WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        connection.execute(
            "UPDATE environment_clone_jobs SET completed_datasets = ?, completed_rows = ?, updated_at = ? WHERE id = ?",
            (int(counts["completed"]), int(counts["rows"]), utc_now(), job_id),
        )
        connection.commit()
    finally:
        connection.close()


def update_clone_transfer_progress(database_url: str, job_id: int, key: str, completed: int) -> None:
    connection = connect_database(database_url)
    try:
        connection.execute(
            "UPDATE environment_clone_job_steps SET completed_bytes = ? WHERE job_id = ? AND dataset_name = ?",
            (completed, job_id, key),
        )
        value = connection.execute(
            "SELECT COALESCE(SUM(completed_bytes), 0) AS bytes FROM environment_clone_job_steps WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        connection.execute(
            "UPDATE environment_clone_jobs SET completed_bytes = ?, updated_at = ? WHERE id = ?",
            (int(value["bytes"]), utc_now(), job_id),
        )
        connection.commit()
    finally:
        connection.close()


def update_artifact_bytes(database_url: str, job_id: int, increment: int) -> None:
    if increment <= 0:
        return
    connection = connect_database(database_url)
    try:
        connection.execute(
            "UPDATE environment_clone_jobs SET completed_bytes = LEAST(total_bytes, completed_bytes + ?), updated_at = ? WHERE id = ?",
            (increment, utc_now(), job_id),
        )
        connection.commit()
    finally:
        connection.close()


def update_artifact_progress(database_url: str, job_id: int, *, skipped: bool, completed_bytes: int = 0) -> None:
    connection = connect_database(database_url)
    try:
        connection.execute(
            """
            UPDATE environment_clone_jobs
            SET artifacts_completed = artifacts_completed + 1,
                artifacts_skipped = artifacts_skipped + ?,
                completed_bytes = LEAST(total_bytes, completed_bytes + ?), updated_at = ?
            WHERE id = ?
            """,
            (int(skipped), completed_bytes, utc_now(), job_id),
        )
        connection.commit()
    finally:
        connection.close()


def update_clone_row_progress(database_url: str, job_id: int, key: str, completed: int) -> None:
    connection = connect_database(database_url)
    try:
        connection.execute(
            "UPDATE environment_clone_job_steps SET completed_rows = ? WHERE job_id = ? AND dataset_name = ?",
            (completed, job_id, key),
        )
        total = connection.execute(
            "SELECT COALESCE(SUM(completed_rows), 0) AS rows FROM environment_clone_job_steps WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        connection.execute(
            "UPDATE environment_clone_jobs SET completed_rows = ?, updated_at = ? WHERE id = ?",
            (int(total["rows"]), utc_now(), job_id),
        )
        connection.commit()
    finally:
        connection.close()


def add_clone_log(database_url: str, job_id: int, level: str, phase: str, dataset: str | None, message: str) -> None:
    connection = connect_database(database_url)
    try:
        add_clone_event(connection, job_id, level, phase, dataset, message)
        connection.commit()
    finally:
        connection.close()


def set_clone_terminal(database_url: str, job_id: int, status: str, error: str, conflict: bool = False) -> None:
    connection = connect_database(database_url)
    try:
        connection.execute(
            """
            UPDATE environment_clone_jobs
            SET status = ?, phase = ?, error = ?, updated_at = ?, finished_at = ?,
                conflicts = conflicts + ?
            WHERE id = ?
            """,
            (status, status, error[-8000:], utc_now(), utc_now(), 1 if conflict else 0, job_id),
        )
        add_clone_event(connection, job_id, "warning" if status == "cancelled" else "error", status, None, error[-8000:])
        connection.commit()
    finally:
        connection.close()


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
