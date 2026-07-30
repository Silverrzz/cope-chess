from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from cope.db import (
    DEFAULT_DB_PATH,
    claim_deployment_job,
    connect_database,
    fail_interrupted_deployment_jobs,
    get_worker,
    list_deployment_targets,
    list_service_heartbeats,
    set_deployment_target_commit,
    touch_service_heartbeat,
    update_deployment_job_status,
    update_deployment_target_status,
    update_server_deployment_target,
)
from cope.version import app_version


LOG = logging.getLogger("cope.updater")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+-]{0,199}$")


@dataclass(frozen=True, slots=True)
class UpdaterConfig:
    db_path: str | Path = DEFAULT_DB_PATH
    source_dir: Path = Path("/workspace")
    repository_url: str = ""
    default_ref: str = "main"
    compose_project: str = ""
    poll_interval_s: float = 2.0
    worker_wait_s: float = 1800.0
    service_wait_s: float = 180.0
    allow_rollback: bool = False


def run_updater(config: UpdaterConfig) -> None:
    source_dir = config.source_dir.expanduser().resolve()
    _run(["git", "config", "--global", "--add", "safe.directory", str(source_dir)])
    LOG.info("deployment updater started source=%s version=%s", source_dir, app_version())
    connection = connect_database(config.db_path)
    try:
        interrupted = fail_interrupted_deployment_jobs(connection)
        touch_service_heartbeat(connection, "updater", app_version())
        connection.commit()
    finally:
        connection.close()
    if interrupted:
        LOG.warning("marked interrupted deployments failed jobs=%s", interrupted)
    threading.Thread(
        target=_heartbeat_loop,
        args=(config,),
        name="updater-heartbeat",
        daemon=True,
    ).start()
    while True:
        connection = connect_database(config.db_path)
        try:
            touch_service_heartbeat(connection, "updater", app_version())
            job = claim_deployment_job(connection)
            connection.commit()
        finally:
            connection.close()
        if job is None:
            time.sleep(max(config.poll_interval_s, 0.5))
            continue
        _run_deployment(config, source_dir, job.id, job.requested_ref)


def _heartbeat_loop(config: UpdaterConfig) -> None:
    while True:
        try:
            connection = connect_database(config.db_path)
            try:
                touch_service_heartbeat(connection, "updater", app_version())
                connection.commit()
            finally:
                connection.close()
        except Exception:
            LOG.exception("updater heartbeat failed")
        time.sleep(5.0)


