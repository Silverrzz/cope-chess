from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import platform
import sys
import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from websockets.client import connect
from websockets.exceptions import ConnectionClosed

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
    EngineStop,
    HardwareInfo,
    WorkerGameAssignment,
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

from .uci_engine import EnginePreparationError, UciEngineProcess


LOG = logging.getLogger("cope.worker")
RECONNECT_INITIAL_DELAY_S = 1.0
RECONNECT_MAX_DELAY_S = 30.0
ENGINE_INFO_SEND_INTERVAL_S = 0.25
ENGINE_CLOCK_SEND_INTERVAL_S = 0.05


@dataclass(frozen=True)
class WorkerClientConfig:
    server_url: str
    app_version: str
    token: str | None = None
    session_id: str | None = None
    label_hint: str = ""
    machine_id: str | None = None
    state_file: Path | None = None


async def run_worker_client(config: WorkerClientConfig) -> None:
    state = _WorkerConnectionState(
        session_id=config.session_id or _read_worker_session(config.state_file),
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
        except (OSError, asyncio.TimeoutError) as error:
            LOG.warning("runner connection failed: %s", error)
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
    async with connect(connection_config.server_url) as websocket:
        await _send_message(websocket, "hello", _build_hello(connection_config))
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
) -> WorkerTokenHello | WorkerSessionHello:
    credential_count = sum(
        value is not None
        for value in (config.token, config.session_id)
    )
    if credential_count != 1:
        raise ValueError(
            "worker client needs exactly one of token or session_id"
        )

    hw = _detect_hardware()
    machine_id = config.machine_id or _detect_machine_id()

    if config.token is not None:
        return WorkerTokenHello(
            token=config.token,
            label_hint=config.label_hint,
            hw=hw,
            app_version=config.app_version,
            machine_id=machine_id,
        )

    return WorkerSessionHello(
        session_id=config.session_id or "",
        hw=hw,
        app_version=config.app_version,
        machine_id=machine_id,
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
    send_lock = asyncio.Lock()
    fatal = asyncio.get_running_loop().create_future()

    def assignment_done(assignment_id: int, task: asyncio.Task) -> None:
        tasks.pop(assignment_id, None)
        queues.pop(assignment_id, None)
        assignments.pop(assignment_id, None)
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
            send_lock=send_lock,
            fatal=fatal,
            assignment_done=assignment_done,
        )
    finally:
        active_tasks = tuple(tasks.values())
        for task in active_tasks:
            task.cancel()
        await asyncio.gather(*active_tasks, return_exceptions=True)
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
    send_lock: asyncio.Lock,
    fatal: asyncio.Future,
    assignment_done,
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
            if tasks:
                raise ProtocolValidationError("runner requested an update during active work")
            update = WorkerUpdateCommand.model_validate(envelope.data)
            return await _apply_worker_update(websocket, update)
        if envelope.type == "assignment":
            assignment = WorkerGameAssignment.model_validate(envelope.data)
            assignment_id = assignment.assignment.assignment_id
            if assignment_id in tasks:
                raise ProtocolValidationError(
                    f"duplicate assignment {assignment_id}"
                )
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


