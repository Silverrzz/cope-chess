from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import platform
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from websockets.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from cope.core.models import (
    AssignmentCleanupComplete,
    AssignmentComplete,
    AssignmentFailed,
    AssignmentProgress,
    AssignmentReady,
    BenchInfo,
    EngineClock,
    EngineCommand,
    EngineCommandStarted,
    EngineCommandResult,
    EngineHardwareScore,
    EngineInfo,
    EngineSpec,
    EngineStop,
    HardwareInfo,
    ToolJobAssignment,
    ToolJobComplete,
    ToolJobEngineResult,
    ToolJobFailed,
    ToolJobProgress,
    ToolJobPuzzleResult,
    WorkerGameAssignment,
    WorkerResourceTelemetry,
    WorkerSessionHello,
    WorkerTokenHello,
    WorkerUpdateCommand,
    WorkerUpdateStatus,
    WorkerWelcome,
    WorkerResources,
)
from cope.core.protocol import (
    ProtocolValidationError,
    decode_envelope,
    encode_message,
    make_message,
)
from cope.core.stream import (
    clamp_uci_info_line,
    is_full_uci_info_line,
    worker_command_elapsed_line,
)
from cope.worker.update import install_worker_release

from .uci_engine import (
    EnginePreparationError,
    UciEngineProcess,
    discover_worker_local_engine_keys,
)


LOG = logging.getLogger("cope.worker")
RECONNECT_INITIAL_DELAY_S = 1.0
RECONNECT_MAX_DELAY_S = 30.0
ENGINE_INFO_SEND_INTERVAL_S = 0.5
ENGINE_CLOCK_SEND_INTERVAL_S = 0.5
ENGINE_BENCHMARK_CONCURRENCY = 2
TELEMETRY_BATCH_INTERVAL_S = 0.25
TELEMETRY_BATCH_MAX_MESSAGES = 128
RESOURCE_TELEMETRY_INTERVAL_S = 2.0


@dataclass(frozen=True)
class WorkerClientConfig:
    server_url: str
    app_version: str
    token: str | None = None
    session_id: str | None = None
    label_hint: str = ""
    machine_id: str | None = None
    state_file: Path | None = None
    cpu_capacity: int | None = None


class _EngineBenchmarkCache:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(ENGINE_BENCHMARK_CONCURRENCY)
        self._tasks: dict[str, asyncio.Task[tuple[int, int]]] = {}

    async def benchmark(
        self,
        engine: UciEngineProcess,
        spec,
        timeout_s: int,
    ) -> tuple[int, int]:
        key = spec.build_hash
        async with self._lock:
            task = self._tasks.get(key)
            if task is None:
                task = asyncio.create_task(self._run(engine, timeout_s))
                self._tasks[key] = task
        try:
            return await asyncio.shield(task)
        except Exception:
            async with self._lock:
                if self._tasks.get(key) is task:
                    self._tasks.pop(key, None)
            raise

    async def _run(
        self,
        engine: UciEngineProcess,
        timeout_s: int,
    ) -> tuple[int, int]:
        async with self._semaphore:
            return await asyncio.to_thread(engine.benchmark, timeout_s)


class _WorkerTelemetryBatcher:
    def __init__(self, websocket, send_lock: asyncio.Lock, fatal: asyncio.Future) -> None:
        self._websocket = websocket
        self._send_lock = send_lock
        self._pending: dict[tuple[str, int, int], dict[str, Any]] = {}
        self._closing = False
        self._task = asyncio.create_task(self._run(), name="worker-telemetry-batcher")

        def completed(task: asyncio.Task) -> None:
            if task.cancelled() or fatal.done() or self._closing:
                return
            error = task.exception()
            if error is not None:
                fatal.set_exception(error)

        self._task.add_done_callback(completed)

    def offer(self, message_type: str, data) -> None:
        if self._closing:
            return
        key = (message_type, data.assignment_id, data.engine_id)
        clock_key = ("engine_clock", data.assignment_id, data.engine_id)
        info_key = ("engine_info", data.assignment_id, data.engine_id)
        if message_type == "engine_info":
            self._pending.pop(clock_key, None)
        elif message_type == "engine_clock" and info_key in self._pending:
            return
        self._pending[key] = make_message(message_type, data).model_dump(mode="json")

    def discard(self, assignment_id: int, engine_id: int) -> None:
        self._pending.pop(("engine_clock", assignment_id, engine_id), None)
        self._pending.pop(("engine_info", assignment_id, engine_id), None)

    async def close(self) -> None:
        self._closing = True
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        await self.flush()

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(TELEMETRY_BATCH_INTERVAL_S)
            await self.flush()

    async def flush(self) -> None:
        messages = list(self._pending.values())
        self._pending.clear()
        for offset in range(0, len(messages), TELEMETRY_BATCH_MAX_MESSAGES):
            await _send_message(
                self._websocket,
                "worker_telemetry_batch",
                {"messages": messages[offset : offset + TELEMETRY_BATCH_MAX_MESSAGES]},
                lock=self._send_lock,
            )


class _WorkerResourceSampler:
    def __init__(self) -> None:
        import psutil

        self._psutil = psutil
        self._process = psutil.Process()
        self._children: dict[int, Any] = {}
        self._cpu_ids = _process_cpu_ids()
        psutil.cpu_percent(percpu=True)
        self._process.cpu_percent()

    def sample(self) -> WorkerResourceTelemetry:
        per_cpu = self._psutil.cpu_percent(percpu=True)
        visible_cpu = [
            value
            for index, value in enumerate(per_cpu)
            if self._cpu_ids is None or index in self._cpu_ids
        ]
        cpu_percent = sum(visible_cpu) / len(visible_cpu) if visible_cpu else 0.0

        virtual_memory = self._psutil.virtual_memory()
        memory_total = float(virtual_memory.total)
        memory_used = float(virtual_memory.used)
        memory_available = float(virtual_memory.available)
        cgroup_limit = _linux_memory_limit_bytes()
        cgroup_used = _linux_memory_used_bytes()
        if (
            cgroup_limit is not None
            and cgroup_used is not None
            and cgroup_limit < memory_total
        ):
            memory_total = float(cgroup_limit)
            memory_used = float(min(cgroup_used, cgroup_limit))
            memory_available = max(0.0, memory_total - memory_used)

        active_children = {}
        with contextlib.suppress(Exception):
            for child in self._process.children(recursive=True):
                process = self._children.get(child.pid, child)
                active_children[child.pid] = process
        self._children = active_children

        coordinator_cpu = 0.0
        coordinator_memory = 0.0
        with contextlib.suppress(Exception):
            coordinator_cpu = self._process.cpu_percent() / 100.0
            coordinator_memory = float(self._process.memory_info().rss)

        engine_cpu = 0.0
        engine_memory = 0.0
        for child in self._children.values():
            with contextlib.suppress(Exception):
                engine_cpu += child.cpu_percent() / 100.0
                engine_memory += float(child.memory_info().rss)

        disk = self._psutil.disk_usage(str(Path.cwd()))
        mb = float(1024**2)
        return WorkerResourceTelemetry(
            cpu_percent=round(cpu_percent, 3),
            memory_used_mb=round(memory_used / mb, 3),
            memory_total_mb=round(memory_total / mb, 3),
            memory_available_mb=round(memory_available / mb, 3),
            coordinator_cpu_cores=round(coordinator_cpu, 4),
            coordinator_memory_mb=round(coordinator_memory / mb, 3),
            engine_cpu_cores=round(engine_cpu, 4),
            engine_memory_mb=round(engine_memory / mb, 3),
            disk_used_mb=round(float(disk.used) / mb, 3),
            disk_free_mb=round(float(disk.free) / mb, 3),
            disk_total_mb=round(float(disk.total) / mb, 3),
            worker_local_engine_keys=discover_worker_local_engine_keys(),
        )


class _WorkerResourceTelemetryPublisher:
    def __init__(self, websocket, send_lock: asyncio.Lock, fatal: asyncio.Future) -> None:
        self._websocket = websocket
        self._send_lock = send_lock
        self._sampler = _WorkerResourceSampler()
        self._closing = False
        self._task = asyncio.create_task(self._run(), name="worker-resource-telemetry")

        def completed(task: asyncio.Task) -> None:
            if task.cancelled() or fatal.done() or self._closing:
                return
            error = task.exception()
            if error is not None:
                fatal.set_exception(error)

        self._task.add_done_callback(completed)

    async def close(self) -> None:
        self._closing = True
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def _run(self) -> None:
        while True:
            try:
                telemetry = await asyncio.to_thread(self._sampler.sample)
            except Exception as error:
                LOG.warning("worker resource sampling failed: %s", error)
                await asyncio.sleep(RESOURCE_TELEMETRY_INTERVAL_S)
                continue
            await _send_message(
                self._websocket,
                "worker_resource_telemetry",
                telemetry,
                lock=self._send_lock,
            )
            await asyncio.sleep(RESOURCE_TELEMETRY_INTERVAL_S)


async def run_worker_client(config: WorkerClientConfig) -> None:
    hw = _detect_hardware(cpu_capacity=config.cpu_capacity)
    try:
        threading.stack_size(1024 * 1024)
    except (RuntimeError, ValueError):
        LOG.warning("could not reduce worker thread stack size")
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(
            max_workers=max(32, hw.logical_cores * 2 + 8),
            thread_name_prefix="cope-worker",
        )
    )
    state = _WorkerConnectionState(
        session_id=config.session_id or _read_worker_session(config.state_file),
        hw=hw,
    )
    reconnect_delay_s = RECONNECT_INITIAL_DELAY_S
    while True:
        state.connected = False
        try:
            restart = await _run_worker_connection(config, state)
            if restart is not None:
                restart_executable, target_commit = restart
                os.environ["COPE_BUILD_VERSION"] = target_commit
                os.environ["COPE_UPDATE_ROOT"] = str(restart_executable.parents[4])
                os.execv(
                    str(restart_executable),
                    [str(restart_executable), *_restart_arguments(sys.argv[1:])],
                )
        except ConnectionClosed as error:
            _log_connection_closed(error)
        except (OSError, asyncio.TimeoutError, InvalidHandshake) as error:
            LOG.warning("runner connection failed: %s", error)
        except ProtocolValidationError as error:
            LOG.warning("runner protocol error; reconnecting: %s", error)
        except Exception:
            LOG.exception("worker client failed")
            raise

        if state.connected:
            reconnect_delay_s = RECONNECT_INITIAL_DELAY_S
        LOG.info("reconnecting to runner in %.1fs", reconnect_delay_s)
        await asyncio.sleep(reconnect_delay_s)
        reconnect_delay_s = min(reconnect_delay_s * 2, RECONNECT_MAX_DELAY_S)


