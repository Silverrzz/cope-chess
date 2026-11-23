from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

from cope.version import app_version

from .containers import ContainerWorker
from .local_state import (
    WorkerSettings,
    default_home,
    is_running,
    locked,
    parse_boolean,
    parse_memory,
    parse_threads,
    read_json,
    request_stop,
    write_json,
)
from .supervisor import release_python, supervise


COMMANDS = {"help", "start", "stop", "config", "health", "update"}

WORKER_HELP = """\
Manage the local Cope worker.

  cope start                      Start the worker and wait for it to connect.
  cope stop                       Begin a safe shutdown immediately.
  cope stop grace                 Finish active work, then shut down cleanly.
  cope stop force                 Kill the worker and its engine processes.
  cope config                     Show worker settings.
  cope config --threads 8         Set the CPU thread limit.
                                  Use auto for available physical cores.
  cope config --memory 12GiB      Set the worker memory limit.
  cope config --auto-update true  Enable automatic updates (the default).
                                  Use false to disable them. The worker stalls
                                  if Cope requires an update.
  cope health status              Show worker health and current activity.
  cope health doctor              Repair common local issues while stopped
                                  and report any problems it finds.
  cope update                     Install the available worker update.
  cope update -i                  Show installed and available versions
                                  and the status of any pending update.

Set multiple limits with cope config --threads 8 --memory 12GiB.
Use cope <command> --help to see all options for a command.
"""


