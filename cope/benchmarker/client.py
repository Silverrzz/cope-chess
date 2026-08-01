from __future__ import annotations

import asyncio
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from websockets.client import connect
from websockets.exceptions import ConnectionClosed

from cope.core.benchmark import benchmark_hardware_key, parse_benchmark_nps
from cope.core.models import (
    BenchmarkAssignment,
    BenchmarkFailed,
    BenchmarkProgress,
    BenchmarkerSessionHello,
    BenchmarkerTokenHello,
    BenchmarkerUpdateCommand,
    BenchmarkerUpdateStatus,
    BenchmarkerWelcome,
    BenchmarkResult,
)
from cope.core.protocol import (
    ProtocolValidationError,
    decode_envelope,
    encode_message,
    make_message,
)
from cope.worker.client import _detect_hardware, _detect_machine_id, _restart_arguments
from cope.worker.update import install_client_release
from cope.worker.uci_engine import EnginePreparationError, UciEngineProcess


LOG = logging.getLogger("cope.benchmarker")
RECONNECT_INITIAL_DELAY_S = 1.0
RECONNECT_MAX_DELAY_S = 30.0
OUTPUT_LIMIT = 64_000
PROGRESS_HEARTBEAT_INTERVAL_S = 5.0


@dataclass(frozen=True)
class BenchmarkerClientConfig:
    server_url: str
    app_version: str
    token: str | None = None
    session_id: str | None = None
    label_hint: str = ""
    machine_id: str | None = None
    session_file: Path | None = None


@dataclass
class _ConnectionState:
    session_id: str | None
    connected: bool = False


async def run_benchmarker_client(config: BenchmarkerClientConfig) -> None:
    state = _ConnectionState(
        session_id=config.session_id or (
            _load_session(config.session_file) if config.token is None else None
        )
    )
    reconnect_delay_s = RECONNECT_INITIAL_DELAY_S
    while True:
        state.connected = False
        try:
            restart = await _run_connection(config, state)
            if restart is not None:
                restart_executable, target_commit = restart
                os.environ["COPE_BUILD_VERSION"] = target_commit
                os.environ["COPE_UPDATE_ROOT"] = str(restart_executable.parents[4])
                os.execv(
                    str(restart_executable),
                    [str(restart_executable), *_restart_arguments(sys.argv[1:])],
                )
        except ConnectionClosed as error:
            reason = error.reason or str(error) or error.__class__.__name__
            LOG.warning("benchmark server connection closed code=%s reason=%s", error.code, reason)
        except (OSError, asyncio.TimeoutError) as error:
            LOG.warning("benchmark server connection failed: %s", error)
        except Exception:
            LOG.exception("benchmarker client failed")
            raise

        if state.connected:
            reconnect_delay_s = RECONNECT_INITIAL_DELAY_S
        LOG.info("reconnecting to benchmark server in %.1fs", reconnect_delay_s)
        await asyncio.sleep(reconnect_delay_s)
        reconnect_delay_s = min(reconnect_delay_s * 2, RECONNECT_MAX_DELAY_S)