def _run_deployment(
    config: UpdaterConfig,
    source_dir: Path,
    job_id: int,
    requested_ref: str,
) -> None:
    original_commit = ""
    rollback_tag = f"cope-chess:rollback-{job_id}"
    built = False
    restarted = False
    source_changed = False
    try:
        repository_url = config.repository_url or _git_output(
            source_dir,
            "remote",
            "get-url",
            "origin",
        )
        _validate_source_repository(source_dir, repository_url)
        _compose_project_name(config.compose_project)
        repository_identity = _normalise_repository_url(repository_url)
        original_commit = _git_output(source_dir, "rev-parse", "HEAD")
        target_commit = _resolve_target_commit(
            source_dir,
            requested_ref or config.default_ref,
        )
        if target_commit != original_commit and not config.allow_rollback:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(source_dir),
                    "merge-base",
                    "--is-ancestor",
                    original_commit,
                    target_commit,
                ],
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError("target commit is not a fast-forward from the deployed source")
        _set_target_commit(config, job_id, target_commit, repository_identity)
        _set_job_status(config, job_id, "building")
        _set_server_target(config, job_id, "updating", current_commit=original_commit)
        _run(["docker", "image", "inspect", "cope-chess:local"], check=True)
        _run(["docker", "tag", "cope-chess:local", rollback_tag])
        _run(["git", "-C", str(source_dir), "checkout", "--detach", target_commit])
        source_changed = target_commit != original_commit
        _validate_compose_inputs(source_dir)
        _compose(
            config,
            source_dir,
            "build",
            "--build-arg",
            f"COPE_BUILD_VERSION={target_commit}",
        )
        built = True
        _set_job_status(config, job_id, "migrating")
        _compose(config, source_dir, "run", "--rm", "migrate")
        _set_job_status(config, job_id, "updating_workers")
        _prepare_worker_targets(config, job_id)
        _wait_for_workers(config, job_id)
        _set_job_status(config, job_id, "restarting")
        _set_server_target(config, job_id, "restarting")
        restart_started_at = datetime.now(UTC)
        _compose(
            config,
            source_dir,
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "web",
            "scheduler",
            "worker-server",
            "benchmark-server",
            "caddy",
        )
        restarted = True
        _set_job_status(config, job_id, "verifying")
        _wait_for_services(config, target_commit, restart_started_at)
        _wait_for_worker_reconnections(config, job_id)
        _set_server_target(
            config,
            job_id,
            "succeeded",
            current_commit=target_commit,
        )
        _set_job_status(config, job_id, "succeeded")
        _run(["docker", "image", "rm", rollback_tag], check=False)
        LOG.info("deployment completed job_id=%s commit=%s", job_id, target_commit)
        _compose(
            config,
            source_dir,
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "updater",
            check=False,
        )
    except Exception as error:
        detail = (str(error).strip() or error.__class__.__name__)[:4000]
        LOG.exception("deployment failed job_id=%s", job_id)
        rollback_detail = ""
        if source_changed and original_commit:
            try:
                _run(["git", "-C", str(source_dir), "checkout", "--detach", original_commit])
            except Exception as source_error:
                rollback_detail = f" Source rollback failed: {source_error}"
        if built or restarted:
            try:
                _run(["docker", "tag", rollback_tag, "cope-chess:local"])
                if restarted:
                    _compose(
                        config,
                        source_dir,
                        "up",
                        "-d",
                        "--no-deps",
                        "--force-recreate",
                        "web",
                        "scheduler",
                        "worker-server",
                        "benchmark-server",
                        "caddy",
                    )
                rollback_detail += " Previous server image restored."
            except Exception as rollback_error:
                rollback_detail += f" Automatic rollback failed: {rollback_error}"
                LOG.exception("deployment rollback failed job_id=%s", job_id)
        _set_server_target(
            config,
            job_id,
            "failed",
            current_commit=original_commit or None,
            detail=detail + rollback_detail,
        )
        _set_job_status(config, job_id, "failed", error=detail + rollback_detail)