def register_commands(subparsers) -> None:
    help_parser = subparsers.add_parser(
        "help",
        prog="cope help",
        help="show the worker command reference",
        description=WORKER_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    help_parser.set_defaults(worker_help=help_parser)

    start = subparsers.add_parser("start", help="start the local game worker")
    start.add_argument("--foreground", action="store_true")
    start.add_argument("--boot", action="store_true", help=argparse.SUPPRESS)
    start.add_argument("--timeout", type=int, default=25)

    stop = subparsers.add_parser("stop", help="stop the local game worker")
    stop.add_argument("mode", nargs="?", choices=("grace", "force"))
    stop.add_argument("--timeout", type=int)

    config = subparsers.add_parser("config", help="view or change local worker settings")
    config.add_argument(
        "--threads", default=argparse.SUPPRESS, type=parse_threads,
        help="set the CPU thread limit or use auto for available physical cores",
    )
    config.add_argument("--memory", dest="memory_mb", default=argparse.SUPPRESS, type=parse_memory)
    config.add_argument("--auto-update", dest="auto_update", default=argparse.SUPPRESS, type=parse_boolean)
    config.add_argument("--server", default=argparse.SUPPRESS)
    config.add_argument("--token-file", default=argparse.SUPPRESS, metavar="FILE")
    config.add_argument("--compose-file", default=argparse.SUPPRESS, metavar="FILE")
    config.add_argument("--output", choices=("text", "json"), default="text")

    health = subparsers.add_parser("health", help="check the local worker")
    health.add_argument("action", choices=("status", "doctor"))
    health.add_argument("--output", choices=("text", "json"), default="text")
    health.add_argument("--ready", action="store_true", help="exit nonzero unless the worker can run work")

    update = subparsers.add_parser("update", help="install or inspect a Cope worker release")
    update.add_argument("-i", "--info", action="store_true")
    update.add_argument("--output", choices=("text", "json"), default="text")


def _print(data: dict, output: str = "text") -> None:
    if output == "json":
        print(json.dumps(data, sort_keys=True))
        return
    for key, value in data.items():
        if value is not None:
            print(f"{key.replace('_', ' ').capitalize():<20} {value}")


def _deadline(seconds: int | None) -> float:
    if seconds is not None and seconds <= 0:
        raise ValueError("timeout must be positive")
    return time.monotonic() + seconds if seconds is not None else float("inf")


def _wait_stop(home: Path, mode: str, seconds: int | None = None) -> int:
    deadline = _deadline(seconds)
    if not is_running(home):
        previous = read_json(home / "status.json")
        if previous.get("state") in {"starting", "connecting", "reconnecting", "ready", "busy", "draining", "stopping", "updating"}:
            raise RuntimeError("the supervisor is unavailable and process cleanup could not be verified")
        with locked(home / "control.lock"):
            if not is_running(home):
                write_json(home / "desired.json", {"state": "stopped"})
                return 0
    request_stop(home, mode)
    while is_running(home):
        if mode == "grace" and read_json(home / "status.json").get("state") == "drain_unavailable":
            raise RuntimeError("Cope does not support graceful drain for this connection. Use cope stop.")
        if time.monotonic() >= deadline:
            raise TimeoutError("worker is still draining" if mode == "grace" else "worker is still stopping")
        time.sleep(0.1)
    if read_json(home / "status.json").get("processes") != 0:
        raise RuntimeError("worker process cleanup could not be verified")
    return 0


def _start(home: Path, seconds: int) -> int:
    deadline = _deadline(seconds)
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    with locked(home / "launch.lock"):
        launched = None
        launch_id = None
        if not is_running(home):
            settings = WorkerSettings.read(home)
            if not settings.credential_file(home).is_file() and not (home / "worker.json").is_file():
                raise ValueError("set a token file with cope config --token-file FILE before starting the worker")
            launch_id = uuid.uuid4().hex
            environment = {**os.environ, "COPE_WORKER_HOME": str(home), "COPE_WORKER_LAUNCH_ID": launch_id}
            with (home / "worker.log").open("ab") as log:
                launched = subprocess.Popen(
                    [sys.executable, "-m", "cope", "start", "--foreground"],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    env=environment, cwd=home, start_new_session=True,
                )
        while time.monotonic() < deadline:
            status = read_json(home / "status.json")
            if is_running(home) and (launch_id is None or status.get("launch_id") == launch_id):
                if status.get("state") in {"ready", "busy"}:
                    _print(status)
                    return 0
                if status.get("state") in {"stalled", "blocked"}:
                    _print(status)
                    return 2
            if launched is not None and launched.poll() is not None:
                break
            time.sleep(0.15)
        if launched is not None and launched.poll() is None:
            _wait_stop(home, "safe", 20)
        status = read_json(home / "status.json")
        raise RuntimeError(status.get("detail") or f"worker did not connect. See {home / 'worker.log'}")


def _settings(home: Path, args) -> int:
    changes = {key: getattr(args, key) for key in asdict(WorkerSettings()) if hasattr(args, key)}
    with locked(home / "config.lock"):
        settings = WorkerSettings.read(home)
        if changes:
            if "compose_file" in changes:
                if is_running(home):
                    raise ValueError("stop the native worker before choosing a Compose backend")
                if settings.compose_file is not None and ContainerWorker(settings, home).running():
                    raise ValueError("stop the container before changing its Compose file")
                compose_file = Path(changes["compose_file"]).expanduser().resolve()
                if not compose_file.is_file():
                    raise ValueError(f"Compose file does not exist: {compose_file}")
                changes["compose_file"] = str(compose_file)
            if "server" in changes:
                split = urlsplit(changes["server"])
                if split.scheme == "https":
                    split = split._replace(scheme="wss")
                if split.scheme not in {"wss", "ws"} or not split.hostname or split.username or split.query or split.fragment:
                    raise ValueError("server must be a worker WebSocket URL")
                path = split.path if split.path not in {"", "/"} else "/worker"
                changes["server"] = urlunsplit(split._replace(path=path))
            if "token_file" in changes:
                token_path = Path(changes["token_file"]).expanduser().resolve()
                if not token_path.is_file():
                    raise ValueError(f"token file does not exist: {token_path}")
                changes["token_file"] = str(token_path)
            settings = replace(settings, **changes)
            settings.write(home)
    data = asdict(settings)
    if args.output == "text":
        data["threads"] = settings.threads or "auto"
        data["memory"] = f"{data.pop('memory_mb')} MiB" if settings.memory_mb else "auto"
        data.pop("memory_mb", None)
    if changes and settings.compose_file is not None:
        if ContainerWorker(settings, home).live_config(changes):
            data["application"] = "Saved and sent to the local container."
    elif changes and is_running(home):
        data["application"] = "Saved. Check cope health status for effective settings."
    _print(data, args.output)
    return 0


def fetch_release(server: str) -> dict:
    worker = urlsplit(server)
    if worker.scheme not in {"ws", "wss"} or not worker.hostname:
        raise ValueError("invalid worker server URL")
    scheme = "https" if worker.scheme == "wss" else "http"
    url = urlunsplit((scheme, worker.netloc, "/api/worker-release", "", ""))
    with urlopen(url, timeout=5) as response:
        release = json.loads(response.read(65536))
    if not isinstance(release, dict):
        raise ValueError("Cope returned invalid worker release metadata")
    return release


def _status(home: Path, *, doctor: bool) -> dict:
    running = is_running(home)
    status = read_json(home / "status.json")
    status.setdefault("state", "starting" if running else "stopped")
    status["supervisor"] = "running" if running else "stopped"
    status.pop("launch_id", None)
    if not doctor:
        return status
    repaired = []
    issues = []
    settings = WorkerSettings.read(home)
    if not running:
        with locked(home / "supervisor.lock", wait=False):
            if (home / "stop.json").is_file():
                (home / "stop.json").unlink()
                repaired.append("removed stale stop request")
            cache = home / "engines"
            if not cache.is_dir():
                cache.mkdir(mode=0o700, parents=True)
                repaired.append(f"created {cache}")
            if not (home / "config.json").exists():
                settings.write(home)
                repaired.append("created default worker settings")
            token_file = home / "token"
            if token_file.is_file() and not token_file.is_symlink() and token_file.stat().st_mode & 0o077:
                token_file.chmod(0o600)
                repaired.append("restricted permissions on the local token")
    if not settings.credential_file(home).is_file() and not (home / "worker.json").is_file():
        issues.append("missing credentials. Run cope config --token-file FILE")
    try:
        fetch_release(settings.server)
    except (OSError, ValueError) as error:
        issues.append(f"release service unavailable: {error}")
    if not running and status["state"] not in {"stopped", "failed", "stalled", "blocked"}:
        issues.append("worker supervisor exited without verifying cleanup")
    status.update(repaired=repaired, issues=issues)
    return status


def _installed_release(home: Path) -> str:
    executable = release_python(home)
    if executable is None:
        return app_version()
    return (executable.parents[2] / "source" / "cope" / "BUILD_VERSION").read_text(encoding="utf-8").strip()


def _update(home: Path, args) -> int:
    settings = WorkerSettings.read(home)
    installed = _installed_release(home)
    try:
        release = fetch_release(settings.server)
    except (OSError, ValueError) as error:
        if not args.info:
            raise
        _print({"installed": installed, "auto_update": settings.auto_update,
                "state": _status(home, doctor=False)["state"], "detail": str(error)}, args.output)
        return 1
    data = {"installed": installed, "available": release.get("commit"), "protocol": release.get("protocol"), "auto_update": settings.auto_update}
    if args.info:
        status = _status(home, doctor=False)
        request = read_json(home / "update.json")
        data.update(
            state=status["state"],
            target=request.get("target_commit") or status.get("target") or status.get("required_version"),
            detail=status.get("detail"),
        )
        _print(data, args.output)
        return 0
    target = release.get("commit")
    repository = release.get("repository_url")
    if not target or not repository:
        raise ValueError("Cope has no installable worker release")
    if target == installed:
        _print({"state": "up to date", "installed": installed}, args.output)
        return 0
    from .update import install_worker_release

    if is_running(home):
        return _request_running_update(home, target, repository, args.output)
    resume = (
        read_json(home / "desired.json").get("state") == "running"
        and read_json(home / "status.json").get("state") == "stalled"
    )
    os.environ.setdefault("COPE_UPDATE_ROOT", str(home / "releases"))
    with locked(home / "update.lock"):
        try:
            install_worker_release(target_commit=target, repository_url=repository)
        except Exception:
            if resume:
                _start(home, 25)
            raise
    _print({"state": "updated", "installed": target}, args.output)
    return _start(home, 25) if resume else 0


def _request_running_update(home: Path, target: str, repository: str, output: str) -> int:
    request = {"target_commit": target, "repository_url": repository}
    with locked(home / "control.lock"):
        existing = read_json(home / "update.json")
        if existing and existing != request:
            raise ValueError("a different worker update is already pending")
        if not existing:
            (home / "update-result.json").unlink(missing_ok=True)
        write_json(home / "update.json", request)
    try:
        while is_running(home):
            status = read_json(home / "status.json")
            result = read_json(home / "update-result.json")
            if result.get("target") == target and result.get("error"):
                raise RuntimeError(result["error"])
            if status.get("version") == target and status.get("state") in {"ready", "busy", "blocked"}:
                _print({"state": "updated", "installed": target}, output)
                return 0
            if status.get("state") == "update_unavailable":
                with locked(home / "control.lock"):
                    if read_json(home / "update.json") == request:
                        (home / "update.json").unlink(missing_ok=True)
                raise RuntimeError(status.get("detail") or "worker update failed")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("Update is still queued. Check cope update -i.", file=sys.stderr)
        return 130
    raise RuntimeError("worker exited before it could confirm the update")


def run_command(args) -> int:
    if args.role == "help":
        args.worker_help.print_help()
        return 0

    home = default_home()
    try:
        settings = WorkerSettings.read(home)
        if args.role != "config" and settings.compose_file is not None:
            return ContainerWorker(settings, home).run(args)
        if args.role == "start" and args.foreground:
            return supervise(home, boot=getattr(args, "boot", False))
        if args.role == "start":
            return _start(home, args.timeout)
        if args.role == "stop":
            timeout = args.timeout if args.timeout is not None else (None if args.mode == "grace" else 25)
            result = _wait_stop(home, args.mode or "safe", timeout)
            print("Worker stopped.")
            return result
        if args.role == "config":
            return _settings(home, args)
        if args.role == "health":
            status = _status(home, doctor=args.action == "doctor")
            _print(status, args.output)
            if args.ready:
                return int(status["supervisor"] != "running" or status["state"] not in {"ready", "busy"})
            return int(bool(status.get("issues")))
        return _update(home, args)
    except (OSError, TypeError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"cope: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
