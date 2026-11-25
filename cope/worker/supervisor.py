from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack
from pathlib import Path

from .local_state import WorkerSettings, locked, read_json, request_stop, write_json


def release_python(home: Path) -> Path | None:
    root = Path(os.environ.get("COPE_UPDATE_ROOT") or home / "releases")
    executable = root / "current" / "venv" / "bin" / "python"
    return executable if executable.is_file() else None


def _child_environment(home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.setdefault("COPE_UPDATE_ROOT", str(home / "releases"))
    environment.setdefault("COPE_WORKER_ENGINE_DIR", str(home / "engines"))
    environment["PYTHONUNBUFFERED"] = "1"
    if release_python(home) is not None:
        environment.pop("COPE_BUILD_VERSION", None)
    return environment


def _enable_subreaper() -> None:
    if sys.platform != "linux" or not hasattr(os, "waitid"):
        raise RuntimeError("the worker supervisor requires Linux process supervision")
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "could not take ownership of worker descendants")


def _kill_adopted_children() -> None:
    children_path = Path(f"/proc/self/task/{os.getpid()}/children")
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid:
            continue
        for child in children_path.read_text().split():
            child_pid = int(child)
            try:
                os.waitid(os.P_PID, child_pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                os.kill(child_pid, signal.SIGKILL)
            except (ChildProcessError, ProcessLookupError):
                continue
        time.sleep(0.05)


def cleanup_orphaned_worker(launch_id: str, *, timeout: float = 10) -> int:
    import psutil

    if sys.platform != "linux":
        raise RuntimeError("worker cleanup requires Linux process supervision")
    if not isinstance(launch_id, str) or not launch_id:
        raise RuntimeError("the worker launch ID is missing; process ownership cannot be verified")
    deadline = time.monotonic() + timeout
    tracked = set()
    while True:
        matching = set()
        for process in psutil.process_iter():
            if process.pid == os.getpid():
                continue
            try:
                if process.status() == psutil.STATUS_ZOMBIE:
                    continue
                if os.geteuid() != 0 and process.uids().real != os.geteuid():
                    continue
                if process.environ().get("COPE_WORKER_LAUNCH_ID") == launch_id:
                    matching.add(process)
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied as error:
                raise RuntimeError(f"cannot verify worker process ownership for PID {process.pid}") from error
        pending = list(matching)
        found = set()
        alive = False
        try:
            while pending:
                process = pending.pop()
                if process in found:
                    continue
                found.add(process)
                try:
                    process.suspend()
                    tracked.add(process)
                    pending.extend(process.children())
                except psutil.NoSuchProcess:
                    continue
                except psutil.AccessDenied as error:
                    raise RuntimeError(f"cannot stop orphaned worker PID {process.pid}") from error
                if time.monotonic() >= deadline:
                    raise RuntimeError("orphaned worker process discovery exceeded the cleanup timeout")
        finally:
            for process in tracked:
                try:
                    if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                        process.kill()
                        alive = True
                except psutil.NoSuchProcess:
                    continue
                except psutil.AccessDenied as error:
                    raise RuntimeError(f"cannot kill orphaned worker PID {process.pid}") from error
        if not matching and not alive:
            return len(tracked)
        if time.monotonic() >= deadline:
            raise RuntimeError("orphaned worker processes did not exit before the cleanup timeout")
        time.sleep(0.05)


def supervise(home: Path, *, boot: bool = False) -> int:
    with ExitStack() as lifetime:
        with locked(home / "control.lock"):
            lifetime.enter_context(locked(home / "supervisor.lock", wait=False))
            if boot and read_json(home / "desired.json").get("state") == "stopped":
                return 0
            (home / "stop.json").unlink(missing_ok=True)
            write_json(home / "desired.json", {"state": "running"})
        _enable_subreaper()
        managed_environment = os.environ.get("COPE_WORKER_MANAGED_ENV") == "1"
        settings = WorkerSettings.read(home, prefer_environment=managed_environment)
        if managed_environment:
            with locked(home / "config.lock"):
                settings.write(home)
        credential_file = settings.credential_file(home)
        command = [
            str(release_python(home) or sys.executable), "-m", "cope", "worker",
            "--managed-home", str(home), "--server-url", settings.server,
            "--state-file", str(home / "worker.json"),
        ]
        if credential_file.is_file():
            command.extend(["--token-file", str(credential_file)])
        if settings.threads is not None:
            command.extend(["--cpu-capacity", str(settings.threads)])
        launch_id = os.environ.get("COPE_WORKER_LAUNCH_ID") or uuid.uuid4().hex
        write_json(home / "status.json", {"state": "starting", "launch_id": launch_id})

        stop_signal = None

        def stop(signum, _frame) -> None:
            nonlocal stop_signal
            stop_signal = signum == signal.SIGINT or bool(stop_signal)

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        child = None
        try:
            environment = _child_environment(home)
            environment["COPE_WORKER_LAUNCH_ID"] = launch_id
            if credential_file.is_file():
                environment.pop("COPE_WORKER_IDENTITY_TOKEN_FILE", None)
                environment.pop("COPE_WORKER_TOKEN", None)
            child = subprocess.Popen(command, cwd=home, env=environment, start_new_session=True)
            safe_since = None
            while child.poll() is None:
                if stop_signal is not None:
                    request_stop(home, "safe", persist=stop_signal)
                    stop_signal = None
                mode = read_json(home / "stop.json").get("mode")
                if mode == "force":
                    child.kill()
                    break
                cleanup_started = mode == "grace" and read_json(home / "status.json").get("state") == "stopping"
                if mode == "safe" or cleanup_started:
                    safe_since = safe_since or time.monotonic()
                    if time.monotonic() - safe_since > 15:
                        child.kill()
                        break
                time.sleep(0.1)
            return_code = child.wait()
        finally:
            if child is not None and child.poll() is None:
                child.kill()
                child.wait()
            _kill_adopted_children()
            with locked(home / "control.lock"):
                request = read_json(home / "stop.json")
                stopped = bool(request.get("mode"))
                (home / "stop.json").unlink(missing_ok=True)
                if stopped and request.get("persist", True):
                    (home / "update.json").unlink(missing_ok=True)
                    write_json(home / "desired.json", {"state": "stopped"})
                status = read_json(home / "status.json")
                status.update(state="stopped" if stopped else status.get("state", "failed"), processes=0)
                if not stopped and status["state"] in {"starting", "ready", "busy", "draining"}:
                    status["state"] = "failed"
                write_json(home / "status.json", status)
        return 0 if stopped else return_code