@dataclass
class _WorkerConnectionState:
    session_id: str | None
    hw: HardwareInfo
    connected: bool = False


async def _run_worker_connection(
    config: WorkerClientConfig,
    state: _WorkerConnectionState,
) -> tuple[Path, str] | None:
    connection_config = _connection_config(config, state)
    LOG.info(
        "connecting to runner url=%s app_version=%s",
        connection_config.server_url,
        connection_config.app_version,
    )
    async with connect(
        connection_config.server_url,
        ping_interval=10,
        ping_timeout=60,
        max_size=8 * 1024 * 1024,
        close_timeout=5,
        max_queue=256,
    ) as websocket:
        await _send_message(websocket, "hello", _build_hello(connection_config, state.hw))
        welcome = await _recv_message(websocket, "welcome", WorkerWelcome)
        state.session_id = welcome.session_id
        _write_worker_session(config.state_file, welcome.session_id)
        state.connected = True
        LOG.info(
            "accepted by runner worker_id=%s session=%s",
            welcome.worker_id,
            _redact_secret(welcome.session_id),
        )
        if welcome.update is not None:
            return await _apply_worker_update(websocket, welcome.update)
        return await _serve_assignments(
            websocket,
            capacity=welcome.capacity,
            server_url=connection_config.server_url,
            credential=welcome.session_id,
        )


def _connection_config(
    config: WorkerClientConfig,
    state: _WorkerConnectionState,
) -> WorkerClientConfig:
    """Use the durable server session after the first accepted connection."""
    if state.session_id is None:
        return config
    return replace(
        config,
        token=None,
        session_id=state.session_id,
    )


def _restart_arguments(arguments: list[str]) -> list[str]:
    result: list[str] = []
    skip_value = False
    for argument in arguments:
        if skip_value:
            skip_value = False
            continue
        if argument == "--app-version":
            skip_value = True
            continue
        if argument.startswith("--app-version="):
            continue
        result.append(argument)
    return result


def _build_hello(
    config: WorkerClientConfig,
    hw: HardwareInfo,
) -> WorkerTokenHello | WorkerSessionHello:
    credential_count = sum(
        value is not None
        for value in (config.token, config.session_id)
    )
    if credential_count != 1:
        raise ValueError(
            "worker client needs exactly one of token or session_id"
        )

    machine_id = config.machine_id or _detect_machine_id()
    worker_local_engine_keys = discover_worker_local_engine_keys()

    if config.token is not None:
        return WorkerTokenHello(
            token=config.token,
            label_hint=config.label_hint,
            hw=hw,
            app_version=config.app_version,
            machine_id=machine_id,
            worker_local_engine_keys=worker_local_engine_keys,
        )

    return WorkerSessionHello(
        session_id=config.session_id or "",
        hw=hw,
        app_version=config.app_version,
        machine_id=machine_id,
        worker_local_engine_keys=worker_local_engine_keys,
    )


