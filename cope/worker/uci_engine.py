from __future__ import annotations

import logging
import hashlib
import os
import queue
import signal
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Iterator
from urllib.parse import urljoin, urlsplit, urlunsplit

from cope.core.benchmark import parse_benchmark_nps
from cope.core.models import EngineSpec
from cope.core.stream import clamp_uci_info_line
from cope.engine_artifacts import extract_artifact_archive


LOG = logging.getLogger("cope.worker.engine")
_ARTIFACT_FAILURE_COOLDOWN_S = 60.0
_ARTIFACT_LOCKS: dict[Path, threading.Lock] = {}
_ARTIFACT_LOCKS_GUARD = threading.Lock()
_COMMAND_OUTPUT_QUEUE_SIZE = 256
_DEFAULT_ENGINE_BUILD_JOBS = 4


class EnginePreparationError(RuntimeError):
    def __init__(self, spec: EngineSpec, stage: str, detail: str):
        self.engine_id = spec.engine_id
        self.engine_name = spec.name
        self.stage = stage
        self.detail = detail.strip() or "unknown engine preparation error"
        super().__init__(f"{spec.name} {stage} failed: {self.detail}")


class UciEngineProcess:
    def __init__(
        self,
        spec: EngineSpec,
        *,
        server_url: str,
        credential: str,
        progress_callback: Callable[[str, str, str, str], None] | None = None,
        command_timeout_s: int | None = None,
        allow_build: bool = False,
    ):
        self._spec = spec
        self._progress_callback = progress_callback
        self._command_timeout_s = command_timeout_s
        self._allow_build = allow_build
        self._source_dir = _engine_source_dir(spec)
        entrypoint = "engine" if spec.artifact is None else spec.artifact.entrypoint
        self._binary_path = self._source_dir / entrypoint
        self._download_url = (
            None
            if spec.artifact is None
            else _absolute_download_url(server_url, spec.artifact.url)
        )
        self._credential = credential
        self._process: subprocess.Popen[str] | None = None
        self._stdout: queue.Queue[str | None] = queue.Queue()
        self._stdout_thread: threading.Thread | None = None
        self._io_lock = threading.Lock()
        self._stdin_lock = threading.Lock()
        self._prepared = False
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

    def report_progress(
        self,
        stage: str,
        substage: str,
        status: str,
        detail: str,
    ) -> None:
        if self._progress_callback is not None:
            self._progress_callback(stage, substage, status, detail)

    @property
    def artifact_path(self) -> Path:
        return self._binary_path

    @property
    def artifact_directory(self) -> Path:
        return self._source_dir

    def prepare(self) -> None:
        """Download and verify this version without starting a UCI process."""
        with self._io_lock:
            try:
                self.report_progress(
                    "engines",
                    "artifact_prepare",
                    "running",
                    f"Preparing {self._spec.name} engine artifact",
                )
                self._ensure_artifact()
                self.report_progress(
                    "engines",
                    "artifact_prepare",
                    "completed",
                    f"{self._spec.name} engine artifact is ready",
                )
            except EnginePreparationError:
                raise
            except Exception as exc:
                raise EnginePreparationError(self._spec, "cache", str(exc)) from exc

    def benchmark(self, timeout_s: int) -> tuple[int, int]:
        with self._io_lock:
            try:
                self._ensure_artifact()
            except EnginePreparationError:
                raise
            except Exception as exc:
                raise EnginePreparationError(
                    self._spec,
                    "benchmark",
                    str(exc),
                ) from exc
            started_ns = time.monotonic_ns()
            try:
                completed = subprocess.run(
                    [str(self._binary_path.resolve()), "bench"],
                    cwd=self._source_dir,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=timeout_s,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise EnginePreparationError(
                    self._spec,
                    "benchmark",
                    f"engine bench exceeded {timeout_s} seconds",
                ) from exc
            except OSError as exc:
                raise EnginePreparationError(
                    self._spec,
                    "benchmark",
                    str(exc),
                ) from exc
            elapsed_ms = max(0, round((time.monotonic_ns() - started_ns) / 1_000_000))
            output = (completed.stdout or "")[-64_000:]
            if completed.returncode != 0:
                raise EnginePreparationError(
                    self._spec,
                    "benchmark",
                    f"engine bench exited with code {completed.returncode}: {output}",
                )
            nps = parse_benchmark_nps(output)
            if nps is None:
                raise EnginePreparationError(
                    self._spec,
                    "benchmark",
                    f"engine bench output did not contain a positive NPS value: {output}",
                )
            return nps, elapsed_ms

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
                    timeout_s=None,
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

    def stop_search(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        self._send("stop")

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
            with self._stdin_lock:
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

        self._ensure_artifact()
        if not self._binary_path.exists():
            raise RuntimeError(f"{self._spec.name} binary does not exist: {self._binary_path}")

        try:
            self.report_progress(
                "startup",
                "process_launch",
                "running",
                f"Launching {self._spec.name}",
            )
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
        self.report_progress(
            "startup",
            "process_launch",
            "completed",
            f"{self._spec.name} process started with pid {self._process.pid}",
        )
        self._stdout = queue.Queue()
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(self._process,),
            daemon=True,
        )
        self._stdout_thread.start()
        return self._process

    def _ensure_downloaded_artifact(self) -> None:
        artifact = self._spec.artifact
        if artifact is None or self._download_url is None:
            raise EnginePreparationError(self._spec, "download", "engine artifact is unavailable")
        if self._prepared and self._binary_path.exists():
            return
        artifact_key = artifact.sha256
        cache_root = self._source_dir.parent
        cache_name = self._source_dir.name
        lock_path = cache_root / ".locks" / f"{cache_name}.lock"
        failure_path = cache_root / ".failures" / f"{cache_name}.txt"
        self.report_progress(
            "engines",
            "cache_lock",
            "running",
            f"Waiting for the machine cache lock for {self._spec.name}",
        )
        with _exclusive_artifact_lock(lock_path):
            self.report_progress(
                "engines",
                "cache_lookup",
                "running",
                f"Checking the machine cache for {self._spec.name}",
            )
            if _artifact_is_ready(self._source_dir, self._binary_path, artifact_key):
                self._prepared = True
                self.report_progress(
                    "engines",
                    "cache_lookup",
                    "completed",
                    f"Using the cached {self._spec.name} artifact",
                )
                return
            cached_failure = _recent_artifact_failure(failure_path)
            if cached_failure is not None:
                stage, detail = cached_failure
                raise EnginePreparationError(
                    self._spec,
                    stage,
                    "a recent machine-wide download attempt failed; retry is temporarily "
                    f"suppressed:\n{detail}",
                )
            temporary = cache_root / ".tmp" / cache_name
            archive_path = cache_root / ".tmp" / f"{cache_name}.tar.gz"
            stage = "cache"
            try:
                if self._source_dir.exists():
                    shutil.rmtree(self._source_dir)
                if temporary.exists():
                    shutil.rmtree(temporary)
                archive_path.unlink(missing_ok=True)
                archive_path.parent.mkdir(parents=True, exist_ok=True)
                stage = "download"
                self.report_progress(
                    "engines",
                    "artifact_download",
                    "running",
                    f"Downloading the {self._spec.name} engine artifact",
                )
                _download_artifact(
                    self._download_url,
                    archive_path,
                    credential=self._credential,
                    expected_size=artifact.size,
                    expected_sha256=artifact.sha256,
                )
                self.report_progress(
                    "engines",
                    "artifact_download",
                    "completed",
                    f"Downloaded the {self._spec.name} engine artifact",
                )
                stage = "verify"
                self.report_progress(
                    "engines",
                    "artifact_verify",
                    "running",
                    f"Verifying the {self._spec.name} engine artifact",
                )
                extract_artifact_archive(
                    archive_path,
                    temporary,
                    expected_build_hash=self._spec.build_hash,
                    expected_entrypoint=artifact.entrypoint,
                )
                temporary_binary = temporary / artifact.entrypoint
                if not temporary_binary.is_file() or temporary_binary.stat().st_size <= 0:
                    raise RuntimeError("engine artifact does not contain its entrypoint")
                if os.name == "posix" and not os.access(temporary_binary, os.X_OK):
                    raise RuntimeError("engine artifact entrypoint is not executable")
                (temporary / ".cope-artifact").write_text(artifact_key, encoding="utf-8")
                os.replace(temporary, self._source_dir)
                failure_path.unlink(missing_ok=True)
                self.report_progress(
                    "engines",
                    "artifact_verify",
                    "completed",
                    f"Verified and cached the {self._spec.name} engine artifact",
                )
            except Exception as exc:
                error = EnginePreparationError(self._spec, stage, str(exc))
                _record_artifact_failure(failure_path, error.stage, error.detail)
                raise error from exc
            finally:
                archive_path.unlink(missing_ok=True)
                if temporary.exists():
                    try:
                        shutil.rmtree(temporary)
                    except OSError:
                        LOG.exception("could not remove temporary engine artifact %s", temporary)
            self._prepared = True

    def _ensure_artifact(self) -> None:
        if self._spec.artifact is not None:
            self._ensure_downloaded_artifact()
            return
        if not self._allow_build:
            raise EnginePreparationError(
                self._spec,
                "artifact",
                "no published worker artifact is available; run this engine's benchmark first",
            )
        if self._prepared and self._binary_path.exists():
            LOG.info(
                "engine artifact already prepared engine_id=%s engine=%s binary=%s",
                self._spec.engine_id,
                self._spec.name,
                self._binary_path,
            )
            return

        artifact_key = self._spec.build_hash
        cache_root = self._source_dir.parent
        cache_name = self._source_dir.name
        lock_path = cache_root / ".locks" / f"{cache_name}.lock"
        failure_path = cache_root / ".failures" / f"{cache_name}.txt"

        LOG.info(
            "engine download waiting for machine cache engine_id=%s engine=%s cache=%s",
            self._spec.engine_id,
            self._spec.name,
            self._source_dir,
        )
        self.report_progress(
            "engines",
            "cache_lock",
            "running",
            f"Waiting for the machine cache lock for {self._spec.name}",
        )
        with _exclusive_artifact_lock(lock_path):
            self.report_progress(
                "engines",
                "cache_lookup",
                "running",
                f"Checking the machine cache for {self._spec.name}",
            )
            if _artifact_is_ready(self._source_dir, self._binary_path, artifact_key):
                self._prepared = True
                self.report_progress(
                    "engines",
                    "cache_lookup",
                    "completed",
                    f"Using the cached {self._spec.name} artifact",
                )
                LOG.info(
                    "engine machine cache hit engine_id=%s engine=%s source_dir=%s sha256=%s",
                    self._spec.engine_id,
                    self._spec.name,
                    self._source_dir,
                    self._spec.build_hash,
                )
                return

            cached_failure = _recent_artifact_failure(failure_path)
            if cached_failure is not None:
                stage, detail = cached_failure
                raise EnginePreparationError(
                    self._spec,
                    stage,
                    "a recent machine-wide download attempt failed; retry is temporarily "
                    f"suppressed:\n{detail}",
                )

            LOG.info(
                "engine machine download starting engine_id=%s engine=%s cache=%s sha256=%s",
                self._spec.engine_id,
                self._spec.name,
                self._source_dir,
                self._spec.build_hash,
            )
            self.report_progress(
                "engines",
                "source_download",
                "running",
                f"Downloading {self._spec.name} source at {self._spec.source_ref}",
            )
            temporary = cache_root / ".tmp" / cache_name
            stage = "cache"
            try:
                if self._source_dir.exists():
                    shutil.rmtree(self._source_dir)
                if temporary.exists():
                    shutil.rmtree(temporary)
                temporary.parent.mkdir(parents=True, exist_ok=True)

                temporary.mkdir(parents=True)
                stage = "download"
                repository = temporary / "repository"
                self._run_artifact_command(
                    ["git", "init", str(repository)], cwd=None, substage="source_download"
                )
                self._run_artifact_command(
                    ["git", "-C", str(repository), "remote", "add", "origin", self._spec.repository_url],
                    cwd=None,
                    substage="source_download",
                )
                self._run_artifact_command(
                    [
                        "git",
                        "-C",
                        str(repository),
                        "fetch",
                        "--depth",
                        "1",
                        "origin",
                        self._spec.source_ref,
                    ],
                    cwd=None,
                    substage="source_download",
                )
                self._run_artifact_command(
                    ["git", "-C", str(repository), "checkout", "--detach", "FETCH_HEAD"],
                    cwd=None,
                    substage="source_download",
                )
                self._run_artifact_command(
                    [
                        "git",
                        "-C",
                        str(repository),
                        "submodule",
                        "update",
                        "--init",
                        "--recursive",
                    ],
                    cwd=None,
                    env={"GIT_LFS_SKIP_SMUDGE": "1"},
                    substage="source_download",
                )
                self.report_progress(
                    "engines",
                    "source_download",
                    "completed",
                    f"Downloaded {self._spec.name} source",
                )
                stage = "build"
                self.report_progress(
                    "engines",
                    "container_build",
                    "running",
                    f"Building the {self._spec.name} engine image",
                )
                build_jobs = _engine_build_jobs()
                (repository / "Dockerfile.cope").write_text(
                    _bounded_engine_dockerfile(
                        self._spec.dockerfile,
                        build_jobs,
                        self._spec.name,
                    ),
                    encoding="utf-8",
                )
                # The supplied Dockerfile is built against Cope's complete checkout. An
                # upstream .dockerignore may belong to a completely different Dockerfile
                # (and some repositories exclude their entire source tree), so it must not
                # control the Cope build context. Keep only the parent repository metadata
                # out of the context; nested submodule metadata remains available to builds
                # that need to run Git LFS commands inside a submodule.
                (repository / ".dockerignore").write_text("/.git\n", encoding="utf-8")
                image_name = f"cope-engine-{artifact_key[:24]}"
                container_name = f"{image_name}-{os.getpid()}-{threading.get_ident()}"
                self._run_artifact_command(
                    [
                        "docker",
                        "build",
                        "--build-arg",
                        f"COPE_BUILD_JOBS={build_jobs}",
                        "--file",
                        "Dockerfile.cope",
                        "--tag",
                        image_name,
                        ".",
                    ],
                    cwd=repository,
                    env={"DOCKER_BUILDKIT": "1"},
                    substage="container_build",
                )
                self.report_progress(
                    "engines",
                    "container_build",
                    "completed",
                    f"Built the {self._spec.name} engine image",
                )
                self.report_progress(
                    "engines",
                    "artifact_extract",
                    "running",
                    f"Extracting the {self._spec.name} executable",
                )
                try:
                    self._run_artifact_command(
                        ["docker", "create", "--name", container_name, image_name],
                        cwd=None,
                        substage="artifact_extract",
                    )
                    try:
                        self._run_artifact_command(
                            [
                                "docker",
                                "cp",
                                f"{container_name}:/opt/cope/.",
                                str(temporary),
                            ],
                            cwd=None,
                            substage="artifact_extract",
                        )
                    finally:
                        self._run_artifact_command(
                            ["docker", "rm", "-f", container_name],
                            cwd=None,
                            substage="artifact_extract",
                        )
                finally:
                    try:
                        self._run_artifact_command(
                            ["docker", "image", "rm", "--force", image_name],
                            cwd=None,
                            substage="artifact_cleanup",
                        )
                    except Exception:
                        LOG.exception("could not remove temporary engine image %s", image_name)
                stage = "verify"
                temporary_binary = temporary / "engine"
                if not temporary_binary.is_file() or temporary_binary.stat().st_size <= 0:
                    raise RuntimeError("Docker image does not contain /opt/cope/engine")
                self.report_progress(
                    "engines",
                    "artifact_extract",
                    "completed",
                    f"Extracted the {self._spec.name} executable",
                )
                self.report_progress(
                    "engines",
                    "artifact_verify",
                    "running",
                    f"Verifying the {self._spec.name} executable",
                )
                temporary_binary.chmod(0o700)
                (temporary / ".cope-artifact").write_text(artifact_key, encoding="utf-8")
                shutil.rmtree(repository)
                os.replace(temporary, self._source_dir)
                if failure_path.exists():
                    failure_path.unlink()
                self.report_progress(
                    "engines",
                    "artifact_verify",
                    "completed",
                    f"Verified and cached the {self._spec.name} executable",
                )
            except Exception as exc:
                error = EnginePreparationError(self._spec, stage, str(exc))
                _record_artifact_failure(failure_path, error.stage, error.detail)
                raise error from exc
            finally:
                if temporary.exists():
                    try:
                        shutil.rmtree(temporary)
                    except OSError:
                        LOG.exception("could not remove temporary engine download %s", temporary)

            self._prepared = True
            LOG.info(
                "engine machine artifact ready engine_id=%s engine=%s binary=%s",
                self._spec.engine_id,
                self._spec.name,
                self._binary_path,
            )

    def _run_artifact_command(
        self,
        command,
        *,
        cwd: Path | None,
        substage: str,
        env: Mapping[str, str] | None = None,
    ) -> None:
        _run_checked(
            command,
            cwd=cwd,
            env=env,
            timeout_s=self._command_timeout_s,
            output_callback=lambda detail: self.report_progress(
                "engines",
                substage,
                "running",
                detail,
            ),
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
        timeout_s: float | None = 60.0,
    ) -> list[str]:
        LOG.debug(
            "engine output wait started engine_id=%s engine=%s",
            self._spec.engine_id,
            self._spec.name,
        )
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        lines: list[str] = []
        while True:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
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
    cache_key = spec.build_hash if spec.artifact is None else spec.artifact.sha256
    prefix = "build" if spec.artifact is None else "artifact"
    return cache_root / f"{prefix}-{cache_key}"


def _effective_home_dir() -> Path:
    if os.name == "posix":
        import pwd

        return Path(pwd.getpwuid(os.geteuid()).pw_dir)
    return Path.home()


def _artifact_is_ready(
    source_dir: Path,
    binary_path: Path,
    artifact_key: str,
) -> bool:
    marker = source_dir / ".cope-artifact"
    try:
        return (
            source_dir.is_dir()
            and binary_path.is_file()
            and binary_path.stat().st_size > 0
            and marker.read_text(encoding="utf-8") == artifact_key
        )
    except (OSError, UnicodeError):
        return False


def _recent_artifact_failure(path: Path) -> tuple[str, str] | None:
    try:
        age = time.time() - path.stat().st_mtime
        if age >= _ARTIFACT_FAILURE_COOLDOWN_S:
            path.unlink(missing_ok=True)
            return None
        stage, separator, detail = path.read_text(encoding="utf-8").partition("\n")
        return (stage, detail.strip()) if separator else ("cache", stage.strip())
    except (FileNotFoundError, OSError, UnicodeError):
        return None


def _record_artifact_failure(path: Path, stage: str, detail: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{stage}\n{detail[-8000:]}\n", encoding="utf-8")
    except OSError:
        # Preserve the actual download/verification exception, especially when the
        # reason the failure cannot be recorded is a full filesystem.
        LOG.exception("could not record engine artifact failure in %s", path)


@contextmanager
def _exclusive_artifact_lock(path: Path) -> Iterator[None]:
    """Serialize one artifact download across pool threads and Linux processes."""
    with _ARTIFACT_LOCKS_GUARD:
        thread_lock = _ARTIFACT_LOCKS.setdefault(path, threading.Lock())

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


def _absolute_download_url(server_url: str, path: str) -> str:
    if path.startswith(("https://", "http://")):
        return path
    parsed = urlsplit(server_url)
    scheme = "https" if parsed.scheme in {"wss", "https"} else "http"
    origin = urlunsplit((scheme, parsed.netloc, "/", "", ""))
    return urljoin(origin, path.lstrip("/"))


def _download_artifact(
    url: str,
    destination: Path,
    *,
    credential: str,
    expected_size: int,
    expected_sha256: str,
) -> None:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {credential}",
            "Accept": "application/gzip",
        },
    )
    digest = hashlib.sha256()
    received = 0
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) != expected_size:
                raise RuntimeError("server reported an unexpected artifact size")
            with destination.open("xb") as output:
                while True:
                    chunk = response.read(min(1024 * 1024, expected_size - received + 1))
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > expected_size:
                        raise RuntimeError("server sent more artifact data than registered")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"artifact server returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"could not reach artifact server: {exc.reason}") from exc
    if received != expected_size:
        raise RuntimeError("downloaded artifact size does not match the descriptor")
    actual_sha256 = digest.hexdigest()
    if actual_sha256 != expected_sha256:
        raise RuntimeError(
            f"artifact SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )


def _run_checked(
    command,
    *,
    cwd: Path | None,
    shell: bool = False,
    env: Mapping[str, str] | None = None,
    timeout_s: int | None = None,
    output_callback: Callable[[str], None] | None = None,
) -> None:
    formatted = _format_command(command)
    LOG.info(
        "worker command started cwd=%s shell=%s command=%s",
        cwd,
        shell,
        formatted,
    )
    try:
        process = subprocess.Popen(
            command,
            cwd=None if cwd is None else str(cwd),
            env=None if env is None else {**os.environ, **env},
            shell=shell,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        raise RuntimeError(f"failed to run command: {command}") from exc

    lines: queue.Queue[str | None] = queue.Queue(maxsize=_COMMAND_OUTPUT_QUEUE_SIZE)

    def read_output() -> None:
        try:
            if process.stdout is not None:
                while True:
                    raw = os.read(process.stdout.fileno(), 4096)
                    if not raw:
                        break
                    chunk = raw.decode("utf-8", errors="replace")
                    lines.put(chunk)
        finally:
            lines.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    output = ""
    pending = ""
    last_emit = time.monotonic()
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    timed_out = False

    def emit() -> None:
        nonlocal pending, last_emit
        detail = pending.strip()
        pending = ""
        last_emit = time.monotonic()
        if not detail:
            return
        LOG.info(
            "worker command output cwd=%s command=%s output=%s",
            cwd,
            formatted,
            detail,
        )
        if output_callback is not None:
            output_callback(detail[-4000:])

    while True:
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
            _kill_command_process(process)
        try:
            line = lines.get(timeout=0.2)
        except queue.Empty:
            if pending and time.monotonic() - last_emit >= 0.5:
                emit()
            if process.poll() is not None and not reader.is_alive():
                break
            continue
        if line is None:
            break
        output = f"{output}\n{line}"[-64_000:]
        pending = f"{pending}\n{line}"[-4000:]
        if time.monotonic() - last_emit >= 0.5:
            emit()

    reader.join(timeout=2)
    while True:
        try:
            line = lines.get_nowait()
        except queue.Empty:
            break
        if line is None:
            continue
        output = f"{output}\n{line}"[-64_000:]
        pending = f"{pending}\n{line}"[-4000:]
    emit()
    try:
        return_code = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _kill_command_process(process)
        return_code = process.wait(timeout=10)
    LOG.info(
        "worker command finished cwd=%s exit_code=%s command=%s",
        cwd,
        return_code,
        formatted,
    )
    if timed_out:
        raise RuntimeError(
            f"command exceeded {timeout_s} seconds: {formatted}\n{output[-8000:].strip()}"
        )
    if return_code != 0:
        raise RuntimeError(
            f"command failed with exit code {return_code}: {formatted}\n{output[-8000:].strip()}"
        )


def _engine_build_jobs() -> int:
    raw = os.environ.get("COPE_ENGINE_BUILD_JOBS", str(_DEFAULT_ENGINE_BUILD_JOBS))
    try:
        return max(1, min(int(raw), 8))
    except ValueError:
        return _DEFAULT_ENGINE_BUILD_JOBS


def _bounded_engine_dockerfile(
    dockerfile: str,
    build_jobs: int,
    engine_name: str,
) -> str:
    lines = dockerfile.splitlines()
    for index, line in enumerate(lines):
        if line.upper().startswith("FROM ") and " AS BUILDER" in line.upper():
            injected = [
                "",
                f"ARG COPE_BUILD_JOBS={build_jobs}",
                "ENV CARGO_BUILD_JOBS=${COPE_BUILD_JOBS} \\",
                "    CMAKE_BUILD_PARALLEL_LEVEL=${COPE_BUILD_JOBS} \\",
                "    GOFLAGS=-p=${COPE_BUILD_JOBS} \\",
                "    MAKEFLAGS=-j${COPE_BUILD_JOBS}",
            ]
            normalized_name = engine_name.strip().casefold()
            if normalized_name == "heimdall" and "ca-certificates git" not in dockerfile:
                injected.extend(
                    [
                        "",
                        "RUN apt-get update \\",
                        "    && apt-get install --no-install-recommends -y ca-certificates git \\",
                        "    && rm -rf /var/lib/apt/lists/*",
                    ]
                )
            if normalized_name == "reckless" and "ca-certificates curl" not in dockerfile:
                injected.extend(
                    [
                        "",
                        "RUN apt-get update \\",
                        "    && apt-get install --no-install-recommends -y ca-certificates curl \\",
                        "    && rm -rf /var/lib/apt/lists/*",
                    ]
                )
            lines[index + 1:index + 1] = injected
            break
    return "\n".join(lines).replace("$(nproc)", "${COPE_BUILD_JOBS}") + "\n"


def _kill_command_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        return


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