async def _run_connection(
    config: BenchmarkerClientConfig,
    state: _ConnectionState,
) -> tuple[Path, str] | None:
    connection_config = (
        config
        if state.session_id is None
        else replace(config, token=None, session_id=state.session_id)
    )
    machine_id = connection_config.machine_id or _detect_machine_id()
    hw = _detect_hardware()
    hardware_key = benchmark_hardware_key(machine_id, hw)
    credential_count = sum(
        value is not None
        for value in (connection_config.token, connection_config.session_id)
    )
    if credential_count != 1:
        raise ValueError("benchmarker needs exactly one of token or session_id")
    if connection_config.token is not None:
        hello = BenchmarkerTokenHello(
            token=connection_config.token,
            label_hint=connection_config.label_hint,
            machine_id=machine_id,
            hardware_key=hardware_key,
            hw=hw,
            app_version=connection_config.app_version,
            supports_updates=True,
            supports_progress=True,
        )
    else:
        hello = BenchmarkerSessionHello(
            session_id=connection_config.session_id or "",
            machine_id=machine_id,
            hardware_key=hardware_key,
            hw=hw,
            app_version=connection_config.app_version,
            supports_updates=True,
            supports_progress=True,
        )

    LOG.info(
        "connecting to benchmark server url=%s hardware_key=%s",
        connection_config.server_url,
        hardware_key,
    )
    async with connect(connection_config.server_url, max_size=128_000) as websocket:
        await websocket.send(encode_message(make_message("benchmark_hello", hello)))
        envelope = decode_envelope(await websocket.recv())
        if envelope.type != "benchmark_welcome":
            raise ProtocolValidationError(
                f"expected benchmark_welcome, got {envelope.type}"
            )
        welcome = BenchmarkerWelcome.model_validate(envelope.data)
        state.session_id = welcome.session_id
        _save_session(connection_config.session_file, welcome.session_id)
        state.connected = True
        LOG.info(
            "accepted benchmarker_id=%s hardware_key=%s",
            welcome.benchmarker_id,
            hardware_key,
        )
        if welcome.update is not None:
            return await _apply_benchmarker_update(websocket, welcome.update)
        while True:
            envelope = decode_envelope(await websocket.recv())
            if envelope.type == "benchmarker_update":
                update = BenchmarkerUpdateCommand.model_validate(envelope.data)
                return await _apply_benchmarker_update(websocket, update)
            if envelope.type != "benchmark_assignment":
                raise ProtocolValidationError(
                    f"expected benchmark_assignment, got {envelope.type}"
                )
            progress_supported = "preparation_timeout_s" in envelope.data
            assignment = BenchmarkAssignment.model_validate(envelope.data)
            if assignment.hardware_key != hardware_key:
                raise ProtocolValidationError("benchmark assignment hardware key mismatch")
            message_type, result = await _run_benchmark_with_progress(
                websocket,
                assignment,
                connection_config.server_url,
                welcome.session_id,
                send_progress=progress_supported,
            )
            await websocket.send(encode_message(make_message(message_type, result)))


async def _run_benchmark_with_progress(
    websocket,
    assignment: BenchmarkAssignment,
    server_url: str,
    credential: str,
    *,
    send_progress: bool,
) -> tuple[str, BenchmarkResult | BenchmarkFailed]:
    loop = asyncio.get_running_loop()
    progress_queue: asyncio.Queue[BenchmarkProgress] = asyncio.Queue(maxsize=256)
    last_progress: BenchmarkProgress | None = None
    stage_started_at = time.monotonic()
    last_sent_at = stage_started_at

    def enqueue(progress: BenchmarkProgress) -> None:
        if progress_queue.full():
            progress_queue.get_nowait()
        progress_queue.put_nowait(progress)

    def report(stage: str, substage: str, status: str, detail: str) -> None:
        nonlocal last_progress, stage_started_at
        if (
            last_progress is None
            or last_progress.stage != stage
            or last_progress.substage != substage
        ):
            stage_started_at = time.monotonic()
        progress = BenchmarkProgress(
            job_id=assignment.job_id,
            job_key=assignment.job_key,
            hardware_key=assignment.hardware_key,
            build_hash=assignment.engine.build_hash,
            stage=stage,
            substage=substage,
            status="completed" if status == "completed" else "running",
            detail=detail.strip()[-4000:] or "Progress updated",
        )
        last_progress = progress
        loop.call_soon_threadsafe(enqueue, progress)

    task = asyncio.create_task(
        asyncio.to_thread(
            _run_benchmark,
            assignment,
            server_url,
            credential,
            report,
        )
    )
    while not task.done() or not progress_queue.empty():
        try:
            progress = await asyncio.wait_for(progress_queue.get(), timeout=0.25)
        except asyncio.TimeoutError:
            now = time.monotonic()
            if (
                send_progress
                and not task.done()
                and last_progress is not None
                and last_progress.status == "running"
                and now - last_sent_at >= PROGRESS_HEARTBEAT_INTERVAL_S
            ):
                elapsed = _duration_text(now - stage_started_at)
                heartbeat = last_progress.model_copy(
                    update={
                        "detail": (
                            f"Heartbeat: {last_progress.substage.replace('_', ' ')} "
                            f"has been active for {elapsed}"
                        )
                    }
                )
                await websocket.send(
                    encode_message(make_message("benchmark_progress", heartbeat))
                )
                last_sent_at = now
            continue
        if send_progress:
            await websocket.send(
                encode_message(make_message("benchmark_progress", progress))
            )
            last_sent_at = time.monotonic()
    message_type, result = await task
    if isinstance(result, BenchmarkFailed):
        LOG.warning(
            "benchmark job failed job_id=%s engine=%s stage=%s error=%s output=%s",
            assignment.job_id,
            assignment.engine.name,
            result.stage,
            result.error,
            result.output,
        )
    else:
        LOG.info(
            "benchmark job completed job_id=%s engine=%s nps=%s elapsed_ms=%s",
            assignment.job_id,
            assignment.engine.name,
            result.nps,
            result.elapsed_ms,
        )
    return message_type, result


