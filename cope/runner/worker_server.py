from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import secrets
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError
from websockets.exceptions import ConnectionClosed
from websockets.server import WebSocketServerProtocol, serve

from cope.core.models import (
    AssignmentCleanupComplete,
    AssignmentComplete,
    AssignmentFailed,
    AssignmentProgress,
    AssignmentReady,
    EngineClock,
    EngineCommand,
    EngineCommandStarted,
    EngineCommandResult,
    EngineInfo,
    EngineStop,
    PROTOCOL_VERSION,
    WorkerResources,
    WorkerSessionHello,
    WorkerTokenHello,
    WorkerUpdateCommand,
    WorkerUpdateStatus,
    WorkerWelcome,
)
from cope.core.protocol import (
    ProtocolError,
    ProtocolValidationError,
    decode_envelope,
    decode_message,
    encode_message,
    make_message,
)
from cope.db import (
    DEFAULT_DATABASE_URL,
    WorkerRecord,
    connect_database,
    disconnect_worker,
    fail_game_assignment,
    finish_game_assignment,
    acknowledge_game_assignment,
    get_game,
    get_worker,
    get_worker_by_session_id,
    get_worker_by_token,
    list_workers,
    record_worker_failure,
    record_game_hardware_score,
    record_game_assignment_progress,
    reconcile_worker_deployment,
    set_service_endpoint,
    touch_workers_seen,
    touch_service_heartbeat,
    update_worker_status,
    update_deployment_target_status,
    upsert_worker_connection,
    worker_token_is_valid,
)
from cope.network import DEFAULT_WORKER_PATH, default_worker_host, default_worker_port
from cope.runner.local import (
    next_worker_assignment,
    run_worker_assignment_game,
)
from cope.runner.events import (
    publish_tournament_event,
    publish_workers_changed,
    set_runner_wake_handler,
    start_event_publisher,
)
from cope.tournament.engine_instance import EngineCommandOutput


LOG = logging.getLogger("cope.worker_server")
WORKER_CONNECTION_REPLACED_CLOSE_CODE = 4001
ASSIGNABLE_WORKER_STATUSES = {"connected", "downloading", "ready", "busy"}
PREPARATION_FAILURE_INITIAL_BACKOFF_S = 60.0
PREPARATION_FAILURE_MAX_BACKOFF_S = 3600.0
RETIRED_ASSIGNMENT_GRACE_S = 60.0
LATE_ASSIGNMENT_MESSAGE_TYPES = {
    "assignment_progress",
    "assignment_cleanup_complete",
}
TRANSIENT_PLAY_PROGRESS = {
    "engine_command",
    "engine_search",
    "move_recorded",
    "move_turn",
    "position_sync",
}


class AssignmentPreparationFailed(RuntimeError):
    def __init__(self, failure: AssignmentFailed):
        self.failure = failure
        super().__init__(
            f"{failure.engine_name} {failure.stage} failed: {failure.error}"
        )


@dataclass(frozen=True)
class EnginePreparationBackoff:
    failures: int
    retry_at: float


@dataclass(frozen=True)
class WorkerServerConfig:
    host: str = field(default_factory=default_worker_host)
    port: int = field(default_factory=default_worker_port)
    db_path: str | Path = DEFAULT_DATABASE_URL
    expected_app_version: str | None = None
    heartbeat_interval_ms: int = 5000
    assignment_poll_interval_s: float = 10.0
    presence_flush_interval_s: float = 15.0
    game_thread_count: int = 2048


async def run_worker_server(config: WorkerServerConfig) -> None:
    try:
        threading.stack_size(1024 * 1024)
    except (RuntimeError, ValueError):
        LOG.warning("could not reduce game thread stack size")
    loop = asyncio.get_running_loop()
    loop.set_default_executor(
        ThreadPoolExecutor(
            max_workers=max(config.game_thread_count, 1),
            thread_name_prefix="cope-game",
        )
    )
    server = WorkerHandshakeServer(config)
    server.install_stream_wake_handler()
    start_event_publisher()
    orphaned_tournaments = server.reset_orphaned_worker_connections()
    if orphaned_tournaments:
        publish_workers_changed("worker.reset")
    for tournament_id in orphaned_tournaments:
        publish_tournament_event(tournament_id)
    heartbeat_interval_s = max(config.heartbeat_interval_ms / 1000, 0.5)
    ping_timeout_s = max(heartbeat_interval_s * 3, 15.0)
    await server.start_background_tasks()
    try:
        async with serve(
            server.handle_connection,
            config.host,
            config.port,
            ping_interval=heartbeat_interval_s,
            ping_timeout=ping_timeout_s,
            close_timeout=1,
            max_queue=32,
        ):
            _register_worker_endpoint(config)
            LOG.info(
                "listening for workers bind=%s:%s path=%s db=postgresql",
                config.host,
                config.port,
                DEFAULT_WORKER_PATH,
            )
            await asyncio.Future()
    finally:
        await server.stop_background_tasks()


def _register_worker_endpoint(config: WorkerServerConfig) -> None:
    connection = connect_database(config.db_path)
    try:
        set_service_endpoint(
            connection,
            service="worker-server",
            host=config.host,
            port=config.port,
            path=DEFAULT_WORKER_PATH,
        )
        connection.commit()
    finally:
        connection.close()


class WorkerConnectionInactive(RuntimeError):
    pass