def _validate_source_repository(source_dir: Path, repository_url: str) -> None:
    if not (source_dir / ".git").exists():
        raise RuntimeError(f"deployment source is not a Git checkout: {source_dir}")
    configured = _git_output(source_dir, "remote", "get-url", "origin")
    if _normalise_repository_url(configured) != _normalise_repository_url(repository_url):
        raise RuntimeError("deployment source origin does not match COPE_UPDATE_REPOSITORY_URL")
    dirty = _run(
        [
            "git",
            "-C",
            str(source_dir),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        capture=True,
    )
    if dirty:
        raise RuntimeError("deployment source has tracked local changes")


def _resolve_target_commit(source_dir: Path, requested_ref: str) -> str:
    if REF_PATTERN.fullmatch(requested_ref) is None:
        raise ValueError("requested Git ref contains unsupported characters")
    _run(["git", "-C", str(source_dir), "fetch", "--prune", "origin"])
    candidates = [requested_ref]
    if not requested_ref.startswith(("refs/", "origin/")) and COMMIT_PATTERN.fullmatch(requested_ref) is None:
        candidates.insert(0, f"origin/{requested_ref}")
    for candidate in candidates:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(source_dir),
                "rev-parse",
                "--verify",
                f"{candidate}^{{commit}}",
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        commit = result.stdout.strip().lower()
        if result.returncode == 0 and COMMIT_PATTERN.fullmatch(commit):
            return commit
    raise RuntimeError(f"could not resolve deployment ref {requested_ref!r}")


def _prepare_worker_targets(config: UpdaterConfig, job_id: int) -> None:
    connection = connect_database(config.db_path)
    try:
        for target in list_deployment_targets(connection, job_id):
            if target.target_kind != "worker" or target.target_id is None:
                continue
            worker = get_worker(connection, target.target_id)
            if worker is None or worker.status == "revoked":
                update_deployment_target_status(
                    connection,
                    target.id,
                    "deferred",
                    detail="Worker is unavailable and will update on its next connection.",
                )
            elif worker.status not in {"connected", "downloading", "ready", "busy"}:
                update_deployment_target_status(
                    connection,
                    target.id,
                    "deferred",
                    current_commit=worker.app_commit,
                    detail="Worker is offline and will update before accepting new work.",
                )
        connection.commit()
    finally:
        connection.close()


def _wait_for_workers(config: UpdaterConfig, job_id: int) -> None:
    deadline = time.monotonic() + max(config.worker_wait_s, 1.0)
    while True:
        connection = connect_database(config.db_path)
        try:
            targets = [
                target
                for target in list_deployment_targets(connection, job_id)
                if target.target_kind == "worker"
            ]
            failed = [target for target in targets if target.status == "failed"]
            active = [
                target
                for target in targets
                if target.status in {"pending", "waiting", "updating"}
            ]
            if failed:
                labels = ", ".join(target.label for target in failed)
                raise RuntimeError(f"worker update failed: {labels}")
            if not active:
                return
            if time.monotonic() >= deadline:
                blocked: list[str] = []
                for target in active:
                    worker = (
                        get_worker(connection, target.target_id)
                        if target.target_id is not None
                        else None
                    )
                    if worker is None or worker.status not in {
                        "connected",
                        "downloading",
                        "ready",
                        "busy",
                    }:
                        update_deployment_target_status(
                            connection,
                            target.id,
                            "deferred",
                            current_commit=None if worker is None else worker.app_commit,
                            detail="Worker is offline and will update before accepting new work.",
                        )
                    else:
                        blocked.append(target.label)
                        update_deployment_target_status(
                            connection,
                            target.id,
                            "failed",
                            current_commit=worker.app_commit,
                            detail="Worker did not drain and update before the deployment timeout.",
                        )
                connection.commit()
                if blocked:
                    raise RuntimeError(
                        f"workers did not drain before the update timeout: {', '.join(blocked)}"
                    )
                return
        finally:
            connection.close()
        time.sleep(2.0)


def _wait_for_services(
    config: UpdaterConfig,
    target_commit: str,
    restart_started_at: datetime,
) -> None:
    expected = {"web", "scheduler", "worker-server", "benchmark-server"}
    deadline = time.monotonic() + max(config.service_wait_s, 1.0)
    while time.monotonic() < deadline:
        connection = connect_database(config.db_path)
        try:
            heartbeats = {
                item["service"]: item
                for item in list_service_heartbeats(connection)
            }
        finally:
            connection.close()
        if all(
            _heartbeat_ready(
                heartbeats.get(service),
                target_commit,
                restart_started_at,
            )
            for service in expected
        ):
            return
        time.sleep(2.0)
    missing = sorted(
        service
        for service in expected
        if not _heartbeat_ready(
            heartbeats.get(service),
            target_commit,
            restart_started_at,
        )
    )
    raise RuntimeError(f"updated services did not become ready: {', '.join(missing)}")


def _wait_for_worker_reconnections(config: UpdaterConfig, job_id: int) -> None:
    deadline = time.monotonic() + max(config.service_wait_s, 1.0)
    while True:
        connection = connect_database(config.db_path)
        try:
            active = [
                target
                for target in list_deployment_targets(connection, job_id)
                if target.target_kind == "worker"
                and target.status in {"pending", "waiting", "updating", "restarting"}
            ]
            if not active:
                return
            if time.monotonic() >= deadline:
                for target in active:
                    update_deployment_target_status(
                        connection,
                        target.id,
                        "deferred",
                        detail="Worker release is installed and will reconcile on reconnect.",
                    )
                connection.commit()
                return
        finally:
            connection.close()
        time.sleep(2.0)


def _heartbeat_ready(
    heartbeat: dict[str, str] | None,
    target_commit: str,
    restart_started_at: datetime,
) -> bool:
    if heartbeat is None or heartbeat.get("app_version") != target_commit:
        return False
    try:
        last_seen = datetime.fromisoformat(heartbeat["last_seen"])
    except (KeyError, ValueError):
        return False
    return last_seen > restart_started_at


def _set_target_commit(
    config: UpdaterConfig,
    job_id: int,
    target_commit: str,
    repository_url: str,
) -> None:
    connection = connect_database(config.db_path)
    try:
        set_deployment_target_commit(
            connection,
            job_id,
            target_commit,
            repository_url,
        )
        connection.commit()
    finally:
        connection.close()


def _set_job_status(
    config: UpdaterConfig,
    job_id: int,
    status: str,
    *,
    error: str | None = None,
) -> None:
    connection = connect_database(config.db_path)
    try:
        update_deployment_job_status(connection, job_id, status, error=error)
        touch_service_heartbeat(connection, "updater", app_version())
        connection.commit()
    finally:
        connection.close()


def _set_server_target(
    config: UpdaterConfig,
    job_id: int,
    status: str,
    *,
    current_commit: str | None = None,
    detail: str = "",
) -> None:
    connection = connect_database(config.db_path)
    try:
        update_server_deployment_target(
            connection,
            job_id,
            status,
            current_commit=current_commit,
            detail=detail,
        )
        connection.commit()
    finally:
        connection.close()


def _compose(
    config: UpdaterConfig,
    source_dir: Path,
    *arguments: str,
    check: bool = True,
) -> str:
    environment = os.environ.copy()
    environment["COPE_HOST_SOURCE_DIR"] = _host_source_dir(source_dir)
    project_name = _compose_project_name(config.compose_project)
    compose_command = (
        ["docker-compose"]
        if shutil.which("docker-compose")
        else ["docker", "compose"]
    )
    return _run(
        [
            *compose_command,
            "--project-name",
            project_name,
            "--project-directory",
            str(source_dir),
            "--file",
            str(source_dir / "compose.yaml"),
            *arguments,
        ],
        check=check,
        capture=True,
        environment=environment,
    )


def _compose_project_name(configured: str = "") -> str:
    container_id = os.environ.get("HOSTNAME", "").strip()
    if container_id:
        project = _run(
            [
                "docker",
                "inspect",
                container_id,
                "--format",
                '{{index .Config.Labels "com.docker.compose.project"}}',
            ],
            check=False,
            capture=True,
        )
        if project:
            service = _run(
                [
                    "docker",
                    "inspect",
                    container_id,
                    "--format",
                    '{{index .Config.Labels "com.docker.compose.service"}}',
                ],
                check=False,
                capture=True,
            )
            if service != "updater":
                raise RuntimeError(
                    f"deployment coordinator is running as Compose service {service!r}, expected 'updater'"
                )
            if configured and configured != project:
                LOG.warning(
                    "ignoring mismatched COPE_COMPOSE_PROJECT configured=%s actual=%s",
                    configured,
                    project,
                )
            return project
    if configured:
        return configured
    raise RuntimeError("could not determine the active Compose project")


def _validate_compose_inputs(source_dir: Path) -> None:
    compose_file = source_dir / "compose.yaml"
    if not compose_file.is_file():
        raise RuntimeError(f"deployment Compose file is missing: {compose_file}")
    missing = [
        str(path.relative_to(source_dir))
        for path in (
            source_dir / "secrets" / "admin_token",
            source_dir / "secrets" / "event_token",
            source_dir / "secrets" / "db_password",
        )
        if not path.is_file() or path.stat().st_size == 0
    ]
    if missing:
        raise RuntimeError(
            "deployment secret files are missing or empty: " + ", ".join(missing)
        )


def _host_source_dir(source_dir: Path) -> str:
    container_id = os.environ.get("HOSTNAME", "").strip()
    if not container_id:
        return str(source_dir)
    source = _run(
        [
            "docker",
            "inspect",
            container_id,
            "--format",
            '{{range .Mounts}}{{if eq .Destination "/workspace"}}{{.Source}}{{end}}{{end}}',
        ],
        check=False,
        capture=True,
    )
    return source or str(source_dir)


def _git_output(repository: Path, *arguments: str) -> str:
    return _run(
        ["git", "-C", str(repository), *arguments],
        capture=True,
    ).strip()


def _normalise_repository_url(value: str) -> str:
    cleaned = value.strip().rstrip("/").removesuffix(".git")
    if "://" in cleaned:
        parsed = urlsplit(cleaned)
        host = parsed.hostname or ""
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        return f"{host}/{parsed.path.lstrip('/')}".lower()
    if ":" in cleaned and not cleaned.startswith(("/", ".")):
        authority, path = cleaned.split(":", 1)
        host = authority.rsplit("@", 1)[-1]
        return f"{host}/{path.lstrip('/')}".lower()
    return cleaned.lower()


def _run(
    command: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    environment: dict[str, str] | None = None,
) -> str:
    LOG.info("running command=%s", " ".join(command))
    result = subprocess.run(
        command,
        capture_output=capture,
        check=False,
        text=True,
        env=environment,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"{command[0]} exited with status {result.returncode}: {detail[-3000:]}"
        )
    return (result.stdout or "").strip()