async def _apply_benchmarker_update(
    websocket,
    update: BenchmarkerUpdateCommand,
) -> tuple[Path, str]:
    status_fields = {
        "job_id": update.job_id,
        "target_commit": update.target_commit,
    }
    await websocket.send(
        encode_message(
            make_message(
                "benchmarker_update_status",
                BenchmarkerUpdateStatus(
                    **status_fields,
                    status="accepted",
                    detail="Benchmarker accepted the deployment.",
                ),
            )
        )
    )
    try:
        await websocket.send(
            encode_message(
                make_message(
                    "benchmarker_update_status",
                    BenchmarkerUpdateStatus(
                        **status_fields,
                        status="installing",
                        detail="Fetching and building the benchmarker release.",
                    ),
                )
            )
        )
        executable = await asyncio.to_thread(
            install_client_release,
            client_name="benchmarker",
            target_commit=update.target_commit,
            repository_url=update.repository_url,
        )
    except Exception as error:
        detail = (str(error).strip() or error.__class__.__name__)[:4000]
        await websocket.send(
            encode_message(
                make_message(
                    "benchmarker_update_status",
                    BenchmarkerUpdateStatus(
                        **status_fields,
                        status="failed",
                        detail=detail,
                    ),
                )
            )
        )
        raise RuntimeError(f"benchmarker update failed: {detail}") from error
    await websocket.send(
        encode_message(
            make_message(
                "benchmarker_update_status",
                BenchmarkerUpdateStatus(
                    **status_fields,
                    status="restarting",
                    detail="Release installed; restarting on the new version.",
                ),
            )
        )
    )
    await websocket.close(code=1000, reason="benchmarker update installed")
    return executable, update.target_commit