def _read_worker_session(path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        return None
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("worker state file has no valid session id")
    return session_id


def _write_worker_session(path: Path | None, session_id: str) -> None:
    if path is None:
        return
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = resolved.with_name(f".{resolved.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps({"session_id": session_id}) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, resolved)
        os.chmod(resolved, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


async def _apply_worker_update(
    websocket,
    update: WorkerUpdateCommand,
) -> tuple[Path, str]:
    status_fields = {
        "job_id": update.job_id,
        "target_commit": update.target_commit,
    }
    await _send_message(
        websocket,
        "worker_update_status",
        WorkerUpdateStatus(
            **status_fields,
            status="accepted",
            detail="Worker accepted the deployment.",
        ),
    )
    try:
        await _send_message(
            websocket,
            "worker_update_status",
            WorkerUpdateStatus(
                **status_fields,
                status="installing",
                detail="Fetching and building the worker release.",
            ),
        )
        executable = await asyncio.to_thread(
            install_worker_release,
            target_commit=update.target_commit,
            repository_url=update.repository_url,
        )
    except Exception as error:
        detail = (str(error).strip() or error.__class__.__name__)[:4000]
        await _send_message(
            websocket,
            "worker_update_status",
            WorkerUpdateStatus(
                **status_fields,
                status="failed",
                detail=detail,
            ),
        )
        raise RuntimeError(f"worker update failed: {detail}") from error
    await _send_message(
        websocket,
        "worker_update_status",
        WorkerUpdateStatus(
            **status_fields,
            status="restarting",
            detail="Release installed; restarting on the new version.",
        ),
    )
    await websocket.close(code=1000, reason="worker update installed")
    return executable, update.target_commit


async def _serve_assignments(
    websocket,
    *,
    capacity: WorkerResources,
    server_url: str,
    credential: str,
) -> tuple[Path, str] | None:
    queues: dict[int, asyncio.Queue] = {}
    assignments: dict[int, WorkerGameAssignment] = {}
    tasks: dict[int, asyncio.Task] = {}
    tool_jobs: dict[int, ToolJobAssignment] = {}
    tool_tasks: dict[int, asyncio.Task] = {}
    send_lock = asyncio.Lock()
    benchmark_cache = _EngineBenchmarkCache()
    fatal = asyncio.get_running_loop().create_future()
    telemetry = _WorkerTelemetryBatcher(websocket, send_lock, fatal)
    resource_telemetry: _WorkerResourceTelemetryPublisher | None = None
    try:
        resource_telemetry = _WorkerResourceTelemetryPublisher(
            websocket,
            send_lock,
            fatal,
        )
    except Exception as error:
        LOG.warning("worker resource telemetry is unavailable: %s", error)

    def assignment_done(assignment_id: int, task: asyncio.Task) -> None:
        tasks.pop(assignment_id, None)
        queues.pop(assignment_id, None)
        assignments.pop(assignment_id, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None and not fatal.done():
            fatal.set_exception(error)

    def tool_job_done(job_id: int, task: asyncio.Task) -> None:
        tool_tasks.pop(job_id, None)
        tool_jobs.pop(job_id, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None and not fatal.done():
            fatal.set_exception(error)

    try:
        return await _route_assignment_messages(
            websocket,
            capacity=capacity,
            server_url=server_url,
            credential=credential,
            queues=queues,
            assignments=assignments,
            tasks=tasks,
            tool_jobs=tool_jobs,
            tool_tasks=tool_tasks,
            send_lock=send_lock,
            benchmark_cache=benchmark_cache,
            telemetry=telemetry,
            fatal=fatal,
            assignment_done=assignment_done,
            tool_job_done=tool_job_done,
        )
    finally:
        active_tasks = (*tasks.values(), *tool_tasks.values())
        for task in active_tasks:
            task.cancel()
        await asyncio.gather(*active_tasks, return_exceptions=True)
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await telemetry.close()
        if resource_telemetry is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await resource_telemetry.close()
        if fatal.done():
            with contextlib.suppress(Exception):
                fatal.exception()


async def _route_assignment_messages(
    websocket,
    *,
    capacity: WorkerResources,
    server_url: str,
    credential: str,
    queues: dict[int, asyncio.Queue],
    assignments: dict[int, WorkerGameAssignment],
    tasks: dict[int, asyncio.Task],
    tool_jobs: dict[int, ToolJobAssignment],
    tool_tasks: dict[int, asyncio.Task],
    send_lock: asyncio.Lock,
    benchmark_cache: _EngineBenchmarkCache,
    telemetry: _WorkerTelemetryBatcher,
    fatal: asyncio.Future,
    assignment_done,
    tool_job_done,
) -> tuple[Path, str] | None:
    while True:
        receive = asyncio.create_task(_recv_envelope(websocket))
        done, _pending = await asyncio.wait(
            {receive, fatal},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if fatal in done:
            receive.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await receive
            fatal.result()

        envelope = receive.result()
        if envelope.type == "worker_update":
            if tasks or tool_tasks:
                raise ProtocolValidationError("runner requested an update during active work")
            update = WorkerUpdateCommand.model_validate(envelope.data)
            return await _apply_worker_update(websocket, update)
        if envelope.type == "assignment":
            assignment = WorkerGameAssignment.model_validate(envelope.data)
            assignment_id = assignment.assignment.assignment_id
            if assignment_id in tasks:
                current = assignments[assignment_id]
                duplicate_identity = (
                    current.assignment.assignment_key
                    == assignment.assignment.assignment_key
                    and current.assignment.game_id == assignment.assignment.game_id
                )
                reason = (
                    f"duplicate assignment {assignment_id}"
                    if duplicate_identity
                    else f"conflicting assignment reuse {assignment_id}"
                )
                LOG.warning(
                    "%s; reconnecting without stopping the worker process",
                    reason,
                )
                await websocket.close(code=4000, reason=reason)
                return None
            used_threads = sum(
                item.required_resources.threads for item in assignments.values()
            )
            used_hash_mb = sum(
                item.required_resources.hash_mb for item in assignments.values()
            )
            required = assignment.required_resources
            if (
                used_threads + required.threads > capacity.threads
                or used_hash_mb + required.hash_mb > capacity.hash_mb
            ):
                raise ProtocolValidationError(
                    f"assignment {assignment_id} exceeds remaining machine capacity"
                )
            queue: asyncio.Queue = asyncio.Queue()
            queues[assignment_id] = queue
            assignments[assignment_id] = assignment
            task = asyncio.create_task(
                _serve_assignment(
                    websocket,
                    assignment,
                    inbox=queue,
                    send_lock=send_lock,
                    benchmark_cache=benchmark_cache,
                    telemetry=telemetry,
                    server_url=server_url,
                    credential=credential,
                ),
                name=f"worker-assignment-{assignment_id}",
            )
            tasks[assignment_id] = task
            task.add_done_callback(
                lambda completed, value=assignment_id: assignment_done(value, completed)
            )
            continue

        if envelope.type == "tool_job":
            job = ToolJobAssignment.model_validate(envelope.data)
            if tasks or tool_tasks:
                raise ProtocolValidationError("tool job requested during active work")
            tool_jobs[job.job_id] = job
            task = asyncio.create_task(
                _serve_tool_job(
                    websocket,
                    job,
                    send_lock=send_lock,
                    server_url=server_url,
                    credential=credential,
                    capacity=capacity,
                ),
                name=f"worker-tool-job-{job.job_id}",
            )
            tool_tasks[job.job_id] = task
            task.add_done_callback(
                lambda completed, value=job.job_id: tool_job_done(value, completed)
            )
            continue

        assignment_id = envelope.data.get("assignment_id")
        if not isinstance(assignment_id, int):
            raise ProtocolValidationError(
                f"{envelope.type} message has no assignment id"
            )
        queue = queues.get(assignment_id)
        if queue is None:
            raise ProtocolValidationError(
                f"{envelope.type} references inactive assignment {assignment_id}"
            )
        await queue.put(envelope)


async def _serve_tool_job(
    websocket,
    job: ToolJobAssignment,
    *,
    send_lock: asyncio.Lock,
    server_url: str,
    credential: str,
    capacity: WorkerResources,
) -> None:
    if job.tool_name in {"puzzle_suite_uniqueness", "puzzle_suite_difficulty"}:
        await _serve_puzzle_suite_job(
            websocket,
            job,
            send_lock=send_lock,
            server_url=server_url,
            credential=credential,
            capacity=capacity,
        )
        return
    if job.tool_name != "who_has_this":
        await _send_message(
            websocket,
            "tool_job_failed",
            ToolJobFailed(
                job_id=job.job_id,
                job_key=job.job_key,
                error=f"unsupported tool {job.tool_name}",
            ),
            lock=send_lock,
        )
        return
    option_name = str(job.input.get("option_name", "")).strip()
    if not option_name:
        await _send_message(
            websocket,
            "tool_job_failed",
            ToolJobFailed(
                job_id=job.job_id,
                job_key=job.job_key,
                error="the tool job has no UCI option name",
            ),
            lock=send_lock,
        )
        return
    total = len(job.engines)
    for current, spec in enumerate(job.engines, start=1):
        await _send_message(
            websocket,
            "tool_job_progress",
            ToolJobProgress(
                job_id=job.job_id,
                job_key=job.job_key,
                engine_id=spec.engine_id,
                status="running",
                detail=f"Inspecting {spec.name} {spec.version}",
                current=current - 1,
                total=total,
            ),
            lock=send_lock,
        )
        started_ns = time.monotonic_ns()
        engine = UciEngineProcess(
            spec,
            server_url=server_url,
            credential=credential,
            command_timeout_s=60,
            allow_build=True,
        )
        try:
            await asyncio.to_thread(engine.prepare)
            lines = await asyncio.to_thread(engine.handle_command, "uci")
            matched_name, option_line = _find_uci_option(lines, option_name)
            status = "supported" if matched_name else "unsupported"
            result = ToolJobEngineResult(
                job_id=job.job_id,
                job_key=job.job_key,
                engine_id=spec.engine_id,
                status=status,
                matched_name=matched_name,
                option_line=option_line,
                elapsed_ms=max(0, round((time.monotonic_ns() - started_ns) / 1_000_000)),
            )
        except Exception as error:
            result = ToolJobEngineResult(
                job_id=job.job_id,
                job_key=job.job_key,
                engine_id=spec.engine_id,
                status="failed",
                error=(str(error).strip() or error.__class__.__name__)[-8000:],
                elapsed_ms=max(0, round((time.monotonic_ns() - started_ns) / 1_000_000)),
            )
        finally:
            await asyncio.to_thread(engine.close)
        await _send_message(websocket, "tool_job_engine_result", result, lock=send_lock)
        await _send_message(
            websocket,
            "tool_job_progress",
            ToolJobProgress(
                job_id=job.job_id,
                job_key=job.job_key,
                engine_id=spec.engine_id,
                status="completed",
                detail=f"Inspected {spec.name} {spec.version}",
                current=current,
                total=total,
            ),
            lock=send_lock,
        )
    await _send_message(
        websocket,
        "tool_job_complete",
        ToolJobComplete(job_id=job.job_id, job_key=job.job_key),
        lock=send_lock,
    )


async def _serve_puzzle_suite_job(
    websocket,
    job: ToolJobAssignment,
    *,
    send_lock: asyncio.Lock,
    server_url: str,
    credential: str,
    capacity: WorkerResources,
) -> None:
    stage = str(job.input.get("stage") or "")
    expected_stage = "uniqueness" if job.tool_name == "puzzle_suite_uniqueness" else "difficulty"
    puzzles = job.input.get("puzzles")
    if stage != expected_stage or not isinstance(puzzles, list) or not puzzles:
        await _send_message(
            websocket,
            "tool_job_failed",
            ToolJobFailed(
                job_id=job.job_id,
                job_key=job.job_key,
                error="the puzzle suite assignment is incomplete",
            ),
            lock=send_lock,
        )
        return
    movetime_ms = _positive_job_integer(job.input, "movetime_ms")
    threads = _positive_job_integer(job.input, "threads")
    hash_mb = _positive_job_integer(job.input, "hash_mb")
    multipv = _positive_job_integer(job.input, "multipv") if stage == "uniqueness" else 1
    min_gap = float(job.input.get("min_sigmoid_gap", 0.15))
    total = int(job.input.get("progress_total") or len(puzzles) * len(job.engines))
    current = int(job.input.get("progress_offset") or 0)
    if stage == "difficulty":
        await _serve_puzzle_suite_difficulty_job(
            websocket,
            job,
            puzzles=puzzles,
            movetime_ms=movetime_ms,
            threads=threads,
            hash_mb=hash_mb,
            min_gap=min_gap,
            total=total,
            current=current,
            send_lock=send_lock,
            server_url=server_url,
            credential=credential,
            capacity=capacity,
        )
        return
    for spec in job.engines:
        engine = UciEngineProcess(
            spec,
            server_url=server_url,
            credential=credential,
            command_timeout_s=max(60, round(movetime_ms / 1000) + 30),
            allow_build=True,
        )
        engine_error = ""
        engine_status = "supported"
        clear_hash = False
        started_ns = time.monotonic_ns()
        await _send_message(
            websocket,
            "tool_job_progress",
            ToolJobProgress(
                job_id=job.job_id,
                job_key=job.job_key,
                engine_id=spec.engine_id,
                status="running",
                detail=f"Preparing {spec.name} {spec.version}",
                current=current,
                total=total,
            ),
            lock=send_lock,
        )
        try:
            await asyncio.to_thread(engine.prepare)
            uci_lines = await asyncio.to_thread(engine.handle_command, "uci")
            clear_hash = "clear_hash" in _uci_option_names(uci_lines)
            missing = _configure_puzzle_engine(
                engine,
                spec_options=spec.uci_options,
                uci_lines=uci_lines,
                stage=stage,
                threads=threads,
                hash_mb=hash_mb,
                multipv=multipv,
            )
            if missing:
                engine_status = "unsupported"
                engine_error = missing
            else:
                await asyncio.to_thread(engine.handle_command, "isready")
        except Exception as error:
            engine_status = "failed"
            engine_error = (str(error).strip() or error.__class__.__name__)[-8000:]
        for puzzle in puzzles:
            puzzle_id = puzzle.get("id") if isinstance(puzzle, dict) else None
            if not isinstance(puzzle_id, int) or puzzle_id <= 0:
                await _send_message(
                    websocket,
                    "tool_job_failed",
                    ToolJobFailed(
                        job_id=job.job_id,
                        job_key=job.job_key,
                        error="the puzzle suite assignment contains an invalid puzzle id",
                    ),
                    lock=send_lock,
                )
                await asyncio.to_thread(engine.close)
                return
            detail = f"{spec.name} - puzzle {current + 1} of {total}"
            await _send_message(
                websocket,
                "tool_job_progress",
                ToolJobProgress(
                    job_id=job.job_id,
                    job_key=job.job_key,
                    engine_id=spec.engine_id,
                    status="running",
                    detail=detail,
                    current=current,
                    total=total,
                ),
                lock=send_lock,
            )
            if engine_error:
                result = ToolJobPuzzleResult(
                    job_id=job.job_id,
                    job_key=job.job_key,
                    engine_id=spec.engine_id,
                    puzzle_id=puzzle_id,
                    stage=stage,
                    status="failed",
                    time_ms=0,
                    error=engine_error,
                )
            else:
                try:
                    result = await asyncio.to_thread(
                        _search_puzzle,
                        engine,
                        job,
                        spec.engine_id,
                        puzzle,
                        stage=stage,
                        movetime_ms=movetime_ms,
                        min_gap=min_gap,
                        clear_hash=clear_hash,
                    )
                except Exception as error:
                    result = ToolJobPuzzleResult(
                        job_id=job.job_id,
                        job_key=job.job_key,
                        engine_id=spec.engine_id,
                        puzzle_id=puzzle_id,
                        stage=stage,
                        status="failed",
                        time_ms=0,
                        error=(str(error).strip() or error.__class__.__name__)[-8000:],
                    )
            await _send_message(websocket, "tool_job_puzzle_result", result, lock=send_lock)
            current += 1
        await asyncio.to_thread(engine.close)
        await _send_message(
            websocket,
            "tool_job_engine_result",
            ToolJobEngineResult(
                job_id=job.job_id,
                job_key=job.job_key,
                engine_id=spec.engine_id,
                status=engine_status,
                error=engine_error,
                elapsed_ms=max(0, round((time.monotonic_ns() - started_ns) / 1_000_000)),
            ),
            lock=send_lock,
        )
        await _send_message(
            websocket,
            "tool_job_progress",
            ToolJobProgress(
                job_id=job.job_id,
                job_key=job.job_key,
                engine_id=spec.engine_id,
                status="completed",
                detail=f"Finished {spec.name} {spec.version}",
                current=current,
                total=total,
            ),
            lock=send_lock,
        )
    await _send_message(
        websocket,
        "tool_job_complete",
        ToolJobComplete(job_id=job.job_id, job_key=job.job_key),
        lock=send_lock,
    )


async def _serve_puzzle_suite_difficulty_job(
    websocket,
    job: ToolJobAssignment,
    *,
    puzzles: list[Any],
    movetime_ms: int,
    threads: int,
    hash_mb: int,
    min_gap: float,
    total: int,
    current: int,
    send_lock: asyncio.Lock,
    server_url: str,
    credential: str,
    capacity: WorkerResources,
) -> None:
    if any(
        not isinstance(puzzle, dict)
        or not isinstance(puzzle.get("id"), int)
        or puzzle["id"] <= 0
        for puzzle in puzzles
    ):
        await _send_message(
            websocket,
            "tool_job_failed",
            ToolJobFailed(
                job_id=job.job_id,
                job_key=job.job_key,
                error="the puzzle suite assignment contains an invalid puzzle id",
            ),
            lock=send_lock,
        )
        return
    search_count = len(puzzles) * len(job.engines)
    parallelism = min(
        search_count,
        max(1, capacity.threads // threads),
        max(1, capacity.hash_mb // hash_mb),
    )
    engine_count = len(job.engines)
    if parallelism < engine_count:
        slot_counts = [1] * engine_count
    else:
        slots_per_engine, extra_slots = divmod(parallelism, engine_count)
        slot_counts = [
            min(len(puzzles), slots_per_engine + (1 if index < extra_slots else 0))
            for index in range(engine_count)
        ]
    semaphore = asyncio.Semaphore(parallelism)
    progress_lock = asyncio.Lock()
    progress_current = [current]
    await asyncio.gather(
        *(
            _serve_puzzle_difficulty_engine(
                websocket,
                job,
                spec,
                puzzles=puzzles,
                slot_count=slot_counts[index],
                parallelism=parallelism,
                movetime_ms=movetime_ms,
                threads=threads,
                hash_mb=hash_mb,
                min_gap=min_gap,
                total=total,
                progress_current=progress_current,
                progress_lock=progress_lock,
                semaphore=semaphore,
                send_lock=send_lock,
                server_url=server_url,
                credential=credential,
            )
            for index, spec in enumerate(job.engines)
        )
    )
    await _send_message(
        websocket,
        "tool_job_complete",
        ToolJobComplete(job_id=job.job_id, job_key=job.job_key),
        lock=send_lock,
    )


async def _serve_puzzle_difficulty_engine(
    websocket,
    job: ToolJobAssignment,
    spec: EngineSpec,
    *,
    puzzles: list[Any],
    slot_count: int,
    parallelism: int,
    movetime_ms: int,
    threads: int,
    hash_mb: int,
    min_gap: float,
    total: int,
    progress_current: list[int],
    progress_lock: asyncio.Lock,
    semaphore: asyncio.Semaphore,
    send_lock: asyncio.Lock,
    server_url: str,
    credential: str,
) -> None:
    started_ns = time.monotonic_ns()
    await _send_puzzle_difficulty_progress(
        websocket,
        job,
        engine_id=spec.engine_id,
        status="running",
        detail=(
            f"Preparing {spec.name}: {slot_count} engine slots, "
            f"{parallelism} worker-wide"
        ),
        total=total,
        progress_current=progress_current,
        progress_lock=progress_lock,
        send_lock=send_lock,
    )
    shards = [puzzles[index::slot_count] for index in range(slot_count)]
    slot_results = await asyncio.gather(
        *(
            _serve_puzzle_difficulty_slot(
                websocket,
                job,
                spec,
                puzzles=shard,
                slot_number=index + 1,
                slot_count=slot_count,
                movetime_ms=movetime_ms,
                threads=threads,
                hash_mb=hash_mb,
                min_gap=min_gap,
                total=total,
                progress_current=progress_current,
                progress_lock=progress_lock,
                semaphore=semaphore,
                send_lock=send_lock,
                server_url=server_url,
                credential=credential,
            )
            for index, shard in enumerate(shards)
        )
    )
    statuses = {status for status, _ in slot_results}
    if "failed" in statuses:
        engine_status = "failed"
    elif "unsupported" in statuses:
        engine_status = "unsupported"
    else:
        engine_status = "supported"
    errors = list(dict.fromkeys(error for _, error in slot_results if error))
    engine_error = "\n".join(errors)[-8000:]
    await _send_message(
        websocket,
        "tool_job_engine_result",
        ToolJobEngineResult(
            job_id=job.job_id,
            job_key=job.job_key,
            engine_id=spec.engine_id,
            status=engine_status,
            error=engine_error,
            elapsed_ms=max(0, round((time.monotonic_ns() - started_ns) / 1_000_000)),
        ),
        lock=send_lock,
    )
    await _send_puzzle_difficulty_progress(
        websocket,
        job,
        engine_id=spec.engine_id,
        status="completed",
        detail=f"Finished {spec.name} {spec.version}",
        total=total,
        progress_current=progress_current,
        progress_lock=progress_lock,
        send_lock=send_lock,
    )


async def _serve_puzzle_difficulty_slot(
    websocket,
    job: ToolJobAssignment,
    spec: EngineSpec,
    *,
    puzzles: list[Any],
    slot_number: int,
    slot_count: int,
    movetime_ms: int,
    threads: int,
    hash_mb: int,
    min_gap: float,
    total: int,
    progress_current: list[int],
    progress_lock: asyncio.Lock,
    semaphore: asyncio.Semaphore,
    send_lock: asyncio.Lock,
    server_url: str,
    credential: str,
) -> tuple[str, str]:
    async with semaphore:
        engine = UciEngineProcess(
            spec,
            server_url=server_url,
            credential=credential,
            command_timeout_s=max(60, round(movetime_ms / 1000) + 30),
            allow_build=True,
        )
        engine_status = "supported"
        engine_error = ""
        clear_hash = False
        try:
            try:
                await asyncio.to_thread(engine.prepare)
                uci_lines = await asyncio.to_thread(engine.handle_command, "uci")
                clear_hash = "clear_hash" in _uci_option_names(uci_lines)
                missing = await asyncio.to_thread(
                    _configure_puzzle_engine,
                    engine,
                    spec_options=spec.uci_options,
                    uci_lines=uci_lines,
                    stage="difficulty",
                    threads=threads,
                    hash_mb=hash_mb,
                    multipv=1,
                )
                if missing:
                    engine_status = "unsupported"
                    engine_error = missing
                else:
                    await asyncio.to_thread(engine.handle_command, "isready")
            except Exception as error:
                engine_status = "failed"
                engine_error = (str(error).strip() or error.__class__.__name__)[-8000:]
            for puzzle in puzzles:
                if engine_error:
                    result = ToolJobPuzzleResult(
                        job_id=job.job_id,
                        job_key=job.job_key,
                        engine_id=spec.engine_id,
                        puzzle_id=int(puzzle["id"]),
                        stage="difficulty",
                        status="failed",
                        time_ms=0,
                        error=engine_error,
                    )
                else:
                    try:
                        result = await asyncio.to_thread(
                            _search_puzzle,
                            engine,
                            job,
                            spec.engine_id,
                            puzzle,
                            stage="difficulty",
                            movetime_ms=movetime_ms,
                            min_gap=min_gap,
                            clear_hash=clear_hash,
                        )
                    except Exception as error:
                        result = ToolJobPuzzleResult(
                            job_id=job.job_id,
                            job_key=job.job_key,
                            engine_id=spec.engine_id,
                            puzzle_id=int(puzzle["id"]),
                            stage="difficulty",
                            status="failed",
                            time_ms=0,
                            error=(
                                str(error).strip() or error.__class__.__name__
                            )[-8000:],
                        )
                await _send_message(
                    websocket,
                    "tool_job_puzzle_result",
                    result,
                    lock=send_lock,
                )
                await _send_puzzle_difficulty_progress(
                    websocket,
                    job,
                    engine_id=spec.engine_id,
                    status="running",
                    detail=(
                        f"{spec.name} slot {slot_number}/{slot_count} finished puzzle "
                        f"{puzzle['id']}"
                    ),
                    total=total,
                    progress_current=progress_current,
                    progress_lock=progress_lock,
                    send_lock=send_lock,
                    advance=True,
                )
            return engine_status, engine_error
        finally:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(engine.close)


async def _send_puzzle_difficulty_progress(
    websocket,
    job: ToolJobAssignment,
    *,
    engine_id: int,
    status: str,
    detail: str,
    total: int,
    progress_current: list[int],
    progress_lock: asyncio.Lock,
    send_lock: asyncio.Lock,
    advance: bool = False,
) -> None:
    async with progress_lock:
        if advance:
            progress_current[0] += 1
        await _send_message(
            websocket,
            "tool_job_progress",
            ToolJobProgress(
                job_id=job.job_id,
                job_key=job.job_key,
                engine_id=engine_id,
                status=status,
                detail=detail,
                current=progress_current[0],
                total=total,
            ),
            lock=send_lock,
        )


def _positive_job_integer(input_data: dict[str, Any], name: str) -> int:
    value = input_data.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProtocolValidationError(f"the puzzle suite assignment has an invalid {name}")
    return value


def _configure_puzzle_engine(
    engine: UciEngineProcess,
    *,
    spec_options: dict[str, Any],
    uci_lines: list[str],
    stage: str,
    threads: int,
    hash_mb: int,
    multipv: int,
) -> str:
    available = _uci_option_names(uci_lines)
    required = ["threads", "hash"]
    if stage == "uniqueness":
        required.extend(("multipv", "uci_showwdl"))
    missing = [name for name in required if name not in available]
    if missing:
        return "engine is missing required UCI option" + ("s" if len(missing) > 1 else "") + ": " + ", ".join(missing)
    for name, value in spec_options.items():
        engine.handle_command(f"setoption name {name} value {value}")
    engine.handle_command(f"setoption name Threads value {threads}")
    engine.handle_command(f"setoption name Hash value {hash_mb}")
    if stage == "uniqueness":
        engine.handle_command(f"setoption name MultiPV value {multipv}")
        engine.handle_command("setoption name UCI_ShowWDL value true")
    return ""


def _uci_option_names(lines: list[str]) -> set[str]:
    names: set[str] = set()
    for line in lines:
        match = re.match(r"^option\s+name\s+(.+?)\s+type\s+\S+", line.strip(), re.IGNORECASE)
        if match is not None:
            names.add("_".join(match.group(1).casefold().split()))
    return names


def _search_puzzle(
    engine: UciEngineProcess,
    job: ToolJobAssignment,
    engine_id: int,
    puzzle: dict[str, Any],
    *,
    stage: str,
    movetime_ms: int,
    min_gap: float,
    clear_hash: bool,
) -> ToolJobPuzzleResult:
    puzzle_id = int(puzzle["id"])
    fen = str(puzzle.get("fen") or "")
    solutions = tuple(str(move) for move in puzzle.get("solutions") or ())
    if not fen or not solutions:
        raise ValueError("puzzle has no FEN or solution")
    if clear_hash:
        engine.handle_command("setoption name Clear Hash")
    engine.handle_command("ucinewgame")
    engine.handle_command("isready")
    engine.handle_command(f"position fen {fen}")
    started_ns = time.monotonic_ns()
    latest: dict[int, dict[str, Any]] = {}
    solution_nodes: int | None = None

    def collect(line: str) -> None:
        nonlocal solution_nodes
        info = _parse_puzzle_search_info(line)
        if info is None:
            return
        latest[int(info["multipv"])] = info
        if (
            stage == "difficulty"
            and int(info["multipv"]) == 1
            and info["move"] in solutions
            and isinstance(info["nodes"], int)
            and int(info["nodes"]) > 0
            and solution_nodes is None
        ):
            solution_nodes = int(info["nodes"])

    lines = engine.handle_command(f"go movetime {movetime_ms}", line_callback=collect)
    elapsed_ms = max(0, round((time.monotonic_ns() - started_ns) / 1_000_000))
    bestmove = _bestmove_from_lines(lines)
    primary = latest.get(1, {})
    if stage == "uniqueness":
        second = latest.get(2, {})
        best_sigmoid = primary.get("sigmoid")
        second_sigmoid = second.get("sigmoid")
        if not isinstance(best_sigmoid, float) or not isinstance(second_sigmoid, float):
            raise ValueError("engine did not report WDL for both leading MultiPV lines")
        best_move = str(primary.get("move") or bestmove)
        second_move = str(second.get("move") or "")
        gap = best_sigmoid - second_sigmoid
        unique = bool(best_move and second_move and best_move != second_move and gap >= min_gap)
        return ToolJobPuzzleResult(
            job_id=job.job_id,
            job_key=job.job_key,
            engine_id=engine_id,
            puzzle_id=puzzle_id,
            stage="uniqueness",
            status="unique" if unique else "ambiguous",
            best_move=best_move,
            second_move=second_move,
            best_sigmoid=best_sigmoid,
            second_sigmoid=second_sigmoid,
            sigmoid_gap=gap,
            final_nodes=_optional_positive_int(primary.get("nodes")),
            depth=_optional_nonnegative_int(primary.get("depth")),
            time_ms=_optional_nonnegative_int(primary.get("time")) or elapsed_ms,
        )
    final_nodes = _optional_positive_int(primary.get("nodes"))
    if final_nodes is None:
        raise ValueError("engine did not report a positive node count")
    if bestmove in solutions and solution_nodes is None:
        solution_nodes = final_nodes
    solved = bestmove in solutions and solution_nodes is not None
    return ToolJobPuzzleResult(
        job_id=job.job_id,
        job_key=job.job_key,
        engine_id=engine_id,
        puzzle_id=puzzle_id,
        stage="difficulty",
        status="solved" if solved else "unsolved",
        best_move=bestmove,
        solution_nodes=solution_nodes if solved else None,
        final_nodes=final_nodes,
        depth=_optional_nonnegative_int(primary.get("depth")),
        time_ms=_optional_nonnegative_int(primary.get("time")) or elapsed_ms,
    )


def _parse_puzzle_search_info(line: str) -> dict[str, Any] | None:
    parts = line.split()
    if not parts or parts[0] != "info" or "pv" not in parts:
        return None
    try:
        pv_index = parts.index("pv")
        move = parts[pv_index + 1]
    except (ValueError, IndexError):
        return None
    result: dict[str, Any] = {
        "move": move,
        "multipv": _integer_after(parts, "multipv") or 1,
        "nodes": _integer_after(parts, "nodes"),
        "depth": _integer_after(parts, "depth"),
        "time": _integer_after(parts, "time"),
    }
    if "wdl" in parts:
        try:
            index = parts.index("wdl")
            win, draw, loss = (int(parts[index + offset]) for offset in (1, 2, 3))
            total = win + draw + loss
            if total > 0:
                result["sigmoid"] = (win + 0.5 * draw) / total
        except (ValueError, IndexError):
            pass
    return result


def _integer_after(parts: list[str], token: str) -> int | None:
    try:
        return int(parts[parts.index(token) + 1])
    except (ValueError, IndexError):
        return None


def _bestmove_from_lines(lines: list[str]) -> str:
    for line in reversed(lines):
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "bestmove":
            return parts[1] if parts[1] != "(none)" else ""
    return ""


def _optional_positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _optional_nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _find_uci_option(lines: list[str], option_name: str) -> tuple[str, str]:
    target = " ".join(option_name.split()).casefold()
    for line in lines:
        stripped = line.strip()
        match = re.match(r"^option\s+name\s+(.+?)\s+type\s+\S+", stripped, re.IGNORECASE)
        if match is None:
            continue
        name = match.group(1).strip()
        if " ".join(name.split()).casefold() == target:
            return name, stripped[:4000]
    return "", ""


async def _serve_assignment(
    websocket,
    assignment: WorkerGameAssignment,
    *,
    inbox: asyncio.Queue,
    send_lock: asyncio.Lock,
    benchmark_cache: _EngineBenchmarkCache,
    telemetry: _WorkerTelemetryBatcher,
    server_url: str,
    credential: str,
) -> None:
    loop = asyncio.get_running_loop()
    progress = _AssignmentProgressPublisher(
        websocket,
        assignment,
        loop=loop,
        send_lock=send_lock,
    )
    engines = {
        engine_id: UciEngineProcess(
            engine,
            server_url=server_url,
            credential=credential,
            progress_callback=progress.thread_callback(engine),
        )
        for engine_id, engine in assignment.engines.items()
    }
    completion_received = False
    engine_names = ", ".join(engine.name for engine in assignment.engines.values())
    LOG.info(
        "assignment received assignment_id=%s game_id=%s tournament=%s round=%s engines=%s",
        assignment.assignment.assignment_id,
        assignment.assignment.game_id,
        assignment.tournament_name,
        assignment.round,
        engine_names,
    )
    try:
        await progress.publish(
            "assignment",
            "received",
            "completed",
            f"Worker accepted game {assignment.assignment.game_id}",
        )
        await progress.publish(
            "engines",
            "prepare_all",
            "running",
            f"Preparing {len(engines)} assigned engines",
            current=0,
            total=len(engines),
        )
        await asyncio.gather(
            *(asyncio.to_thread(engine.prepare) for engine in engines.values())
        )
        await progress.publish(
            "engines",
            "prepare_all",
            "completed",
            f"All {len(engines)} assigned engines are available",
            current=len(engines),
            total=len(engines),
        )
        benchmark_reference = assignment.benchmark_reference
        benchmark_engine_ids = (
            ()
            if benchmark_reference is None
            else tuple(sorted(benchmark_reference.engine_nps))
        )
        await progress.publish(
            "benchmark",
            "benchmark_all",
            "running",
            f"Benchmarking {len(benchmark_engine_ids)} managed engines",
            current=0,
            total=max(1, len(benchmark_engine_ids)),
        )
        completed_benchmarks = 0
        benchmark_slots = asyncio.Semaphore(ENGINE_BENCHMARK_CONCURRENCY)
        benchmark_progress_lock = asyncio.Lock()

        async def benchmark_engine(engine_id: int) -> EngineHardwareScore:
            nonlocal completed_benchmarks
            engine = engines[engine_id]
            spec = assignment.engines[engine_id]
            if benchmark_reference is None:
                raise ProtocolValidationError("managed engine has no benchmark reference")
            reference_nps = benchmark_reference.engine_nps[engine_id]
            async with benchmark_slots:
                async with benchmark_progress_lock:
                    current = completed_benchmarks
                await progress.publish(
                    "benchmark",
                    "engine_benchmark",
                    "running",
                    f"Benchmarking {spec.name}",
                    engine=spec,
                    current=current,
                    total=len(benchmark_engine_ids),
                )
                worker_nps, elapsed_ms = await benchmark_cache.benchmark(
                    engine,
                    spec,
                    benchmark_reference.timeout_s,
                )
                hardware_score = worker_nps / reference_nps
                score = EngineHardwareScore(
                    benchmark_nps=reference_nps,
                    worker_nps=worker_nps,
                    hardware_score=hardware_score,
                    elapsed_ms=elapsed_ms,
                )
                async with benchmark_progress_lock:
                    completed_benchmarks += 1
                    current = completed_benchmarks
                await progress.publish(
                    "benchmark",
                    "engine_benchmark",
                    "completed",
                    f"Benchmarked {spec.name} at {worker_nps} NPS",
                    engine=spec,
                    current=current,
                    total=len(benchmark_engine_ids),
                    metadata={
                        "benchmark_nps": reference_nps,
                        "worker_nps": worker_nps,
                        "hardware_score": hardware_score,
                        "elapsed_ms": elapsed_ms,
                    },
                )
                return score

        benchmark_results = await asyncio.gather(
            *(benchmark_engine(engine_id) for engine_id in benchmark_engine_ids),
            return_exceptions=True,
        )
        hardware_scores: dict[int, EngineHardwareScore] = {}
        for engine_id, result in zip(benchmark_engine_ids, benchmark_results):
            if isinstance(result, BaseException):
                raise result
            hardware_scores[engine_id] = result
        await progress.publish(
            "benchmark",
            "benchmark_all",
            "completed",
            (
                f"Benchmarked all {len(benchmark_engine_ids)} managed engines"
                if benchmark_engine_ids
                else "No managed engines required a hardware benchmark"
            ),
            current=len(benchmark_engine_ids),
            total=max(1, len(benchmark_engine_ids)),
        )
        ready = AssignmentReady(
            **assignment.assignment.message_fields(),
            prepared_engine_ids=sorted(engines),
            hardware_scores=hardware_scores,
        )
        await _send_message(websocket, "assignment_ready", ready, lock=send_lock)
        LOG.info(
            "assignment prepared assignment_id=%s game_id=%s engines=%s",
            assignment.assignment.assignment_id,
            assignment.assignment.game_id,
            ready.prepared_engine_ids,
        )
        commands_handled = 0
        play_started = False
        infinite_searches: dict[int, asyncio.Task] = {}
        while True:
            envelope = await inbox.get()
            if envelope.type == "assignment_complete":
                complete = AssignmentComplete.model_validate(envelope.data)
                _validate_assignment_message(complete, assignment, "assignment_complete")
                for engine_id, task in tuple(infinite_searches.items()):
                    await _stop_infinite_engine_search(engines[engine_id], task)
                infinite_searches.clear()
                completion_received = True
                LOG.info(
                    "assignment complete assignment_id=%s game_id=%s commands=%s",
                    assignment.assignment.assignment_id,
                    assignment.assignment.game_id,
                    commands_handled,
                )
                break

            if envelope.type == "engine_stop":
                stop = EngineStop.model_validate(envelope.data)
                _validate_assignment_message(stop, assignment, "engine_stop")
                if stop.engine_id not in engines:
                    raise ProtocolValidationError(
                        f"assignment missing engine {stop.engine_id}"
                    )
                task = infinite_searches.pop(stop.engine_id, None)
                if task is not None:
                    await _stop_infinite_engine_search(engines[stop.engine_id], task)
                continue

            if envelope.type != "engine_command":
                raise ProtocolValidationError(f"unexpected runner message: {envelope.type}")

            command = EngineCommand.model_validate(envelope.data)
            _validate_assignment_message(command, assignment, "engine_command")
            LOG.debug(
                "engine command received assignment_id=%s game_id=%s engine_id=%s command=%s",
                command.assignment_id,
                command.game_id,
                command.engine_id,
                command.command,
            )

            engine = engines.get(command.engine_id)
            if engine is None:
                raise ProtocolValidationError(f"assignment missing engine {command.engine_id}")

            if command.command.strip().lower() == "go infinite":
                if command.engine_id in infinite_searches:
                    raise ProtocolValidationError(f"engine {command.engine_id} is already searching")
                play_started = True
                infinite_searches[command.engine_id] = asyncio.create_task(
                    _run_infinite_engine_search(
                        websocket,
                        engine,
                        command,
                        assignment.engines[command.engine_id].name,
                        telemetry=telemetry,
                        send_lock=send_lock,
                        loop=loop,
                    )
                )
                continue

            command_stage, command_substage, command_detail = _command_progress(
                command.command,
                assignment.engines[command.engine_id].name,
                play_started=play_started,
            )
            track_command_progress = command_stage != "play"
            if command.command.startswith("go"):
                play_started = True
            if track_command_progress:
                await progress.publish(
                    command_stage,
                    command_substage,
                    "running",
                    command_detail,
                    engine=assignment.engines[command.engine_id],
                )
            await _send_message(
                websocket,
                "engine_command_started",
                EngineCommandStarted(
                    **command.model_dump(exclude={"command"}),
                ),
                lock=send_lock,
            )
            command_timer = _CommandTimer()

            info_publisher = (
                _EngineInfoPublisher(telemetry, command, loop, command_timer)
                if command.command.startswith("go")
                else None
            )
            line_callback = None if info_publisher is None else info_publisher.publish
            clock_task = (
                asyncio.create_task(
                    _publish_engine_clock(
                        command,
                        telemetry,
                        command_timer,
                    )
                )
                if info_publisher is not None
                else None
            )
            try:
                command_task = asyncio.create_task(
                    asyncio.to_thread(
                        _handle_engine_command_timed,
                        engine,
                        command.command,
                        line_callback,
                        command_timer,
                    )
                )
                if info_publisher is not None:
                    command_result = await _wait_for_engine_search(
                        command_task,
                        inbox,
                        engine,
                        command,
                        assignment,
                        info_publisher,
                        clock_task,
                        telemetry,
                    )
                    if command_result is None:
                        await info_publisher.cancel()
                        if clock_task is not None:
                            clock_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await clock_task
                        if track_command_progress:
                            await progress.publish(
                                command_stage,
                                command_substage,
                                "completed",
                                f"{assignment.engines[command.engine_id].name} search was stopped",
                                engine=assignment.engines[command.engine_id],
                                metadata={"stopped": True},
                            )
                        continue
                    result_lines, command_elapsed_ms = command_result
                else:
                    result_lines, command_elapsed_ms = await command_task
                if info_publisher is not None:
                    await info_publisher.finish()
                if clock_task is not None:
                    clock_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await clock_task
                if info_publisher is not None:
                    telemetry.discard(command.assignment_id, command.engine_id)
                if track_command_progress:
                    await progress.publish(
                        command_stage,
                        command_substage,
                        "completed",
                        _command_completed_detail(
                            command.command,
                            assignment.engines[command.engine_id].name,
                            command_elapsed_ms,
                        ),
                        engine=assignment.engines[command.engine_id],
                    )
            except Exception as error:
                if info_publisher is not None:
                    await info_publisher.cancel()
                if clock_task is not None:
                    clock_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await clock_task
                telemetry.discard(command.assignment_id, command.engine_id)
                failure = AssignmentFailed(
                    **assignment.assignment.message_fields(),
                    engine_id=command.engine_id,
                    engine_name=assignment.engines[command.engine_id].name,
                    stage="runtime" if engine.process_started else "start",
                    error=(str(error).strip() or error.__class__.__name__)[-8000:],
                )
                await progress.publish(
                    command_stage,
                    command_substage,
                    "failed",
                    failure.error,
                    engine=assignment.engines[command.engine_id],
                )
                await _send_message(
                    websocket,
                    "assignment_failed",
                    failure,
                    lock=send_lock,
                )
                LOG.error(
                    "engine command failed assignment_id=%s game_id=%s engine_id=%s "
                    "engine=%s stage=%s error=%s",
                    command.assignment_id,
                    command.game_id,
                    failure.engine_id,
                    failure.engine_name,
                    failure.stage,
                    failure.error,
                )
                await _wait_for_failed_assignment_complete(
                    websocket,
                    assignment,
                    failure,
                    inbox=inbox,
                    send_lock=send_lock,
                )
                completion_received = True
                break

            result = EngineCommandResult(
                **command.model_dump(exclude={"command"}),
                lines=_compact_search_result_lines(result_lines, command_elapsed_ms)
                if info_publisher is not None
                else result_lines,
                elapsed_ms=command_elapsed_ms,
            )
            commands_handled += 1
            LOG.debug(
                "engine command completed assignment_id=%s game_id=%s engine_id=%s command=%s lines=%s%s",
                command.assignment_id,
                command.game_id,
                command.engine_id,
                command.command,
                len(result_lines),
                _line_sample(result_lines),
            )
            await _send_message(
                websocket,
                "engine_command_result",
                result,
                lock=send_lock,
            )
    except EnginePreparationError as error:
        failed_engine = assignment.engines[error.engine_id]
        await progress.publish(
            "benchmark" if error.stage == "benchmark" else "engines",
            f"{error.stage}_failed",
            "failed",
            error.detail[-4000:],
            engine=failed_engine,
        )
        failure = AssignmentFailed(
            **assignment.assignment.message_fields(),
            engine_id=error.engine_id,
            engine_name=error.engine_name,
            stage=error.stage,
            error=error.detail[-8000:],
        )
        await _send_message(
            websocket,
            "assignment_failed",
            failure,
            lock=send_lock,
        )
        LOG.error(
            "assignment preparation failed assignment_id=%s game_id=%s engine_id=%s "
            "engine=%s stage=%s error=%s",
            assignment.assignment.assignment_id,
            assignment.assignment.game_id,
            error.engine_id,
            error.engine_name,
            error.stage,
            error.detail,
        )
        await _wait_for_failed_assignment_complete(
            websocket,
            assignment,
            failure,
            inbox=inbox,
            send_lock=send_lock,
        )
        completion_received = True
    except Exception:
        LOG.exception(
            "assignment failed assignment_id=%s game_id=%s",
            assignment.assignment.assignment_id,
            assignment.assignment.game_id,
        )
        raise
    finally:
        try:
            if completion_received:
                await progress.publish(
                    "cleanup",
                    "engine_shutdown",
                    "running",
                    f"Closing {len(engines)} engine processes",
                    current=0,
                    total=len(engines),
                )
        finally:
            await asyncio.gather(
                *(asyncio.to_thread(engine.close) for engine in engines.values())
            )
            if completion_received:
                await progress.publish(
                    "cleanup",
                    "engine_shutdown",
                    "completed",
                    f"Closed {len(engines)} engine processes",
                    current=len(engines),
                    total=len(engines),
                )
                await _send_message(
                    websocket,
                    "assignment_cleanup_complete",
                    AssignmentCleanupComplete(**assignment.assignment.message_fields()),
                    lock=send_lock,
                )
        LOG.info(
            "assignment engines closed assignment_id=%s game_id=%s",
            assignment.assignment.assignment_id,
            assignment.assignment.game_id,
        )


async def _wait_for_failed_assignment_complete(
    websocket,
    assignment: WorkerGameAssignment,
    failure: AssignmentFailed,
    *,
    inbox: asyncio.Queue,
    send_lock: asyncio.Lock,
) -> None:
    while True:
        envelope = await inbox.get()
        if envelope.type == "assignment_complete":
            complete = AssignmentComplete.model_validate(envelope.data)
            _validate_assignment_message(complete, assignment, "assignment_complete")
            return
        if envelope.type == "engine_command":
            command = EngineCommand.model_validate(envelope.data)
            _validate_assignment_message(command, assignment, "engine_command")
            await _send_message(
                websocket,
                "assignment_failed",
                failure,
                lock=send_lock,
            )
            continue
        raise ProtocolValidationError(
            f"unexpected runner message after engine failure: {envelope.type}"
        )


def _validate_assignment_message(
    message: AssignmentComplete | EngineCommand | EngineStop,
    assignment: WorkerGameAssignment,
    label: str,
) -> None:
    if not message.matches_assignment(assignment.assignment):
        raise ProtocolValidationError(f"{label} assignment mismatch")


class _CommandTimer:
    def __init__(self) -> None:
        self._started_ns = time.monotonic_ns()
        self._ended_ns: int | None = None
        self._lock = threading.Lock()

    def stop(self) -> int:
        with self._lock:
            if self._ended_ns is None:
                self._ended_ns = time.monotonic_ns()
            ended_ns = self._ended_ns
        return max(0, round((ended_ns - self._started_ns) / 1_000_000))

    def elapsed_ms(self) -> int:
        with self._lock:
            ended_ns = self._ended_ns
        now_ns = time.monotonic_ns() if ended_ns is None else ended_ns
        return max(0, round((now_ns - self._started_ns) / 1_000_000))


async def _wait_for_engine_search(
    command_task: asyncio.Task,
    inbox: asyncio.Queue,
    engine: UciEngineProcess,
    command: EngineCommand,
    assignment: WorkerGameAssignment,
    info_publisher: _EngineInfoPublisher,
    clock_task: asyncio.Task,
    telemetry: _WorkerTelemetryBatcher,
) -> tuple[list[str], int] | None:
    while True:
        receive = asyncio.create_task(inbox.get())
        try:
            done, _pending = await asyncio.wait(
                {command_task, receive},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive not in done:
                return command_task.result()

            envelope = receive.result()
            if envelope.type != "engine_stop":
                raise ProtocolValidationError(
                    f"unexpected runner message during engine search: {envelope.type}"
                )
            stop = EngineStop.model_validate(envelope.data)
            _validate_assignment_message(stop, assignment, "engine_stop")
            if stop.engine_id != command.engine_id:
                raise ProtocolValidationError("engine_stop engine mismatch")
            await info_publisher.cancel()
            clock_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await clock_task
            await asyncio.to_thread(engine.stop_search)
            try:
                await asyncio.wait_for(asyncio.shield(command_task), timeout=2)
            except asyncio.TimeoutError:
                await asyncio.to_thread(engine.close)
            with contextlib.suppress(Exception):
                await command_task
            telemetry.discard(command.assignment_id, command.engine_id)
            return None
        finally:
            if not receive.done():
                receive.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await receive


async def _run_infinite_engine_search(
    websocket,
    engine: UciEngineProcess,
    command: EngineCommand,
    engine_name: str,
    *,
    telemetry: _WorkerTelemetryBatcher,
    send_lock: asyncio.Lock,
    loop,
) -> None:
    await _send_message(
        websocket,
        "engine_command_started",
        EngineCommandStarted(**command.model_dump(exclude={"command"})),
        lock=send_lock,
    )
    command_timer = _CommandTimer()
    info_publisher = _EngineInfoPublisher(telemetry, command, loop, command_timer)
    clock_task = asyncio.create_task(
        _publish_engine_clock(command, telemetry, command_timer)
    )
    try:
        result_lines, elapsed_ms = await asyncio.to_thread(
            _handle_engine_command_timed,
            engine,
            command.command,
            info_publisher.publish,
            command_timer,
        )
        await info_publisher.finish()
    except Exception as error:
        await info_publisher.cancel()
        await _send_message(
            websocket,
            "assignment_failed",
            AssignmentFailed(
                **command.model_dump(exclude={"command", "engine_id"}),
                engine_id=command.engine_id,
                engine_name=engine_name,
                stage="runtime" if engine.process_started else "start",
                error=(str(error).strip() or error.__class__.__name__)[-8000:],
            ),
            lock=send_lock,
        )
        return
    finally:
        clock_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await clock_task
        await telemetry.flush()
        telemetry.discard(command.assignment_id, command.engine_id)
    await _send_message(
        websocket,
        "engine_command_result",
        EngineCommandResult(
            **command.model_dump(exclude={"command"}),
            lines=_compact_search_result_lines(result_lines, elapsed_ms),
            elapsed_ms=elapsed_ms,
        ),
        lock=send_lock,
    )


async def _stop_infinite_engine_search(
    engine: UciEngineProcess,
    task: asyncio.Task,
) -> None:
    await asyncio.to_thread(engine.stop_search)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=2)
    except asyncio.TimeoutError:
        await asyncio.to_thread(engine.close)
        try:
            await asyncio.wait_for(task, timeout=2)
        except asyncio.TimeoutError:
            task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


class _AssignmentProgressPublisher:
    def __init__(
        self,
        websocket,
        assignment: WorkerGameAssignment,
        *,
        loop,
        send_lock: asyncio.Lock,
    ) -> None:
        self._websocket = websocket
        self._assignment = assignment
        self._loop = loop
        self._send_lock = send_lock
        self._steps = {step.key: step for step in assignment.workflow}

    async def publish(
        self,
        stage: str,
        substage: str,
        status: str,
        detail: str,
        *,
        engine=None,
        current: int | None = None,
        total: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        step = self._steps.get(stage)
        if step is None:
            raise ProtocolValidationError(f"workflow has no {stage!r} stage")
        payload = AssignmentProgress(
            **self._assignment.assignment.message_fields(),
            stage=stage,
            stage_label=step.label,
            stage_order=step.order,
            substage=substage,
            status=status,
            detail=detail[:4000],
            engine_id=None if engine is None else engine.engine_id,
            engine_name=None if engine is None else engine.name,
            current=current,
            total=total,
            metadata=metadata or {},
        )
        await _send_message(
            self._websocket,
            "assignment_progress",
            payload,
            lock=self._send_lock,
        )

    def thread_callback(self, engine):
        def report(stage: str, substage: str, status: str, detail: str) -> None:
            future = asyncio.run_coroutine_threadsafe(
                self.publish(
                    stage,
                    substage,
                    status,
                    detail,
                    engine=engine,
                ),
                self._loop,
            )
            future.result()

        return report


def _command_progress(
    command: str,
    engine_name: str,
    *,
    play_started: bool,
) -> tuple[str, str, str]:
    if command == "uci":
        return "startup", "uci_handshake", f"Negotiating UCI with {engine_name}"
    if command.startswith("setoption"):
        return "startup", "configure_option", f"Configuring {engine_name}: {command}"
    if command == "isready":
        return "startup", "readiness_check", f"Waiting for {engine_name} readiness"
    if command == "ucinewgame":
        return "startup", "new_game_reset", f"Resetting {engine_name} for the assigned game"
    if command.startswith("position"):
        move_count = len(command.split(" moves ", 1)[1].split()) if " moves " in command else 0
        return (
            "play" if play_started else "opening",
            "position_sync" if play_started else "position_load",
            f"Loading the position in {engine_name} with {move_count} preplayed moves",
        )
    if command.startswith("go"):
        return "play", "engine_search", f"{engine_name} is calculating the next move"
    if command == "quit":
        return "cleanup", "engine_quit", f"Stopping {engine_name}"
    return "play", "engine_command", f"Sending {command} to {engine_name}"


def _command_completed_detail(command: str, engine_name: str, elapsed_ms: int) -> str:
    if command.startswith("go"):
        return f"{engine_name} selected a move in {elapsed_ms}ms"
    return f"{engine_name} completed {command} in {elapsed_ms}ms"


async def _publish_engine_clock(
    command: EngineCommand,
    telemetry: _WorkerTelemetryBatcher,
    command_timer: _CommandTimer,
) -> None:
    while True:
        await asyncio.sleep(ENGINE_CLOCK_SEND_INTERVAL_S)
        telemetry.offer(
            "engine_clock",
            EngineClock(
                **command.model_dump(exclude={"command"}),
                elapsed_ms=command_timer.elapsed_ms(),
            ),
        )


class _EngineInfoPublisher:
    """Keep engine stdout draining while bounding analysis traffic to the runner."""

    def __init__(
        self,
        telemetry: _WorkerTelemetryBatcher,
        command: EngineCommand,
        loop,
        command_timer: _CommandTimer | None = None,
    ) -> None:
        self._telemetry = telemetry
        self._command = command
        self._loop = loop
        self._command_timer = _CommandTimer() if command_timer is None else command_timer
        self._latest_line: str | None = None
        self._wake = asyncio.Event()
        self._finish_requested = asyncio.Event()
        self._finishing = False
        self._task = loop.create_task(self._run())

    def publish(self, line: str) -> None:
        line = clamp_uci_info_line(line)
        if is_full_uci_info_line(line):
            self._loop.call_soon_threadsafe(self._offer, line)

    def _offer(self, line: str) -> None:
        if self._finishing or self._task.done():
            return
        self._latest_line = line
        self._wake.set()

    async def finish(self) -> None:
        self._finishing = True
        self._finish_requested.set()
        self._wake.set()
        await self._task

    async def cancel(self) -> None:
        self._finishing = True
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task

    async def _run(self) -> None:
        next_send_at = 0.0
        while True:
            await self._wake.wait()
            self._wake.clear()

            if self._latest_line is None:
                if self._finishing:
                    return
                continue

            if not self._finishing:
                delay = next_send_at - self._loop.time()
                if delay > 0:
                    try:
                        await asyncio.wait_for(self._finish_requested.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass

            line = self._latest_line
            self._latest_line = None
            info = EngineInfo(
                **self._command.model_dump(exclude={"command"}),
                lines=[line],
                elapsed_ms=self._command_timer.elapsed_ms(),
            )
            self._telemetry.offer(
                "engine_info",
                info,
            )
            next_send_at = self._loop.time() + ENGINE_INFO_SEND_INTERVAL_S

            if self._finishing and self._latest_line is None:
                return
            if self._latest_line is not None:
                self._wake.set()


def _handle_engine_command_timed(
    engine: UciEngineProcess,
    command: str,
    line_callback,
    command_timer: _CommandTimer | None = None,
) -> tuple[list[str], int]:
    command_timer = _CommandTimer() if command_timer is None else command_timer
    try:
        lines = engine.handle_command(command, line_callback)
    finally:
        elapsed_ms = command_timer.stop()
    return lines, elapsed_ms


def _compact_search_result_lines(lines: list[str], elapsed_ms: int) -> list[str]:
    """Retain the final analysis snapshot and all non-analysis UCI output."""
    last_info: str | None = None
    result: list[str] = []
    for line in lines:
        if is_full_uci_info_line(line):
            last_info = line
        elif not (line == "info" or line.startswith("info ")):
            result.append(line)
    if last_info is not None:
        result.insert(max(len(result) - 1, 0), last_info)
    result.insert(max(len(result) - 1, 0), worker_command_elapsed_line(elapsed_ms))
    return result


async def _send_message(
    websocket,
    message_type: str,
    data,
    *,
    lock: asyncio.Lock | None = None,
) -> None:
    log = (
        LOG.debug
        if message_type in {
            "assignment_progress",
            "engine_clock",
            "engine_command_result",
            "engine_command_started",
            "engine_info",
            "worker_telemetry_batch",
            "worker_resource_telemetry",
        }
        else LOG.info
    )
    log(
        "sending runner message type=%s %s",
        message_type,
        _message_log_context(message_type, data),
    )
    payload = encode_message(make_message(message_type, data))
    if lock is None:
        await websocket.send(payload)
        return
    async with lock:
        await websocket.send(payload)


async def _recv_envelope(websocket):
    raw_message = await websocket.recv()
    envelope = decode_envelope(raw_message)
    log = LOG.debug if envelope.type == "engine_command" else LOG.info
    log(
        "received runner message type=%s %s",
        envelope.type,
        _message_log_context(envelope.type, envelope.data),
    )
    return envelope


async def _recv_message(websocket, message_type: str, data_type):
    envelope = await _recv_envelope(websocket)
    if envelope.type != message_type:
        raise ProtocolValidationError(
            f"expected {message_type} message, got {envelope.type}"
        )
    return data_type.model_validate(envelope.data)


def _message_log_context(message_type: str, data: Any) -> str:
    payload = _model_data(data)
    if message_type == "hello":
        if payload.get("token"):
            auth = "token"
        else:
            auth = "session"
        hw = payload.get("hw") or {}
        return (
            f"auth={auth} app_version={payload.get('app_version')} "
            f"label_hint={payload.get('label_hint', '')!r} "
            f"active_assignments={len(payload.get('active_assignment_ids') or [])} "
            f"cpu={hw.get('cpu_model')} cores={hw.get('physical_cores')}P/{hw.get('logical_cores')}T "
            f"ram={hw.get('ram_gb')}GB os={hw.get('os')}"
        )
    if message_type == "welcome":
        return (
            f"worker_id={payload.get('worker_id')} "
            f"session={_redact_secret(payload.get('session_id'))} "
            f"heartbeat_interval_ms={payload.get('heartbeat_interval_ms')}"
        )
    if message_type == "assignment":
        assignment = payload.get("assignment") or {}
        engines = payload.get("engines") or {}
        engine_names = ", ".join(
            str(engine.get("name", engine_id))
            for engine_id, engine in engines.items()
            if isinstance(engine, dict)
        )
        return (
            f"assignment_id={assignment.get('assignment_id')} "
            f"game_id={assignment.get('game_id')} "
            f"tournament={payload.get('tournament_name')} "
            f"round={payload.get('round')} max_plies={payload.get('max_plies')} "
            f"engines={engine_names}"
        )
    if message_type in {"assignment_ready", "assignment_complete"}:
        return _assignment_context(payload)
    if message_type == "engine_command":
        return (
            f"{_assignment_context(payload)} "
            f"engine_id={payload.get('engine_id')} command={payload.get('command')}"
        )
    if message_type in {"engine_info", "engine_command_result"}:
        lines = payload.get("lines") or []
        return (
            f"{_assignment_context(payload)} "
            f"engine_id={payload.get('engine_id')} lines={len(lines)}{_line_sample(lines)}"
        )
    return f"keys={','.join(sorted(payload))}"


def _model_data(data: Any) -> dict[str, Any]:
    if hasattr(data, "model_dump"):
        return data.model_dump(mode="json")
    if isinstance(data, dict):
        return data
    return {}


def _assignment_context(payload: dict[str, Any]) -> str:
    return (
        f"assignment_id={payload.get('assignment_id')} "
        f"game_id={payload.get('game_id')}"
    )


def _line_sample(lines: list[str]) -> str:
    if not lines:
        return ""
    line = lines[-1]
    if len(line) > 200:
        line = f"{line[:197]}..."
    return f" last_line={line!r}"


def _redact_secret(value: Any) -> str:
    text = "" if value is None else str(value)
    if not text:
        return "<empty>"
    if len(text) <= 8:
        return "<redacted>"
    return f"{text[:4]}...{text[-4:]}"


def _detect_hardware(*, cpu_capacity: int | None = None) -> HardwareInfo:
    system_logical_cores = os.cpu_count() or 1
    reported_physical_cores = system_logical_cores
    cpu_ids = _process_cpu_ids()
    logical_cores = len(cpu_ids) if cpu_ids else system_logical_cores
    ram_gb = 1
    ram_mb = 1024

    try:
        import psutil

        reported_physical_cores = (
            psutil.cpu_count(logical=False) or reported_physical_cores
        )
        system_logical_cores = psutil.cpu_count(logical=True) or system_logical_cores
        if cpu_ids is None:
            with contextlib.suppress(Exception):
                affinity = psutil.Process().cpu_affinity()
                if affinity:
                    cpu_ids = {int(cpu_id) for cpu_id in affinity}
                    logical_cores = len(cpu_ids)
        total_ram = psutil.virtual_memory().total
        cgroup_limit = _linux_memory_limit_bytes()
        if cgroup_limit is not None:
            total_ram = min(total_ram, cgroup_limit)
        ram_mb = max(1, total_ram // (1024**2))
        ram_gb = max(1, round(total_ram / (1024**3)))
    except ImportError:
        pass

    detected_physical_cores = _linux_physical_core_count(cpu_ids)
    if detected_physical_cores is None:
        detected_physical_cores = max(
            1,
            min(
                logical_cores,
                (
                    logical_cores * reported_physical_cores
                    + system_logical_cores
                    - 1
                )
                // system_logical_cores,
            ),
        )
    cpu_quota = _linux_cpu_quota()
    usable_physical_cores = (
        detected_physical_cores
        if cpu_quota is None
        else min(detected_physical_cores, cpu_quota)
    )
    if cpu_quota is not None:
        logical_cores = min(logical_cores, cpu_quota)
    physical_cores = min(usable_physical_cores, logical_cores)
    if cpu_capacity is not None:
        if cpu_capacity > logical_cores:
            raise ValueError(
                "worker CPU capacity cannot exceed the detected usable "
                f"logical-thread count ({logical_cores})"
            )
        logical_cores = cpu_capacity
        physical_cores = min(physical_cores, logical_cores)

    hw = HardwareInfo(
        cpu_model=_detect_cpu_model(),
        physical_cores=physical_cores,
        logical_cores=logical_cores,
        ram_gb=ram_gb,
        ram_mb=ram_mb,
        gpu=None,
        os=f"{platform.system()} {platform.release()}".strip(),
        python=platform.python_version(),
        bench=BenchInfo(),
    )
    LOG.info(
        "detected hardware cpu=%s topology=%s quota=%s capacity=%s ram=%s os=%s",
        hw.cpu_model,
        f"{detected_physical_cores}P/{hw.logical_cores}T",
        cpu_quota if cpu_quota is not None else "unlimited",
        hw.logical_cores,
        f"{hw.ram_gb}GB",
        hw.os,
    )
    return hw


def _linux_memory_limit_bytes() -> int | None:
    if sys.platform != "linux":
        return None
    paths = (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory.limit_in_bytes"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    )
    limits: list[int] = []
    for path in paths:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value == "max":
            continue
        try:
            limit = int(value)
        except ValueError:
            continue
        if 0 < limit < 1 << 60:
            limits.append(limit)
    return min(limits) if limits else None


def _linux_memory_used_bytes() -> int | None:
    if sys.platform != "linux":
        return None
    paths = (
        Path("/sys/fs/cgroup/memory.current"),
        Path("/sys/fs/cgroup/memory.usage_in_bytes"),
        Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
    )
    for path in paths:
        try:
            value = int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        if value >= 0:
            return value
    return None


def _process_cpu_ids() -> set[int] | None:
    get_affinity = getattr(os, "sched_getaffinity", None)
    if get_affinity is None:
        return None
    try:
        cpu_ids = {int(cpu_id) for cpu_id in get_affinity(0)}
    except OSError:
        return None
    return cpu_ids or None


def _linux_physical_core_count(cpu_ids: set[int] | None) -> int | None:
    if platform.system() != "Linux" or not cpu_ids:
        return None
    sibling_groups: set[str] = set()
    for cpu_id in cpu_ids:
        path = Path(
            f"/sys/devices/system/cpu/cpu{cpu_id}/topology/thread_siblings_list"
        )
        try:
            sibling_group = path.read_text(encoding="ascii").strip()
        except OSError:
            return None
        if not sibling_group:
            return None
        sibling_groups.add(sibling_group)
    return len(sibling_groups) or None


def _linux_cpu_quota() -> int | None:
    if platform.system() != "Linux":
        return None
    try:
        quota_text, period_text = Path("/sys/fs/cgroup/cpu.max").read_text(
            encoding="ascii"
        ).split()
        if quota_text != "max":
            quota = int(quota_text)
            period = int(period_text)
            if quota > 0 and period > 0:
                return max(1, quota // period)
    except (OSError, ValueError):
        pass
    try:
        quota = int(
            Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
            .read_text(encoding="ascii")
            .strip()
        )
        period = int(
            Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
            .read_text(encoding="ascii")
            .strip()
        )
    except (OSError, ValueError):
        return None
    if quota <= 0 or period <= 0:
        return None
    return max(1, quota // period)


def _detect_machine_id() -> str:
    configured = os.environ.get("COPE_MACHINE_ID", "").strip()
    if configured:
        return configured

    fingerprint = "|".join(
        (
            platform.node().strip().lower(),
            f"{uuid.getnode():012x}",
            platform.system().strip().lower(),
            platform.machine().strip().lower(),
        )
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def _detect_cpu_model() -> str:
    processor = platform.processor().strip()
    if processor and processor.lower() not in {"unknown", platform.machine().lower()}:
        return processor

    processor_identifier = os.environ.get("PROCESSOR_IDENTIFIER", "").strip()
    if processor_identifier:
        return processor_identifier

    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        try:
            for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip().lower() in {"model name", "hardware"}:
                    model = value.strip()
                    if model:
                        return model
        except OSError:
            pass

    return platform.machine() or "unknown"


def _log_connection_closed(error: ConnectionClosed) -> None:
    reason = error.reason or str(error) or error.__class__.__name__
    if error.code == 1000:
        LOG.info("runner connection closed code=%s reason=%s", error.code, reason)
        return

    LOG.warning("runner connection lost code=%s reason=%s", error.code, reason)