class WorkerHandshakeServer:
    def __init__(self, config: WorkerServerConfig):
        self._config = config
        self._work_available = asyncio.Condition()
        self._work_generation = 0
        self._wake_pending = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._assignment_lock = asyncio.Lock()
        self._empty_claim_generation: dict[tuple, int] = {}
        self._engine_preparation_backoff: dict[
            tuple[str, str, int], EnginePreparationBackoff
        ] = {}
        self._connections: dict[
            int, tuple[str, WebSocketServerProtocol]
        ] = {}
        self._worker_capabilities: dict[int, tuple] = {}
        self._background_tasks: list[asyncio.Task] = []

    async def start_background_tasks(self) -> None:
        self._background_tasks = [
            asyncio.create_task(self._fallback_wake_loop(), name="worker-fallback-wake"),
            asyncio.create_task(self._presence_flush_loop(), name="worker-presence-flush"),
        ]

    async def stop_background_tasks(self) -> None:
        for task in self._background_tasks:
            task.cancel()
        for task in self._background_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._background_tasks.clear()
        await self._flush_worker_presence()

    async def _fallback_wake_loop(self) -> None:
        interval = max(self._config.assignment_poll_interval_s, 1.0)
        while True:
            await asyncio.sleep(interval)
            await self._wake_workers()

    async def _presence_flush_loop(self) -> None:
        interval = max(self._config.presence_flush_interval_s, 1.0)
        while True:
            await asyncio.sleep(interval)
            try:
                await self._flush_worker_presence()
            except Exception:
                LOG.exception("worker presence batch failed")

    async def _flush_worker_presence(self) -> None:
        sessions = [
            (worker_id, session_id)
            for worker_id, (session_id, _websocket) in self._connections.items()
        ]
        connection = connect_database(self._config.db_path)
        try:
            current = touch_workers_seen(connection, sessions) if sessions else set()
            touch_service_heartbeat(
                connection,
                "worker-server",
                self._config.expected_app_version or "dev",
            )
            connection.commit()
        finally:
            connection.close()

        stale = [item for item in sessions if item[0] not in current]
        for worker_id, session_id in stale:
            live = self._connections.get(worker_id)
            if live is None or live[0] != session_id:
                continue
            with contextlib.suppress(ConnectionClosed):
                await live[1].close(
                    code=WORKER_CONNECTION_REPLACED_CLOSE_CODE,
                    reason="worker session is no longer current",
                )

    def install_stream_wake_handler(self) -> None:
        self._loop = asyncio.get_running_loop()

        def wake_from_stream(_event) -> None:
            loop = self._loop
            if loop is None:
                return
            asyncio.run_coroutine_threadsafe(self._wake_workers(), loop)

        set_runner_wake_handler(wake_from_stream)

    def reset_orphaned_worker_connections(self) -> tuple[int, ...]:
        connection = connect_database(self._config.db_path)
        try:
            tournament_ids: set[int] = set()
            for worker in list_workers(connection):
                if worker.status not in ASSIGNABLE_WORKER_STATUSES:
                    continue
                tournament_ids.update(
                    disconnect_worker(
                        connection,
                        worker.id,
                        reason="worker server restarted",
                    )
                )
            connection.commit()
            return tuple(sorted(tournament_ids))
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def handle_connection(
        self,
        websocket: WebSocketServerProtocol,
        path: str | None = None,
    ) -> None:
        worker: WorkerRecord | None = None
        try:
            if path is not None and path != DEFAULT_WORKER_PATH:
                await websocket.close(code=4004, reason="unknown websocket path")
                return

            raw_message = await websocket.recv()
            hello = decode_message(
                raw_message,
                "hello",
                WorkerTokenHello
                | WorkerSessionHello,
            )
            authenticated_worker = self._authenticate_worker(hello)
            session_id = (
                authenticated_worker.session_id
                if isinstance(hello, WorkerSessionHello)
                else _new_session_id()
            )
            if not session_id:
                raise ProtocolValidationError("worker session is unavailable")
            label = _worker_label(authenticated_worker, hello)
            worker = self._record_connection(authenticated_worker, label, session_id, hello)
            update = self._worker_update_command(worker.id, hello.app_version)

            welcome = WorkerWelcome(
                worker_id=worker.id,
                session_id=session_id,
                heartbeat_interval_ms=self._config.heartbeat_interval_ms,
                capacity=WorkerResources(
                    threads=hello.hw.physical_cores,
                    hash_mb=hello.hw.total_ram_mb,
                ),
                update=update,
            )
            await _send_message(websocket, "welcome", welcome)
            previous = self._connections.get(worker.id)
            self._connections[worker.id] = (worker.session_id or "", websocket)
            if previous is not None and previous[1] is not websocket:
                with contextlib.suppress(ConnectionClosed):
                    await previous[1].close(
                        code=WORKER_CONNECTION_REPLACED_CLOSE_CODE,
                        reason="worker connection replaced",
                    )
            LOG.info("worker accepted worker_id=%s label=%s", worker.id, label)
            publish_workers_changed("worker.connected", {"worker_id": worker.id})
            if update is not None:
                await self._serve_worker_update(websocket, worker, update)
                return
            await self._wake_workers()
            await self._serve_worker(websocket, worker)
        except ProtocolError as error:
            LOG.warning("closing connection reason=%s", error)
            await websocket.close(code=error.close_code, reason=_close_reason(error))
        except ConnectionClosed:
            LOG.info("worker connection closed")
            return
        finally:
            if worker is not None:
                live = self._connections.get(worker.id)
                current_connection = live is not None and live[1] is websocket
                if current_connection:
                    self._connections.pop(worker.id, None)
                    self._worker_capabilities.pop(worker.id, None)
                    try:
                        tournament_ids = self._record_worker_disconnected(worker)
                        for tournament_id in tournament_ids:
                            publish_tournament_event(tournament_id)
                        await self._wake_workers()
                    except Exception:
                        LOG.exception("worker disconnect cleanup failed worker_id=%s", worker.id)

    async def _serve_worker(
        self,
        websocket: WebSocketServerProtocol,
        worker: WorkerRecord,
    ) -> None:
        wake_generation = self._work_generation
        inboxes: dict[int, asyncio.Queue] = {}
        assignment_identities: dict[int, tuple[str, int]] = {}
        retired_assignments: dict[tuple[int, str], tuple[int, float]] = {}
        assignments: dict[int, asyncio.Task] = {}
        assignment_resources: dict[int, WorkerResources] = {}
        send_lock = asyncio.Lock()
        receiver = asyncio.create_task(
            self._route_worker_messages(
                websocket,
                inboxes,
                assignment_identities,
                retired_assignments,
            ),
            name=f"worker-receiver-{worker.id}",
        )
        worker_status = "connected"
        try:
            while True:
                pending_update = self._worker_update_command(
                    worker.id,
                    worker.app_commit or "",
                )
                while pending_update is None and not websocket.closed:
                    assignment = await self._claim_next_assignment(
                        worker,
                        wake_generation,
                        (
                            sum(
                                resources.threads
                                for resources in assignment_resources.values()
                            ),
                            sum(
                                resources.hash_mb
                                for resources in assignment_resources.values()
                            ),
                        ),
                    )
                    if assignment is None:
                        break
                    assignment_id = assignment.assignment.assignment_id
                    inbox: asyncio.Queue = asyncio.Queue()
                    inboxes[assignment_id] = inbox
                    assignment_identities[assignment_id] = (
                        assignment.assignment.assignment_key,
                        assignment.assignment.game_id,
                    )
                    task = asyncio.create_task(
                        self._serve_worker_assignment(
                            websocket,
                            worker,
                            assignment,
                            inbox=inbox,
                            send_lock=send_lock,
                        ),
                        name=f"server-assignment-{assignment_id}",
                    )
                    assignments[assignment_id] = task
                    assignment_resources[assignment_id] = assignment.required_resources
                    if worker_status != "busy":
                        if not self._record_worker_status(
                            worker.id,
                            "busy",
                            session_id=worker.session_id,
                        ):
                            task.cancel()
                            raise WorkerConnectionInactive(
                                "worker session is no longer current"
                            )
                        worker_status = "busy"

                if pending_update is not None and not assignments:
                    receiver.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await receiver
                    await _send_message(
                        websocket,
                        "worker_update",
                        pending_update,
                        lock=send_lock,
                    )
                    await self._serve_worker_update(websocket, worker, pending_update)
                    return
                if not assignments and worker_status != "ready":
                    if not self._record_worker_status(
                        worker.id,
                        "ready",
                        session_id=worker.session_id,
                    ):
                        raise WorkerConnectionInactive(
                            "worker session is no longer current"
                        )
                    worker_status = "ready"

                work = asyncio.create_task(self._wait_for_work(wake_generation))
                done, _pending = await asyncio.wait(
                    {receiver, work, *assignments.values()},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if receiver in done:
                    work.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await work
                    receiver.result()
                    return
                if work in done:
                    wake_generation = work.result()
                else:
                    work.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await work

                completed = [
                    assignment_id
                    for assignment_id, task in assignments.items()
                    if task.done()
                ]
                for assignment_id in completed:
                    task = assignments.pop(assignment_id)
                    assignment_resources.pop(assignment_id, None)
                    inboxes.pop(assignment_id, None)
                    identity = assignment_identities.pop(assignment_id, None)
                    if identity is not None:
                        assignment_key, game_id = identity
                        retired_at = time.monotonic()
                        for retired_key, (_retired_game_id, expires_at) in tuple(
                            retired_assignments.items()
                        ):
                            if expires_at <= retired_at:
                                retired_assignments.pop(retired_key, None)
                        retired_assignments[(assignment_id, assignment_key)] = (
                            game_id,
                            retired_at + RETIRED_ASSIGNMENT_GRACE_S,
                        )
                    task.result()
                if completed:
                    await self._wake_workers()
        except WorkerConnectionInactive as error:
            LOG.info(
                "worker session inactive worker_id=%s reason=%s",
                worker.id,
                error,
            )
            with contextlib.suppress(ConnectionClosed):
                await websocket.close(
                    code=WORKER_CONNECTION_REPLACED_CLOSE_CODE,
                    reason=_close_reason(error),
                )
        finally:
            receiver.cancel()
            for task in assignments.values():
                task.cancel()
            await asyncio.gather(
                receiver,
                *assignments.values(),
                return_exceptions=True,
            )

    async def _route_worker_messages(
        self,
        websocket: WebSocketServerProtocol,
        inboxes: dict[int, asyncio.Queue],
        assignment_identities: dict[int, tuple[str, int]],
        retired_assignments: dict[tuple[int, str], tuple[int, float]],
    ) -> None:
        while True:
            envelope = decode_envelope(await websocket.recv())
            assignment_id = envelope.data.get("assignment_id")
            if not isinstance(assignment_id, int):
                raise ProtocolValidationError(
                    f"{envelope.type} message has no assignment id"
                )
            assignment_key = envelope.data.get("assignment_key")
            game_id = envelope.data.get("game_id")
            if not isinstance(assignment_key, str) or not isinstance(game_id, int):
                raise ProtocolValidationError(
                    f"{envelope.type} message has no assignment identity"
                )
            identity = assignment_identities.get(assignment_id)
            if identity == (assignment_key, game_id):
                inbox = inboxes.get(assignment_id)
                if inbox is not None:
                    await inbox.put(envelope)
                    continue
            retired = retired_assignments.get((assignment_id, assignment_key))
            if retired is not None:
                retired_game_id, expires_at = retired
                if expires_at <= time.monotonic():
                    retired_assignments.pop((assignment_id, assignment_key), None)
                elif (
                    game_id == retired_game_id
                    and envelope.type in LATE_ASSIGNMENT_MESSAGE_TYPES
                ):
                    self._validate_late_assignment_message(envelope.type, envelope.data)
                    LOG.info(
                        "ignoring late worker message type=%s assignment_id=%s game_id=%s",
                        envelope.type,
                        assignment_id,
                        game_id,
                    )
                    continue
            raise ProtocolValidationError(
                f"{envelope.type} references inactive assignment {assignment_id}"
            )

    @staticmethod
    def _validate_late_assignment_message(message_type: str, payload: dict) -> None:
        model = (
            AssignmentProgress
            if message_type == "assignment_progress"
            else AssignmentCleanupComplete
        )
        try:
            model.model_validate(payload)
        except ValidationError as error:
            raise ProtocolValidationError(str(error)) from error

    async def _serve_worker_assignment(
        self,
        websocket: WebSocketServerProtocol,
        worker: WorkerRecord,
        assignment,
        *,
        inbox: asyncio.Queue,
        send_lock: asyncio.Lock,
    ) -> None:
        payload = assignment.assignment
        LOG.info(
            "dispatching assignment worker_id=%s assignment_id=%s game_id=%s tournament=%s round=%s",
            worker.id,
            payload.assignment_id,
            payload.game_id,
            assignment.tournament_name,
            assignment.round,
        )
        transport = WorkerEngineTransport(
            websocket,
            assignment,
            inbox=inbox,
            send_lock=send_lock,
            failure_handler=lambda failure: self._record_runtime_failure(
                worker,
                assignment,
                failure,
            ),
            progress_handler=lambda progress: self._record_worker_progress(
                assignment,
                progress,
            ),
        )
        game_completed = False
        try:
            for step in assignment.workflow:
                self._record_assignment_progress(
                    assignment,
                    stage=step.key,
                    substage="waiting",
                    status="pending",
                    detail=f"{step.label} is waiting to start",
                )
            self._record_assignment_progress(
                assignment,
                stage="assignment",
                substage="dispatch",
                status="running",
                detail=f"Dispatching game {payload.game_id} to worker {worker.id}",
            )
            await _send_message(
                websocket,
                "assignment",
                assignment,
                lock=send_lock,
            )
            ready = await self._receive_assignment_ready(inbox, assignment)
            self._clear_engine_preparation_backoff(
                worker,
                ready.prepared_engine_ids,
            )
            self._acknowledge_assignment(worker, assignment, ready)
            self._record_assignment_progress(
                assignment,
                stage="benchmark",
                substage="ready_barrier",
                status="completed",
                detail=f"Worker {worker.id} hardware scores were validated and stored",
                current=len(ready.hardware_scores),
                total=len(assignment.engines),
                metadata={
                    "hardware_scores": {
                        str(engine_id): score.model_dump(mode="json")
                        for engine_id, score in ready.hardware_scores.items()
                    },
                    "benchmark_hardware_key": assignment.benchmark_reference.hardware_key,
                },
            )
            await asyncio.to_thread(
                self._run_assignment_game,
                assignment,
                transport,
                lambda stage, substage, status, detail, current=None, total=None, metadata=None:
                    self._record_assignment_progress(
                        assignment,
                        stage=stage,
                        substage=substage,
                        status=status,
                        detail=detail,
                        current=current,
                        total=total,
                        metadata=metadata,
                    ),
            )
            game_completed = True
        except AssignmentPreparationFailed as error:
            self._fail_preparation_assignment(worker, assignment, error.failure)
            LOG.error(
                "assignment preparation failed worker_id=%s machine_id=%s "
                "assignment_id=%s game_id=%s engine_id=%s engine=%s stage=%s error=%s",
                worker.id,
                worker.machine_id,
                payload.assignment_id,
                payload.game_id,
                error.failure.engine_id,
                error.failure.engine_name,
                error.failure.stage,
                error.failure.error,
            )
        except asyncio.CancelledError:
            try:
                self._fail_assignment(
                    assignment,
                    RuntimeError("worker assignment stopped"),
                )
            except Exception:
                LOG.exception(
                    "assignment cleanup failed worker_id=%s assignment_id=%s game_id=%s",
                    worker.id,
                    payload.assignment_id,
                    payload.game_id,
                )
            raise
        except Exception as error:
            with contextlib.suppress(Exception):
                self._record_assignment_progress(
                    assignment,
                    stage="conclude",
                    substage="pipeline_failed",
                    status="failed",
                    detail=(str(error).strip() or error.__class__.__name__)[:4000],
                )
            self._fail_assignment(assignment, error)
            LOG.exception(
                "assignment failed worker_id=%s assignment_id=%s game_id=%s",
                worker.id,
                payload.assignment_id,
                payload.game_id,
            )
            if websocket.closed:
                raise
        finally:
            transport.close()

        if not websocket.closed:
            await _send_message(
                websocket,
                "assignment_complete",
                AssignmentComplete(**payload.message_fields()),
                lock=send_lock,
            )
            await self._receive_assignment_cleanup(inbox, assignment)
            if game_completed:
                self._finish_assignment(assignment)
            LOG.info(
                "assignment complete worker_id=%s assignment_id=%s game_id=%s",
                worker.id,
                payload.assignment_id,
                payload.game_id,
            )

    def _finish_assignment(self, assignment) -> None:
        payload = assignment.assignment
        connection = connect_database(self._config.db_path)
        try:
            finish_game_assignment(
                connection,
                payload.assignment_id,
                payload.assignment_key,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _record_assignment_progress(
        self,
        assignment,
        *,
        stage: str,
        substage: str,
        status: str,
        detail: str,
        engine_id: int | None = None,
        engine_name: str | None = None,
        current: int | None = None,
        total: int | None = None,
        metadata: dict | None = None,
        source: str = "server",
    ) -> None:
        steps = {step.key: step for step in assignment.workflow}
        step = steps.get(stage)
        if step is None:
            raise ProtocolValidationError(f"assignment workflow has no {stage!r} stage")
        if source == "worker" and step.owner == "server":
            raise ProtocolValidationError(f"worker cannot update server workflow stage {stage!r}")
        if source == "server" and step.owner == "worker" and status != "pending":
            raise ProtocolValidationError(f"server cannot update worker workflow stage {stage!r}")
        if stage == "play" and substage in TRANSIENT_PLAY_PROGRESS and status != "failed":
            return
        progress = AssignmentProgress(
            **assignment.assignment.message_fields(),
            stage=stage,
            stage_label=step.label,
            stage_order=step.order,
            substage=substage,
            status=status,
            detail=detail,
            engine_id=engine_id,
            engine_name=engine_name,
            current=current,
            total=total,
            metadata=metadata or {},
        )
        connection = connect_database(self._config.db_path)
        try:
            record_game_assignment_progress(connection, progress, source=source)
            connection.commit()
        finally:
            connection.close()

    def _record_worker_progress(self, assignment, progress: AssignmentProgress) -> None:
        if not progress.matches_assignment(assignment.assignment):
            raise ProtocolValidationError("assignment progress mismatch")
        step = next(
            (item for item in assignment.workflow if item.key == progress.stage),
            None,
        )
        if (
            step is None
            or step.label != progress.stage_label
            or step.order != progress.stage_order
        ):
            raise ProtocolValidationError("assignment progress stage is not in the workflow")
        if progress.engine_id is not None:
            engine = assignment.engines.get(progress.engine_id)
            if engine is None or engine.name != progress.engine_name:
                raise ProtocolValidationError("assignment progress engine mismatch")
        self._record_assignment_progress(
            assignment,
            stage=progress.stage,
            substage=progress.substage,
            status=progress.status,
            detail=progress.detail,
            engine_id=progress.engine_id,
            engine_name=progress.engine_name,
            current=progress.current,
            total=progress.total,
            metadata=progress.metadata,
            source="worker",
        )

    async def _wake_workers(self) -> None:
        if self._wake_pending:
            return
        self._wake_pending = True
        try:
            await asyncio.sleep(0.05)
            async with self._work_available:
                self._work_generation += 1
                self._empty_claim_generation.clear()
                self._work_available.notify_all()
        finally:
            self._wake_pending = False

    async def _wait_for_work(self, wake_generation: int) -> int:
        async with self._work_available:
            await self._work_available.wait_for(
                lambda: self._work_generation != wake_generation
            )
            return self._work_generation

    def _worker_update_command(
        self,
        worker_id: int,
        app_commit: str,
    ) -> WorkerUpdateCommand | None:
        connection = connect_database(self._config.db_path)
        try:
            target = reconcile_worker_deployment(connection, worker_id, app_commit)
            if target is None:
                connection.commit()
                return None
            if not target.target_commit or not target.repository_url:
                update_deployment_target_status(
                    connection,
                    target.id,
                    "failed",
                    current_commit=app_commit,
                    detail="Deployment repository URL is unavailable.",
                )
                connection.commit()
                raise ProtocolValidationError("worker deployment source is unavailable")
            connection.commit()
            return WorkerUpdateCommand(
                job_id=target.job_id,
                target_commit=target.target_commit,
                repository_url=target.repository_url,
            )
        finally:
            connection.close()

    async def _serve_worker_update(
        self,
        websocket: WebSocketServerProtocol,
        worker: WorkerRecord,
        command: WorkerUpdateCommand,
    ) -> None:
        while True:
            status = await _receive_message(
                websocket,
                "worker_update_status",
                WorkerUpdateStatus,
            )
            if status.job_id != command.job_id or status.target_commit != command.target_commit:
                raise ProtocolValidationError("worker update status does not match the deployment")
            connection = connect_database(self._config.db_path)
            try:
                target = reconcile_worker_deployment(
                    connection,
                    worker.id,
                    worker.app_commit or "",
                )
                if target is None:
                    connection.commit()
                    return
                mapped = {
                    "accepted": "updating",
                    "installing": "updating",
                    "restarting": "restarting",
                    "failed": "failed",
                }[status.status]
                update_deployment_target_status(
                    connection,
                    target.id,
                    mapped,
                    current_commit=worker.app_commit,
                    detail=status.detail,
                )
                connection.commit()
            finally:
                connection.close()
            publish_workers_changed(
                "worker.update",
                {"worker_id": worker.id, "status": status.status},
            )
            if status.status == "restarting":
                await websocket.close(code=1000, reason="worker update installed")
                return
            if status.status == "failed":
                await websocket.close(code=1011, reason="worker update failed")
                return

    def _authenticate_worker(
        self,
        hello: WorkerTokenHello | WorkerSessionHello,
    ) -> WorkerRecord:
        connection = connect_database(self._config.db_path)
        try:
            if isinstance(hello, WorkerTokenHello):
                worker = get_worker_by_token(connection, hello.token)
                if worker is None or not worker_token_is_valid(worker):
                    raise ProtocolValidationError("invalid or expired worker token")
                return worker

            worker = get_worker_by_session_id(connection, hello.session_id)
            if worker is None or worker.status == "revoked":
                raise ProtocolValidationError("invalid worker session")
            return worker
        finally:
            connection.close()

    def _record_connection(
        self,
        worker: WorkerRecord,
        label: str,
        session_id: str,
        hello: WorkerTokenHello | WorkerSessionHello,
    ) -> WorkerRecord:
        connection = connect_database(self._config.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._validate_worker_machine(connection, worker, hello)
            tournament_ids = disconnect_worker(
                connection,
                worker.id,
                reason="worker session replaced",
            )
            upsert_worker_connection(
                connection,
                worker_id=worker.id,
                label=label,
                session_id=session_id,
                app_commit=hello.app_version,
                protocol_version=PROTOCOL_VERSION,
                machine_id=hello.machine_id,
                hw=hello.hw,
            )
            current = get_worker(connection, worker.id)
            if current is None or current.status == "revoked" or current.session_id != session_id:
                raise ProtocolValidationError("worker registration was revoked")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        for tournament_id in tournament_ids:
            publish_tournament_event(tournament_id)
        return current

    def _validate_worker_machine(
        self,
        connection,
        worker: WorkerRecord,
        hello: WorkerTokenHello | WorkerSessionHello,
    ) -> None:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
            (hello.machine_id,),
        )
        machine = connection.execute(
            """
            SELECT id, label
            FROM workers
            WHERE id != ?
              AND machine_id = ?
            ORDER BY id
            LIMIT 1
            """,
            (worker.id, hello.machine_id),
        ).fetchone()
        if machine is not None:
            raise ProtocolValidationError(
                f"machine is already registered as worker {machine['id']} ({machine['label']})"
            )

    def _record_worker_status(
        self,
        worker_id: int,
        status: str,
        *,
        session_id: str | None = None,
    ) -> bool:
        connection = connect_database(self._config.db_path)
        try:
            updated = update_worker_status(
                connection,
                worker_id,
                status,
                session_id=session_id,
            )
            connection.commit()
            if updated:
                publish_workers_changed("worker.status", {"worker_id": worker_id, "status": status})
            return updated
        finally:
            connection.close()

    def _record_worker_disconnected(self, worker: WorkerRecord) -> tuple[int, ...]:
        connection = connect_database(self._config.db_path)
        try:
            tournament_ids = disconnect_worker(
                connection,
                worker.id,
                session_id=worker.session_id,
                reason="worker connection lost",
            )
            connection.commit()
            publish_workers_changed("worker.disconnected", {"worker_id": worker.id})
            return tournament_ids
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def _claim_next_assignment(
        self,
        worker: WorkerRecord,
        wake_generation: int,
        used_resources: tuple[int, int],
    ):
        capability = self._worker_capabilities.get(
            worker.id,
            _worker_capability_key(worker),
        )
        if self._empty_claim_generation.get(capability) == wake_generation:
            return None
        async with self._assignment_lock:
            if self._empty_claim_generation.get(capability) == wake_generation:
                return None
            assignment = self._claim_next_assignment_from_database(
                worker,
                used_resources,
            )
            if assignment is None:
                self._empty_claim_generation[capability] = wake_generation
            return assignment

    def _claim_next_assignment_from_database(
        self,
        worker: WorkerRecord,
        used_resources: tuple[int, int],
    ):
        connection = connect_database(self._config.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            live_worker = self._validate_assignable_worker(connection, worker)
            blocked_engine_ids = self._blocked_engine_ids(live_worker)
            assignment = (
                None
                if 0 in blocked_engine_ids
                else next_worker_assignment(
                    connection,
                    live_worker,
                    used_resources=used_resources,
                    excluded_engine_ids=blocked_engine_ids,
                )
            )
            if assignment is not None:
                connection.commit()
                game = get_game(connection, assignment.assignment.game_id)
                if game is not None:
                    publish_tournament_event(game.tournament_id)
            return assignment
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _validate_assignable_worker(
        self,
        connection,
        worker: WorkerRecord,
    ) -> WorkerRecord:
        live_worker = get_worker(connection, worker.id)
        if live_worker is None:
            raise WorkerConnectionInactive("worker record was deleted")
        if live_worker.status == "revoked":
            raise WorkerConnectionInactive("worker was revoked")
        if live_worker.session_id != worker.session_id:
            raise WorkerConnectionInactive("worker session was replaced")
        if live_worker.status not in ASSIGNABLE_WORKER_STATUSES:
            raise WorkerConnectionInactive(f"worker is {live_worker.status}")
        return live_worker

    def _run_assignment_game(self, assignment, transport, progress_handler) -> None:
        connection = connect_database(self._config.db_path)
        try:
            run_worker_assignment_game(
                connection,
                assignment,
                transport,
                progress_handler=progress_handler,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def _receive_assignment_ready(
        self,
        inbox: asyncio.Queue,
        assignment,
    ) -> AssignmentReady:
        while True:
            envelope = await inbox.get()
            if envelope.type == "assignment_progress":
                progress = AssignmentProgress.model_validate(envelope.data)
                self._record_worker_progress(assignment, progress)
                continue
            break
        if envelope.type == "assignment_failed":
            failure = AssignmentFailed.model_validate(envelope.data)
            if not failure.matches_assignment(assignment.assignment):
                raise ProtocolValidationError("assignment failure mismatch")
            if failure.engine_id not in assignment.engines:
                raise ProtocolValidationError("assignment failure references unknown engine")
            expected_name = assignment.engines[failure.engine_id].name
            if failure.engine_name != expected_name:
                raise ProtocolValidationError("assignment failure engine name mismatch")
            raise AssignmentPreparationFailed(failure)
        if envelope.type != "assignment_ready":
            raise ProtocolValidationError(
                f"expected assignment_ready, got {envelope.type}"
            )
        ready = AssignmentReady.model_validate(envelope.data)
        if not ready.matches_assignment(assignment.assignment):
            raise ProtocolValidationError("assignment_ready assignment mismatch")
        expected = set(assignment.engines)
        if set(ready.prepared_engine_ids) != expected:
            raise ProtocolValidationError(
                "assignment_ready must include every assigned engine"
            )
        if set(ready.hardware_scores) != expected:
            raise ProtocolValidationError(
                "assignment_ready must include hardware scores for every assigned engine"
            )
        for engine_id, score in ready.hardware_scores.items():
            benchmark_nps = assignment.benchmark_reference.engine_nps[engine_id]
            if score.benchmark_nps != benchmark_nps:
                raise ProtocolValidationError("assignment_ready benchmark NPS mismatch")
            calculated = score.worker_nps / benchmark_nps
            if not math.isclose(score.hardware_score, calculated, rel_tol=1e-12):
                raise ProtocolValidationError("assignment_ready hardware score mismatch")
        return ready

    async def _receive_assignment_cleanup(
        self,
        inbox: asyncio.Queue,
        assignment,
    ) -> None:
        while True:
            envelope = await inbox.get()
            if envelope.type == "assignment_progress":
                progress = AssignmentProgress.model_validate(envelope.data)
                self._record_worker_progress(assignment, progress)
                continue
            if envelope.type != "assignment_cleanup_complete":
                raise ProtocolValidationError(
                    f"expected assignment_cleanup_complete, got {envelope.type}"
                )
            complete = AssignmentCleanupComplete.model_validate(envelope.data)
            if not complete.matches_assignment(assignment.assignment):
                raise ProtocolValidationError("assignment cleanup mismatch")
            return

    def _acknowledge_assignment(
        self,
        worker: WorkerRecord,
        assignment,
        ready: AssignmentReady,
    ) -> None:
        connection = connect_database(self._config.db_path)
        try:
            acknowledge_game_assignment(
                connection,
                ready.assignment_id,
                ready.assignment_key,
            )
            colors = {
                engine_id: color.name.lower()
                for color, engine_id in assignment.assignment.slots.items()
            }
            for engine_id, score in ready.hardware_scores.items():
                record_game_hardware_score(
                    connection,
                    game_id=ready.game_id,
                    assignment_id=ready.assignment_id,
                    worker_id=worker.id,
                    engine_version_id=engine_id,
                    color=colors[engine_id],
                    benchmark_hardware_key=assignment.benchmark_reference.hardware_key,
                    benchmark_nps=score.benchmark_nps,
                    worker_nps=score.worker_nps,
                    hardware_score=score.worker_nps / score.benchmark_nps,
                    elapsed_ms=score.elapsed_ms,
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _fail_assignment(self, assignment, error: Exception) -> None:
        connection = connect_database(self._config.db_path)
        try:
            fail_game_assignment(
                connection,
                assignment.assignment.assignment_id,
                assignment.assignment.assignment_key,
                str(error) or error.__class__.__name__,
            )
            game = get_game(connection, assignment.assignment.game_id)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        if game is not None:
            publish_tournament_event(game.tournament_id)
        publish_workers_changed(
            "assignment.failed",
            {"assignment_id": assignment.assignment.assignment_id},
        )

    def _fail_preparation_assignment(
        self,
        worker: WorkerRecord,
        assignment,
        failure: AssignmentFailed,
    ) -> None:
        self._record_engine_preparation_backoff(worker, failure)
        connection = connect_database(self._config.db_path)
        try:
            fail_game_assignment(
                connection,
                assignment.assignment.assignment_id,
                assignment.assignment.assignment_key,
                str(AssignmentPreparationFailed(failure)),
            )
            record_worker_failure(
                connection,
                worker=worker,
                assignment_id=assignment.assignment.assignment_id,
                game_id=assignment.assignment.game_id,
                engine_id=failure.engine_id,
                engine_name=failure.engine_name,
                stage=failure.stage,
                error=failure.error,
            )
            game = get_game(connection, assignment.assignment.game_id)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        if game is not None:
            publish_tournament_event(game.tournament_id)
        publish_workers_changed(
            "worker.engine_failure",
            {
                "worker_id": worker.id,
                "machine_id": worker.machine_id,
                "assignment_id": assignment.assignment.assignment_id,
                "game_id": assignment.assignment.game_id,
                "engine_id": failure.engine_id,
                "stage": failure.stage,
            },
        )

    def _record_engine_preparation_backoff(
        self,
        worker: WorkerRecord,
        failure: AssignmentFailed,
    ) -> None:
        blocked_engine_id = (
            0 if _is_machine_wide_preparation_failure(failure) else failure.engine_id
        )
        key = _engine_preparation_backoff_key(worker, blocked_engine_id)
        previous = self._engine_preparation_backoff.get(key)
        failures = 1 if previous is None else previous.failures + 1
        exponent = min(failures - 1, 6)
        delay_s = min(
            PREPARATION_FAILURE_INITIAL_BACKOFF_S * (2 ** exponent),
            PREPARATION_FAILURE_MAX_BACKOFF_S,
        )
        self._engine_preparation_backoff[key] = EnginePreparationBackoff(
            failures=failures,
            retry_at=time.monotonic() + delay_s,
        )
        LOG.warning(
            "suppressing engine assignments after preparation failure worker_id=%s "
            "machine_id=%s engine_id=%s failures=%s retry_in_s=%.0f",
            worker.id,
            worker.machine_id,
            blocked_engine_id,
            failures,
            delay_s,
        )

    def _clear_engine_preparation_backoff(
        self,
        worker: WorkerRecord,
        engine_ids: list[int],
    ) -> None:
        self._engine_preparation_backoff.pop(
            _engine_preparation_backoff_key(worker, 0),
            None,
        )
        for engine_id in engine_ids:
            self._engine_preparation_backoff.pop(
                _engine_preparation_backoff_key(worker, engine_id),
                None,
            )

    def _blocked_engine_ids(self, worker: WorkerRecord) -> frozenset[int]:
        machine_key, app_commit, _engine_id = _engine_preparation_backoff_key(worker, 0)
        now = time.monotonic()
        return frozenset(
            engine_id
            for (candidate_machine, candidate_commit, engine_id), backoff
            in self._engine_preparation_backoff.items()
            if candidate_machine == machine_key
            and candidate_commit == app_commit
            and backoff.retry_at > now
        )

    def _record_runtime_failure(
        self,
        worker: WorkerRecord,
        assignment,
        failure: AssignmentFailed,
    ) -> None:
        connection = connect_database(self._config.db_path)
        try:
            record_worker_failure(
                connection,
                worker=worker,
                assignment_id=assignment.assignment.assignment_id,
                game_id=assignment.assignment.game_id,
                engine_id=failure.engine_id,
                engine_name=failure.engine_name,
                stage=failure.stage,
                error=failure.error,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        publish_workers_changed(
            "worker.engine_failure",
            {
                "worker_id": worker.id,
                "machine_id": worker.machine_id,
                "assignment_id": assignment.assignment.assignment_id,
                "game_id": assignment.assignment.game_id,
                "engine_id": failure.engine_id,
                "stage": failure.stage,
            },
        )


def _worker_capability_key(worker: WorkerRecord) -> tuple:
    return (worker.id,)


def _engine_preparation_backoff_key(
    worker: WorkerRecord,
    engine_id: int,
) -> tuple[str, str, int]:
    machine_key = worker.machine_id or f"worker:{worker.id}"
    return machine_key, worker.app_commit or "", engine_id


def _is_machine_wide_preparation_failure(failure: AssignmentFailed) -> bool:
    detail = failure.error.lower()
    return "buildx component is missing or broken" in detail


class WorkerEngineTransport:
    def __init__(
        self,
        websocket: WebSocketServerProtocol,
        assignment,
        *,
        inbox: asyncio.Queue,
        send_lock: asyncio.Lock,
        failure_handler: Callable[[AssignmentFailed], None],
        progress_handler: Callable[[AssignmentProgress], None],
    ):
        self._websocket = websocket
        self._assignment = assignment
        self._inbox = inbox
        self._send_lock = send_lock
        self._loop = asyncio.get_running_loop()
        self._command_lock = asyncio.Lock()
        self._closed = threading.Event()
        self._pending: set[Future] = set()
        self._pending_lock = threading.Lock()
        self._failure_handler = failure_handler
        self._progress_handler = progress_handler
        self._failure_reported = False
        self._clock_samples: dict[int, tuple[int, bool]] = {}
        self._clock_lock = threading.Lock()

    def close(self) -> None:
        self._closed.set()
        with self._pending_lock:
            pending = tuple(self._pending)
        for future in pending:
            future.cancel()

    def execute_engine_command(
        self,
        engine_id: int,
        command: str,
        info_handler: Callable[[str], None] | None = None,
    ) -> EngineCommandOutput:
        with self._pending_lock:
            if self._closed.is_set():
                raise RuntimeError("worker transport closed")
            future = asyncio.run_coroutine_threadsafe(
                self._execute_engine_command(engine_id, command, info_handler),
                self._loop,
            )
            self._pending.add(future)
        try:
            return future.result()
        finally:
            with self._pending_lock:
                self._pending.discard(future)

    def current_command_elapsed_ms(self, engine_id: int) -> int | None:
        with self._clock_lock:
            sample = self._clock_samples.get(engine_id)
        if sample is None:
            return None
        return sample[0]

    def begin_engine_search(self, engine_id: int) -> None:
        with self._clock_lock:
            self._clock_samples.pop(engine_id, None)

    def stop_engine_search(self, engine_id: int) -> None:
        if self._closed.is_set():
            return
        future = asyncio.run_coroutine_threadsafe(
            self._send_engine_stop(engine_id),
            self._loop,
        )
        future.result()

    async def _send_engine_stop(self, engine_id: int) -> None:
        assignment = self._assignment.assignment
        await _send_message(
            self._websocket,
            "engine_stop",
            EngineStop(
                **assignment.message_fields(),
                engine_id=engine_id,
            ),
            lock=self._send_lock,
        )

    async def _execute_engine_command(
        self,
        engine_id: int,
        command: str,
        info_handler: Callable[[str], None] | None,
    ) -> EngineCommandOutput:
        async with self._command_lock:
            return await self._execute_engine_command_locked(engine_id, command, info_handler)

    async def _execute_engine_command_locked(
        self,
        engine_id: int,
        command: str,
        info_handler: Callable[[str], None] | None,
    ) -> EngineCommandOutput:
        assignment = self._assignment.assignment
        is_search = command.startswith("go")
        with self._clock_lock:
            self._clock_samples.pop(engine_id, None)
        await _send_message(
            self._websocket,
            "engine_command",
            EngineCommand(
                **assignment.message_fields(),
                engine_id=engine_id,
                command=command,
            ),
            lock=self._send_lock,
        )

        while True:
            envelope = await self._inbox.get()
            if envelope.type == "assignment_progress":
                progress = _validate_worker_payload(AssignmentProgress, envelope.data)
                self._progress_handler(progress)
                continue
            if envelope.type == "engine_command_started":
                started = _validate_worker_payload(EngineCommandStarted, envelope.data)
                self._validate_engine_reply(started, engine_id)
                if is_search:
                    self._record_clock_sample(engine_id, 0, running=True)
                continue

            if envelope.type == "engine_clock":
                clock = _validate_worker_payload(EngineClock, envelope.data)
                self._validate_engine_reply(clock, engine_id)
                if is_search:
                    self._record_clock_sample(engine_id, clock.elapsed_ms, running=True)
                continue

            if envelope.type == "engine_info":
                info = _validate_worker_payload(EngineInfo, envelope.data)
                self._validate_engine_reply(info, engine_id)
                if is_search:
                    self._record_clock_sample(engine_id, info.elapsed_ms, running=True)
                if info_handler is not None:
                    for line in info.lines:
                        try:
                            info_handler(line)
                        except Exception:
                            LOG.exception(
                                "engine info handler failed assignment_id=%s game_id=%s engine_id=%s",
                                assignment.assignment_id,
                                assignment.game_id,
                                engine_id,
                            )
                continue

            if envelope.type == "assignment_failed":
                failure = _validate_worker_payload(AssignmentFailed, envelope.data)
                if not failure.matches_assignment(assignment):
                    raise ProtocolValidationError("engine failure assignment mismatch")
                if failure.engine_id != engine_id:
                    raise ProtocolValidationError("engine failure engine mismatch")
                if not self._failure_reported:
                    self._failure_reported = True
                    self._failure_handler(failure)
                raise RuntimeError(
                    f"{failure.engine_name} {failure.stage} failed: {failure.error}"
                )

            if envelope.type != "engine_command_result":
                raise ProtocolValidationError(f"unexpected worker message: {envelope.type}")

            result = _validate_worker_payload(EngineCommandResult, envelope.data)
            self._validate_engine_reply(result, engine_id)
            if is_search:
                self._record_clock_sample(engine_id, result.elapsed_ms, running=False)
            return EngineCommandOutput(lines=result.lines, elapsed_ms=result.elapsed_ms)

    def _record_clock_sample(
        self,
        engine_id: int,
        elapsed_ms: int,
        *,
        running: bool,
    ) -> None:
        with self._clock_lock:
            current = self._clock_samples.get(engine_id)
            if current is not None:
                elapsed_ms = max(elapsed_ms, current[0])
            self._clock_samples[engine_id] = (elapsed_ms, running)

    def _validate_engine_reply(
        self,
        result: EngineCommandResult | EngineCommandStarted | EngineClock | EngineInfo,
        engine_id: int,
    ) -> None:
        if not result.matches_assignment(self._assignment.assignment) or result.engine_id != engine_id:
            assignment = self._assignment.assignment
            raise ProtocolValidationError(
                "engine reply mismatch: "
                f"expected assignment_id={assignment.assignment_id} "
                f"game_id={assignment.game_id} engine_id={engine_id}, "
                f"got assignment_id={result.assignment_id} "
                f"game_id={result.game_id} engine_id={result.engine_id}"
            )


def _new_session_id() -> str:
    return secrets.token_urlsafe(32)


async def _send_message(
    websocket: WebSocketServerProtocol,
    message_type: str,
    data,
    *,
    lock: asyncio.Lock | None = None,
) -> None:
    payload = encode_message(make_message(message_type, data))
    if lock is None:
        await websocket.send(payload)
        return
    async with lock:
        await websocket.send(payload)


async def _receive_message(websocket, message_type: str, data_type):
    return decode_message(await websocket.recv(), message_type, data_type)


def _validate_worker_payload(model_type, data):
    try:
        return model_type.model_validate(data)
    except ValidationError as error:
        raise ProtocolValidationError(str(error)) from error


def _hello_label(
    hello: WorkerTokenHello | WorkerSessionHello,
) -> str:
    if isinstance(hello, WorkerTokenHello):
        return hello.label_hint or "token worker"

    return "session worker"


def _worker_label(
    worker: WorkerRecord,
    hello: WorkerTokenHello | WorkerSessionHello,
) -> str:
    if worker.label:
        return worker.label

    return _hello_label(hello)


def _close_reason(error: Exception) -> str:
    reason = str(error)
    if len(reason) <= 120:
        return reason

    return f"{reason[:117]}..."