def _run_benchmark(
    assignment: BenchmarkAssignment,
    server_url: str,
    credential: str,
    progress_callback,
) -> tuple[str, BenchmarkResult | BenchmarkFailed]:
    progress_callback(
        "setup",
        "assignment",
        "completed",
        f"Accepted benchmark job {assignment.job_id} for {assignment.engine.name}",
    )
    engine = UciEngineProcess(
        assignment.engine,
        server_url=server_url,
        credential=credential,
        progress_callback=progress_callback,
        command_timeout_s=assignment.preparation_timeout_s,
    )
    output = ""
    try:
        try:
            engine.prepare()
        except EnginePreparationError as error:
            return (
                "benchmark_failed",
                BenchmarkFailed(
                    job_id=assignment.job_id,
                    job_key=assignment.job_key,
                    hardware_key=assignment.hardware_key,
                    build_hash=assignment.engine.build_hash,
                    stage="build",
                    error=error.detail[-8000:],
                ),
            )

        progress_callback(
            "benchmark",
            "engine_bench",
            "running",
            f"Running {assignment.engine.name} bench with a {assignment.timeout_s} second limit",
        )
        try:
            return_code, output, elapsed_ms, timed_out = _run_bench_command(
                engine.artifact_path.resolve(),
                cwd=engine.artifact_directory,
                timeout_s=assignment.timeout_s,
                output_callback=lambda detail: progress_callback(
                    "benchmark",
                    "engine_bench",
                    "running",
                    detail,
                ),
            )
        except OSError as error:
            return (
                "benchmark_failed",
                BenchmarkFailed(
                    job_id=assignment.job_id,
                    job_key=assignment.job_key,
                    hardware_key=assignment.hardware_key,
                    build_hash=assignment.engine.build_hash,
                    stage="bench",
                    error=str(error)[-8000:],
                ),
            )
        if timed_out:
            return (
                "benchmark_failed",
                BenchmarkFailed(
                    job_id=assignment.job_id,
                    job_key=assignment.job_key,
                    hardware_key=assignment.hardware_key,
                    build_hash=assignment.engine.build_hash,
                    stage="bench",
                    error=f"engine bench exceeded {assignment.timeout_s} seconds",
                    output=output,
                ),
            )
        if return_code != 0:
            return (
                "benchmark_failed",
                BenchmarkFailed(
                    job_id=assignment.job_id,
                    job_key=assignment.job_key,
                    hardware_key=assignment.hardware_key,
                    build_hash=assignment.engine.build_hash,
                    stage="bench",
                    error=f"engine bench exited with code {return_code}",
                    output=output,
                ),
            )
        nps = parse_benchmark_nps(output)
        if nps is None:
            return (
                "benchmark_failed",
                BenchmarkFailed(
                    job_id=assignment.job_id,
                    job_key=assignment.job_key,
                    hardware_key=assignment.hardware_key,
                    build_hash=assignment.engine.build_hash,
                    stage="parse",
                    error="engine bench output did not contain a positive NPS value",
                    output=output,
                ),
            )
        progress_callback(
            "benchmark",
            "engine_bench",
            "completed",
            f"Completed {assignment.engine.name} bench at {nps:,} NPS in {elapsed_ms / 1000:.1f} seconds",
        )
        return (
            "benchmark_result",
            BenchmarkResult(
                job_id=assignment.job_id,
                job_key=assignment.job_key,
                hardware_key=assignment.hardware_key,
                build_hash=assignment.engine.build_hash,
                nps=nps,
                elapsed_ms=elapsed_ms,
                output=output,
            ),
        )
    finally:
        engine.close()


def _trim_output(output: str) -> str:
    return output[-OUTPUT_LIMIT:]


def _run_bench_command(
    executable: Path,
    *,
    cwd: Path,
    timeout_s: int,
    output_callback,
) -> tuple[int, str, int, bool]:
    started = time.monotonic()
    process = subprocess.Popen(
        [str(executable), "bench"],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    lines.put(line.rstrip("\r\n"))
        finally:
            lines.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    output = ""
    pending: list[str] = []
    pending_length = 0
    last_emit = time.monotonic()
    timed_out = False

    def emit() -> None:
        nonlocal pending, pending_length, last_emit
        detail = "\n".join(pending).strip()
        pending = []
        pending_length = 0
        last_emit = time.monotonic()
        if detail:
            output_callback(detail[-4000:])

    while True:
        if not timed_out and time.monotonic() - started >= timeout_s:
            timed_out = True
            process.kill()
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
        output = _trim_output(f"{output}\n{line}")
        pending.append(line)
        pending_length += len(line) + 1
        if pending_length >= 3000 or time.monotonic() - last_emit >= 0.5:
            emit()

    reader.join(timeout=2)
    while True:
        try:
            line = lines.get_nowait()
        except queue.Empty:
            break
        if line is None:
            continue
        output = _trim_output(f"{output}\n{line}")
        pending.append(line)
    emit()
    return_code = process.wait()
    elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
    return return_code, output.strip(), elapsed_ms, timed_out


def _duration_text(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, remaining = divmod(total, 60)
    if minutes:
        return f"{minutes}m {remaining:02d}s"
    return f"{remaining}s"


def _load_session(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        value = path.expanduser().read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(f"could not read benchmarker session file: {error}") from error
    return value or None


def _save_session(path: Path | None, session_id: str) -> None:
    if path is None:
        return
    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(session_id + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(target)