async def _serve_assignment(
    websocket,
    assignment: WorkerGameAssignment,
    *,
    inbox: asyncio.Queue,
    send_lock: asyncio.Lock,
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
        hardware_scores: dict[int, EngineHardwareScore] = {}
        await progress.publish(
            "benchmark",
            "benchmark_all",
            "running",
            f"Benchmarking {len(engines)} assigned engines",
            current=0,
            total=len(engines),
        )
        for position, engine_id in enumerate(sorted(engines), start=1):
            engine = engines[engine_id]
            spec = assignment.engines[engine_id]
            reference_nps = assignment.benchmark_reference.engine_nps[engine_id]
            await progress.publish(
                "benchmark",
                "engine_benchmark",
                "running",
                f"Benchmarking {spec.name}",
                engine=spec,
                current=position - 1,
                total=len(engines),
            )
            worker_nps, elapsed_ms = await asyncio.to_thread(
                engine.benchmark,
                assignment.benchmark_reference.timeout_s,
            )
            hardware_score = worker_nps / reference_nps
            hardware_scores[engine_id] = EngineHardwareScore(
                benchmark_nps=reference_nps,
                worker_nps=worker_nps,
                hardware_score=hardware_score,
                elapsed_ms=elapsed_ms,
            )
            await progress.publish(
                "benchmark",
                "engine_benchmark",
                "completed",
                f"Benchmarked {spec.name} at {worker_nps} NPS",
                engine=spec,
                current=position,
                total=len(engines),
                metadata={
                    "benchmark_nps": reference_nps,
                    "worker_nps": worker_nps,
                    "hardware_score": hardware_score,
                    "elapsed_ms": elapsed_ms,
                },
            )
        await progress.publish(
            "benchmark",
            "benchmark_all",
            "completed",
            f"Benchmarked all {len(engines)} assigned engines",
            current=len(engines),
            total=len(engines),
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
        while True:
            envelope = await inbox.get()
            if envelope.type == "assignment_complete":
                complete = AssignmentComplete.model_validate(envelope.data)
                _validate_assignment_message(complete, assignment, "assignment_complete")
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
                continue

            if envelope.type != "engine_command":
                raise ProtocolValidationError(f"unexpected runner message: {envelope.type}")

            command = EngineCommand.model_validate(envelope.data)
            _validate_assignment_message(command, assignment, "engine_command")
            LOG.info(
                "engine command received assignment_id=%s game_id=%s engine_id=%s command=%s",
                command.assignment_id,
                command.game_id,
                command.engine_id,
                command.command,
            )

            engine = engines.get(command.engine_id)
            if engine is None:
                raise ProtocolValidationError(f"assignment missing engine {command.engine_id}")

            command_stage, command_substage, command_detail = _command_progress(
                command.command,
                assignment.engines[command.engine_id].name,
                play_started=play_started,
            )
            if command.command.startswith("go"):
                play_started = True
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
                _EngineInfoPublisher(websocket, command, loop, send_lock, command_timer)
                if command.command.startswith("go")
                else None
            )
            line_callback = None if info_publisher is None else info_publisher.publish
            clock_task = (
                asyncio.create_task(
                    _publish_engine_clock(
                        websocket,
                        command,
                        send_lock,
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
                    )
                    if command_result is None:
                        await info_publisher.cancel()
                        if clock_task is not None:
                            clock_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await clock_task
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
            LOG.info(
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
) -> tuple[list[str], int] | None:
    while True:
        receive = asyncio.create_task(inbox.get())
        done, _pending = await asyncio.wait(
            {command_task, receive},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if receive not in done:
            receive.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await receive
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
        return None


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
    websocket,
    command: EngineCommand,
    send_lock: asyncio.Lock,
    command_timer: _CommandTimer,
) -> None:
    while True:
        await asyncio.sleep(ENGINE_CLOCK_SEND_INTERVAL_S)
        await _send_message(
            websocket,
            "engine_clock",
            EngineClock(
                **command.model_dump(exclude={"command"}),
                elapsed_ms=command_timer.elapsed_ms(),
            ),
            lock=send_lock,
        )


class _EngineInfoPublisher:
    """Keep engine stdout draining while bounding analysis traffic to the runner."""

    def __init__(
        self,
        websocket,
        command: EngineCommand,
        loop,
        send_lock: asyncio.Lock | None = None,
        command_timer: _CommandTimer | None = None,
    ) -> None:
        self._websocket = websocket
        self._command = command
        self._loop = loop
        self._send_lock = send_lock
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
            await _send_message(
                self._websocket,
                "engine_info",
                info,
                lock=self._send_lock,
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
    log = LOG.debug if message_type == "engine_info" else LOG.info
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


def _detect_hardware() -> HardwareInfo:
    logical_cores = os.cpu_count() or 1
    physical_cores = logical_cores
    ram_gb = 1
    ram_mb = 1024

    try:
        import psutil

        physical_cores = psutil.cpu_count(logical=False) or logical_cores
        logical_cores = psutil.cpu_count(logical=True) or logical_cores
        total_ram = psutil.virtual_memory().total
        ram_mb = max(1, total_ram // (1024**2))
        ram_gb = max(1, round(total_ram / (1024**3)))
    except ImportError:
        pass

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
        "detected hardware cpu=%s cores=%s ram=%s os=%s",
        hw.cpu_model,
        f"{hw.physical_cores}P/{hw.logical_cores}T",
        f"{hw.ram_gb}GB",
        hw.os,
    )
    return hw


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
