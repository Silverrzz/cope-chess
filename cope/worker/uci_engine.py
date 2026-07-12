from __future__ import annotations

import hashlib
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from collections.abc import Callable
from pathlib import Path
from typing import Iterator

from cope.core.models import EngineSpec
from cope.core.stream import clamp_uci_info_line


LOG = logging.getLogger("cope.worker.engine")
_BUILD_FAILURE_COOLDOWN_S = 60.0
_BUILD_LOCKS: dict[Path, threading.Lock] = {}
_BUILD_LOCKS_GUARD = threading.Lock()


class EnginePreparationError(RuntimeError):
    def __init__(self, spec: EngineSpec, stage: str, detail: str):
        self.engine_id = spec.engine_id
        self.engine_name = spec.name
        self.stage = stage
        self.detail = detail.strip() or "unknown engine preparation error"
        super().__init__(f"{spec.name} {stage} failed: {self.detail}")


class UciEngineProcess:
    def __init__(self, spec: EngineSpec):
        self._spec = spec
        self._source_dir = _engine_source_dir(spec)
        self._binary_path = self._source_dir / spec.binary_path
        self._process: subprocess.Popen[str] | None = None
        self._stdout: queue.Queue[str | None] = queue.Queue()
        self._stdout_thread: threading.Thread | None = None
        self._io_lock = threading.Lock()
        self._built = False
        LOG.info(
            "engine wrapper created engine_id=%s engine=%s source_dir=%s binary=%s",
            self._spec.engine_id,
            self._spec.name,
            self._source_dir,
            self._binary_path,
        )

    @property
    def process_started(self) -> bool:
        return self._process is not None

    def prepare(self) -> None:
        """Install and build this engine without starting a UCI game process."""
        with self._io_lock:
            try:
                self._ensure_built()
            except EnginePreparationError:
                raise
            except Exception as exc:
                raise EnginePreparationError(self._spec, "cache", str(exc)) from exc

    def handle_command(
        self,
        command: str,
        line_callback: Callable[[str], None] | None = None,
    ) -> list[str]:
        with self._io_lock:
            LOG.info(
                "engine command handling engine_id=%s engine=%s command=%s",
                self._spec.engine_id,
                self._spec.name,
                command,
            )
            if command == "quit":
                LOG.info(
                    "engine quit command received engine_id=%s engine=%s",
                    self._spec.engine_id,
                    self._spec.name,
                )
                self.close()
                return []

            self._send(command)
            if command == "uci":
                return self._read_until(lambda line: line == "uciok")
            if command == "isready":
                return self._read_until(lambda line: line == "readyok")
            if command.startswith("go"):
                return self._read_until(
                    lambda line: line.startswith("bestmove"),
                    line_callback=line_callback,
                )
            if command == "stop":
                return self._drain_available()

            return self._drain_available()

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            LOG.info(
                "engine close skipped engine_id=%s engine=%s reason=not_started",
                self._spec.engine_id,
                self._spec.name,
            )
            return
        LOG.info(
            "engine closing engine_id=%s engine=%s pid=%s",
            self._spec.engine_id,
            self._spec.name,
            process.pid,
        )
        try:
            if process.poll() is None and process.stdin is not None:
                try:
                    LOG.info(
                        "engine stdin sending shutdown engine_id=%s engine=%s pid=%s line=%s",
                        self._spec.engine_id,
                        self._spec.name,
                        process.pid,
                        "quit",
                    )
                    process.stdin.write("quit\n")
                    process.stdin.flush()
                    process.wait(timeout=2)
                except Exception:
                    LOG.exception(
                        "engine graceful shutdown failed engine_id=%s engine=%s pid=%s",
                        self._spec.engine_id,
                        self._spec.name,
                        process.pid,
                    )
                    pass
            if process.poll() is None:
                LOG.warning(
                    "engine terminating engine_id=%s engine=%s pid=%s",
                    self._spec.engine_id,
                    self._spec.name,
                    process.pid,
                )
                process.terminate()
                process.wait(timeout=2)
        except Exception:
            if process.poll() is None:
                try:
                    LOG.warning(
                        "engine killing engine_id=%s engine=%s pid=%s",
                        self._spec.engine_id,
                        self._spec.name,
                        process.pid,
                    )
                    process.kill()
                    process.wait(timeout=2)
                except Exception:
                    LOG.exception(
                        "engine kill failed engine_id=%s engine=%s pid=%s",
                        self._spec.engine_id,
                        self._spec.name,
                        process.pid,
                    )
                    pass
        finally:
            for stream in (process.stdin, process.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    LOG.exception(
                        "engine stream close failed engine_id=%s engine=%s pid=%s",
                        self._spec.engine_id,
                        self._spec.name,
                        process.pid,
                    )
                    pass
            LOG.info(
                "engine closed engine_id=%s engine=%s pid=%s return_code=%s",
                self._spec.engine_id,
                self._spec.name,
                process.pid,
                process.poll(),
            )

    def _send(self, command: str) -> None:
        process = self._ensure_process()
        if process.stdin is None:
            raise RuntimeError(f"{self._spec.name} stdin is not available")
        try:
            LOG.debug(
                "engine stdin engine_id=%s engine=%s pid=%s line=%s",
                self._spec.engine_id,
                self._spec.name,
                process.pid,
                command,
            )
            process.stdin.write(command + "\n")
            process.stdin.flush()
        except BrokenPipeError as exc:
            raise RuntimeError(f"{self._spec.name} pipe broke while sending {command!r}") from exc

    def _ensure_process(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            LOG.debug(
                "engine process ready engine_id=%s engine=%s pid=%s",
                self._spec.engine_id,
                self._spec.name,
                self._process.pid,
            )
            return self._process
        if self._process is not None:
            LOG.error(
                "engine process exited engine_id=%s engine=%s return_code=%s",
                self._spec.engine_id,
                self._spec.name,
                self._process.returncode,
            )
            raise RuntimeError(
                f"{self._spec.name} exited with code {self._process.returncode}"
            )

        self._ensure_built()
        if not self._binary_path.exists():
            raise RuntimeError(f"{self._spec.name} binary does not exist: {self._binary_path}")

        try:
            LOG.info(
                "engine starting engine_id=%s engine=%s binary=%s cwd=%s",
                self._spec.engine_id,
                self._spec.name,
                self._binary_path,
                self._source_dir,
            )
            self._process = subprocess.Popen(
                [str(self._binary_path.resolve())],
                cwd=self._source_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise RuntimeError(f"{self._spec.name} failed to start {self._binary_path}") from exc

        LOG.info(
            "engine started engine_id=%s engine=%s pid=%s",
            self._spec.engine_id,
            self._spec.name,
            self._process.pid,
        )
        self._stdout = queue.Queue()
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(self._process,),
            daemon=True,
        )
        self._stdout_thread.start()
        return self._process

    def _ensure_built(self) -> None:
        if self._built and self._binary_path.exists():
            LOG.info(
                "engine build already prepared engine_id=%s engine=%s binary=%s",
                self._spec.engine_id,
                self._spec.name,
                self._binary_path,
            )
            return

        build_key = _build_key(self._spec)
        cache_root = self._source_dir.parent
        cache_name = self._source_dir.name
        lock_path = cache_root / ".locks" / f"{cache_name}.lock"
        failure_path = cache_root / ".failures" / f"{cache_name}.txt"

        LOG.info(
            "engine build waiting for machine cache engine_id=%s engine=%s cache=%s",
            self._spec.engine_id,
            self._spec.name,
            self._source_dir,
        )
        with _exclusive_build_lock(lock_path):
            if _build_is_ready(self._source_dir, self._binary_path, build_key):
                self._built = True
                LOG.info(
                    "engine machine cache hit engine_id=%s engine=%s source_dir=%s commit=%s",
                    self._spec.engine_id,
                    self._spec.name,
                    self._source_dir,
                    self._spec.commit,
                )
                return

            cached_failure = _recent_build_failure(failure_path)
            if cached_failure is not None:
                stage, detail = cached_failure
                raise EnginePreparationError(
                    self._spec,
                    stage,
                    "a recent machine-wide build attempt failed; retry is temporarily "
                    f"suppressed:\n{detail}",
                )

            LOG.info(
                "engine machine build starting engine_id=%s engine=%s source_dir=%s commit=%s",
                self._spec.engine_id,
                self._spec.name,
                self._source_dir,
                self._spec.commit,
            )
            # The name is deterministic because the build lock guarantees one
            # writer. A process killed mid-build leaves a directory that the
            # next attempt can identify and remove.
            temporary = cache_root / ".tmp" / cache_name
            stage = "cache"
            try:
                if self._source_dir.exists():
                    shutil.rmtree(self._source_dir)
                if temporary.exists():
                    shutil.rmtree(temporary)
                temporary.parent.mkdir(parents=True, exist_ok=True)

                stage = "clone"
                command = ["git", "clone"]
                if self._spec.branch:
                    command.extend(["--branch", self._spec.branch])
                command.extend([self._spec.git_url, str(temporary)])
                _run_checked(command, cwd=None)
                stage = "checkout"
                _run_checked(
                    ["git", "checkout", "--force", "--detach", self._spec.commit],
                    cwd=temporary,
                )
                stage = "build"
                _run_checked(self._spec.build_cmd, cwd=temporary, shell=True)

                stage = "verify"
                temporary_binary = temporary / self._spec.binary_path
                if not temporary_binary.is_file():
                    raise RuntimeError(
                        f"{self._spec.name} build completed but binary was not found: "
                        f"{temporary_binary}"
                    )
                (temporary / ".cope-build").write_text(build_key, encoding="utf-8")
                os.replace(temporary, self._source_dir)
                if failure_path.exists():
                    failure_path.unlink()
            except Exception as exc:
                error = EnginePreparationError(self._spec, stage, str(exc))
                _record_build_failure(failure_path, error.stage, error.detail)
                raise error from exc
            finally:
                if temporary.exists():
                    try:
                        shutil.rmtree(temporary)
                    except OSError:
                        LOG.exception("could not remove temporary engine build %s", temporary)

            self._built = True
            LOG.info(
                "engine machine build ready engine_id=%s engine=%s binary=%s",
                self._spec.engine_id,
                self._spec.name,
                self._binary_path,
            )

    def _read_stdout(self, process: subprocess.Popen[str]) -> None:
        LOG.info(
            "engine stdout reader started engine_id=%s engine=%s pid=%s",
            self._spec.engine_id,
            self._spec.name,
            process.pid,
        )
        if process.stdout is None:
            self._stdout.put(None)
            LOG.warning(
                "engine stdout unavailable engine_id=%s engine=%s pid=%s",
                self._spec.engine_id,
                self._spec.name,
                process.pid,
            )
            return
        try:
            for line in process.stdout:
                line = clamp_uci_info_line(line.rstrip("\r\n"))
                LOG.debug(
                    "engine stdout engine_id=%s engine=%s pid=%s line=%s",
                    self._spec.engine_id,
                    self._spec.name,
                    process.pid,
                    line,
                )
                self._stdout.put(line)
        finally:
            self._stdout.put(None)
            LOG.info(
                "engine stdout reader stopped engine_id=%s engine=%s pid=%s return_code=%s",
                self._spec.engine_id,
                self._spec.name,
                process.pid,
                process.poll(),
            )

    def _read_until(
        self,
        predicate,
        line_callback: Callable[[str], None] | None = None,
    ) -> list[str]:
        LOG.debug(
            "engine output wait started engine_id=%s engine=%s",
            self._spec.engine_id,
            self._spec.name,
        )
        deadline = time.monotonic() + 60.0
        lines: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"{self._spec.name} timed out waiting for UCI output")
            try:
                line = self._stdout.get(timeout=remaining)
            except queue.Empty as exc:
                raise RuntimeError(f"{self._spec.name} timed out waiting for UCI output") from exc
            if line is None:
                process = self._process
                code = None if process is None else process.poll()
                raise RuntimeError(f"{self._spec.name} exited while waiting for UCI output: {code}")
            lines.append(line)
            if line_callback is not None and line.startswith("info"):
                line_callback(line)
            if predicate(line):
                LOG.debug(
                    "engine output wait finished engine_id=%s engine=%s lines=%s terminal_line=%s",
                    self._spec.engine_id,
                    self._spec.name,
                    len(lines),
                    line,
                )
                return lines

    def _drain_available(self) -> list[str]:
        lines: list[str] = []
        while True:
            try:
                line = self._stdout.get_nowait()
            except queue.Empty:
                LOG.debug(
                    "engine output drained engine_id=%s engine=%s lines=%s%s",
                    self._spec.engine_id,
                    self._spec.name,
                    len(lines),
                    _line_sample(lines),
                )
                return lines
            if line is None:
                process = self._process
                code = None if process is None else process.poll()
                raise RuntimeError(f"{self._spec.name} exited: {code}")
            lines.append(line)


def _engine_source_dir(spec: EngineSpec) -> Path:
    configured_cache_root = os.environ.get("COPE_WORKER_ENGINE_DIR")
    if configured_cache_root:
        cache_root = Path(configured_cache_root).expanduser().resolve()
    else:
        cache_root = (_effective_home_dir() / ".cope-worker" / "engines").resolve()
    return cache_root / f"engine-{_build_key(spec)}"


def _effective_home_dir() -> Path:
    if os.name == "posix":
        import pwd

        return Path(pwd.getpwuid(os.geteuid()).pw_dir)
    return Path.home()


def _build_is_ready(source_dir: Path, binary_path: Path, build_key: str) -> bool:
    marker = source_dir / ".cope-build"
    try:
        return (
            source_dir.is_dir()
            and binary_path.is_file()
            and marker.read_text(encoding="utf-8") == build_key
        )
    except (OSError, UnicodeError):
        return False


def _recent_build_failure(path: Path) -> tuple[str, str] | None:
    try:
        age = time.time() - path.stat().st_mtime
        if age >= _BUILD_FAILURE_COOLDOWN_S:
            path.unlink(missing_ok=True)
            return None
        stage, separator, detail = path.read_text(encoding="utf-8").partition("\n")
        return (stage, detail.strip()) if separator else ("build", stage.strip())
    except (FileNotFoundError, OSError, UnicodeError):
        return None


def _record_build_failure(path: Path, stage: str, detail: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{stage}\n{detail[-8000:]}\n", encoding="utf-8")
    except OSError:
        # Preserve the actual clone/build exception, especially when the
        # reason the failure cannot be recorded is a full filesystem.
        LOG.exception("could not record engine build failure in %s", path)


@contextmanager
def _exclusive_build_lock(path: Path) -> Iterator[None]:
    """Serialize one engine build across pool threads and Linux processes."""
    with _BUILD_LOCKS_GUARD:
        thread_lock = _BUILD_LOCKS.setdefault(path, threading.Lock())

    with thread_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as lock_file:
            if os.name == "posix":
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "posix":
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _build_key(spec: EngineSpec) -> str:
    digest = hashlib.blake2s(digest_size=16)
    for value in (
        spec.git_url,
        spec.branch,
        spec.commit,
        spec.build_cmd,
        spec.binary_path,
        *spec.required_dependencies,
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _run_checked(command, *, cwd: Path | None, shell: bool = False) -> None:
    LOG.info(
        "worker command started cwd=%s shell=%s command=%s",
        cwd,
        shell,
        _format_command(command),
    )
    try:
        completed = subprocess.run(
            command,
            cwd=None if cwd is None else str(cwd),
            shell=shell,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"failed to run command: {command}") from exc

    output = (completed.stdout or "").strip()
    if output:
        LOG.debug(
            "worker command output cwd=%s command=%s output=%s",
            cwd,
            _format_command(command),
            output,
        )
    LOG.info(
        "worker command finished cwd=%s exit_code=%s command=%s",
        cwd,
        completed.returncode,
        _format_command(command),
    )
    if completed.returncode != 0:
        if len(output) > 8000:
            output = output[-8000:]
        raise RuntimeError(
            f"command failed with exit code {completed.returncode}: {command}\n{output}"
        )


def _format_command(command) -> str:
    if isinstance(command, str):
        return command
    return " ".join(str(part) for part in command)


def _line_sample(lines: list[str]) -> str:
    if not lines:
        return ""
    line = lines[-1]
    if len(line) > 200:
        line = f"{line[:197]}..."
    return f" last_line={line!r}"
