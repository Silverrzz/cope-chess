from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from websockets.exceptions import ConnectionClosed
from websockets.server import WebSocketServerProtocol, serve

from cope.core.benchmark import benchmark_hardware_key
from cope.core.models import (
    PROTOCOL_VERSION,
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
    ProtocolError,
    ProtocolValidationError,
    decode_envelope,
    decode_message,
    encode_message,
    make_message,
)
from cope.db import (
    DEFAULT_DB_PATH,
    BenchmarkerRecord,
    BenchmarkJobRecord,
    benchmarker_token_is_valid,
    claim_benchmark_job,
    complete_benchmark_job,
    connect_database,
    disconnect_benchmarker,
    fail_benchmark_job,
    get_benchmarker_by_session_id,
    get_benchmarker_by_token,
    reconcile_benchmarker_deployment,
    record_benchmark_progress,
    register_benchmarker_connection,
    reset_benchmark_service_state,
    set_service_endpoint,
    touch_service_heartbeat,
    update_benchmarker_status,
    update_deployment_target_status,
)
from cope.network import (
    DEFAULT_BENCHMARKER_PATH,
    default_benchmark_server_host,
    default_benchmark_server_port,
)


LOG = logging.getLogger("cope.benchmark_server")
CONNECTION_REPLACED_CLOSE_CODE = 4001


@dataclass(frozen=True)
class BenchmarkServerConfig:
    host: str = field(default_factory=default_benchmark_server_host)
    port: int = field(default_factory=default_benchmark_server_port)
    db_path: str | Path = DEFAULT_DB_PATH
    expected_app_version: str | None = None
    poll_interval_s: float = 30.0
    retry_interval_s: int = 3600
    preparation_timeout_s: int = 1800
    benchmark_timeout_s: int = 600
    response_timeout_s: int = 7200


async def run_benchmark_server(config: BenchmarkServerConfig) -> None:
    server = BenchmarkServer(config)
    server.reset_service_state()
    await server.start_background_tasks()
    try:
        async with serve(
            server.handle_connection,
            config.host,
            config.port,
            ping_interval=15,
            ping_timeout=45,
            close_timeout=1,
            max_queue=8,
            max_size=128_000,
        ):
            server.register_endpoint()
            LOG.info(
                "listening for benchmarkers bind=%s:%s path=%s db=postgresql",
                config.host,
                config.port,
                DEFAULT_BENCHMARKER_PATH,
            )
            await asyncio.Future()
    finally:
        await server.stop_background_tasks()


class BenchmarkServer:
    def __init__(self, config: BenchmarkServerConfig):
        self._config = config
        self._connections: dict[int, tuple[str, WebSocketServerProtocol]] = {}
        self._background_tasks: list[asyncio.Task] = []

    async def start_background_tasks(self) -> None:
        self._background_tasks = [
            asyncio.create_task(self._heartbeat_loop(), name="benchmark-server-heartbeat")
        ]

    async def stop_background_tasks(self) -> None:
        for task in self._background_tasks:
            task.cancel()
        for task in self._background_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._background_tasks.clear()

    def reset_service_state(self) -> None:
        connection = connect_database(self._config.db_path)
        try:
            reset_benchmark_service_state(
                connection,
                retry_seconds=self._config.retry_interval_s,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def register_endpoint(self) -> None:
        connection = connect_database(self._config.db_path)
        try:
            set_service_endpoint(
                connection,
                service="benchmark-server",
                host=self._config.host,
                port=self._config.port,
                path=DEFAULT_BENCHMARKER_PATH,
            )
            connection.commit()
        finally:
            connection.close()

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(15)
            connection = connect_database(self._config.db_path)
            try:
                touch_service_heartbeat(
                    connection,
                    "benchmark-server",
                    self._config.expected_app_version or "dev",
                )
                connection.commit()
            finally:
                connection.close()

    async def handle_connection(
        self,
        websocket: WebSocketServerProtocol,
        path: str | None = None,
    ) -> None:
        benchmarker: BenchmarkerRecord | None = None
        active_job: BenchmarkJobRecord | None = None
        try:
            if path is not None and path != DEFAULT_BENCHMARKER_PATH:
                await websocket.close(code=4004, reason="unknown websocket path")
                return
            hello = decode_message(
                await websocket.recv(),
                "benchmark_hello",
                BenchmarkerTokenHello | BenchmarkerSessionHello,
            )
            self._validate_hello(hello)
            authenticated = self._authenticate(hello)
            session_id = secrets.token_urlsafe(32)
            benchmarker = self._record_connection(
                authenticated,
                session_id,
                hello,
            )
            update = self._benchmarker_update_command(
                benchmarker.id,
                hello.app_version,
                hello.supports_updates,
            )
            old = self._connections.get(benchmarker.id)
            self._connections[benchmarker.id] = (session_id, websocket)
            if old is not None and old[0] != session_id:
                with contextlib.suppress(ConnectionClosed):
                    await old[1].close(
                        code=CONNECTION_REPLACED_CLOSE_CODE,
                        reason="benchmarker session replaced",
                    )
            welcome = BenchmarkerWelcome(
                benchmarker_id=benchmarker.id,
                session_id=session_id,
                poll_interval_ms=max(
                    1,
                    round(self._config.poll_interval_s * 1000),
                ),
                update=update,
            )
            await websocket.send(
                encode_message(
                    make_message(
                        "benchmark_welcome",
                        welcome.model_dump(mode="json", exclude_none=True),
                    )
                )
            )
            LOG.info(
                "benchmarker accepted benchmarker_id=%s label=%s hardware_key=%s",
                benchmarker.id,
                benchmarker.label,
                benchmarker.hardware_key,
            )
            if update is not None:
                await self._serve_benchmarker_update(websocket, benchmarker, update)
                return
            while True:
                pending_update = self._benchmarker_update_command(
                    benchmarker.id,
                    benchmarker.app_commit or "",
                    hello.supports_updates,
                )
                if pending_update is not None:
                    await websocket.send(
                        encode_message(
                            make_message("benchmarker_update", pending_update)
                        )
                    )
                    await self._serve_benchmarker_update(
                        websocket,
                        benchmarker,
                        pending_update,
                    )
                    return
                active_job = self._claim_next_job(benchmarker)
                if active_job is None:
                    closed = asyncio.create_task(websocket.wait_closed())
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(closed),
                            timeout=max(self._config.poll_interval_s, 1.0),
                        )
                        return
                    except asyncio.TimeoutError:
                        closed.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await closed
                        continue

                self._set_status(benchmarker, "busy")
                assignment = BenchmarkAssignment(
                    job_id=active_job.id,
                    job_key=active_job.job_key,
                    hardware_key=active_job.hardware_key,
                    engine=active_job.engine,
                    preparation_timeout_s=self._config.preparation_timeout_s,
                    timeout_s=self._config.benchmark_timeout_s,
                )
                assignment_payload = assignment.model_dump(
                    mode="json",
                    exclude=(
                        set()
                        if benchmarker.app_commit == self._config.expected_app_version
                        else {"preparation_timeout_s"}
                    ),
                )
                await websocket.send(
                    encode_message(make_message("benchmark_assignment", assignment_payload))
                )
                while True:
                    envelope = decode_envelope(
                        await asyncio.wait_for(
                            websocket.recv(),
                            timeout=max(self._config.response_timeout_s, 1),
                        )
                    )
                    if envelope.type != "benchmark_progress":
                        break
                    progress = BenchmarkProgress.model_validate(envelope.data)
                    self._record_progress(benchmarker, active_job, progress)
                if envelope.type == "benchmark_result":
                    result = BenchmarkResult.model_validate(envelope.data)
                    self._complete_job(benchmarker, active_job, result)
                    LOG.info(
                        "benchmark complete benchmarker_id=%s job_id=%s engine=%s "
                        "version=%s nps=%s hardware_key=%s",
                        benchmarker.id,
                        active_job.id,
                        active_job.engine_name,
                        active_job.engine_version,
                        result.nps,
                        active_job.hardware_key,
                    )
                elif envelope.type == "benchmark_failed":
                    failure = BenchmarkFailed.model_validate(envelope.data)
                    self._fail_reported_job(benchmarker, active_job, failure)
                    LOG.warning(
                        "benchmark failed benchmarker_id=%s job_id=%s engine=%s "
                        "stage=%s error=%s",
                        benchmarker.id,
                        active_job.id,
                        active_job.engine_name,
                        failure.stage,
                        failure.error,
                    )
                else:
                    raise ProtocolValidationError(
                        f"expected benchmark_result or benchmark_failed, got {envelope.type}"
                    )
                active_job = None
                self._set_status(benchmarker, "connected")
        except ProtocolError as error:
            LOG.warning("closing benchmarker connection reason=%s", error)
            await websocket.close(code=error.close_code, reason=str(error)[:120])
        except ConnectionClosed:
            LOG.info("benchmarker connection closed")
        except asyncio.TimeoutError:
            LOG.warning("benchmarker response timed out")
        finally:
            if benchmarker is not None:
                if active_job is not None:
                    with contextlib.suppress(Exception):
                        self._fail_interrupted_job(benchmarker, active_job)
                live = self._connections.get(benchmarker.id)
                if live is not None and live[0] == benchmarker.session_id:
                    self._connections.pop(benchmarker.id, None)
                with contextlib.suppress(Exception):
                    self._disconnect(benchmarker)

    def _validate_hello(
        self,
        hello: BenchmarkerTokenHello | BenchmarkerSessionHello,
    ) -> None:
        calculated = benchmark_hardware_key(hello.machine_id, hello.hw)
        if calculated != hello.hardware_key:
            raise ProtocolValidationError("invalid benchmark hardware key")

    def _authenticate(
        self,
        hello: BenchmarkerTokenHello | BenchmarkerSessionHello,
    ) -> BenchmarkerRecord:
        connection = connect_database(self._config.db_path)
        try:
            if isinstance(hello, BenchmarkerTokenHello):
                record = get_benchmarker_by_token(connection, hello.token)
                if record is None or not benchmarker_token_is_valid(record):
                    raise ProtocolValidationError("invalid or expired benchmarker token")
                return record
            record = get_benchmarker_by_session_id(connection, hello.session_id)
            if record is None or record.status == "revoked":
                raise ProtocolValidationError("invalid benchmarker session")
            return record
        finally:
            connection.close()

    def _record_connection(
        self,
        record: BenchmarkerRecord,
        session_id: str,
        hello: BenchmarkerTokenHello | BenchmarkerSessionHello,
    ) -> BenchmarkerRecord:
        connection = connect_database(self._config.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            label = (
                hello.label_hint.strip()
                if isinstance(hello, BenchmarkerTokenHello) and hello.label_hint.strip()
                else record.label
            )
            current = register_benchmarker_connection(
                connection,
                benchmarker=record,
                label=label,
                session_id=session_id,
                app_commit=hello.app_version,
                protocol_version=PROTOCOL_VERSION,
                machine_id=hello.machine_id,
                hardware_key=hello.hardware_key,
                hw=hello.hw,
            )
            connection.commit()
            return current
        except ValueError as error:
            connection.rollback()
            raise ProtocolValidationError(str(error)) from error
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _claim_next_job(
        self,
        benchmarker: BenchmarkerRecord,
    ) -> BenchmarkJobRecord | None:
        if benchmarker.hardware_key is None:
            raise RuntimeError("connected benchmarker has no hardware key")
        connection = connect_database(self._config.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            job = claim_benchmark_job(
                connection,
                benchmarker_id=benchmarker.id,
                hardware_key=benchmarker.hardware_key,
            )
            connection.commit()
            return job
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _benchmarker_update_command(
        self,
        benchmarker_id: int,
        app_commit: str,
        supports_updates: bool,
    ) -> BenchmarkerUpdateCommand | None:
        connection = connect_database(self._config.db_path)
        try:
            target = reconcile_benchmarker_deployment(
                connection,
                benchmarker_id,
                app_commit,
            )
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
                raise ProtocolValidationError(
                    "benchmarker deployment source is unavailable"
                )
            if not supports_updates:
                update_deployment_target_status(
                    connection,
                    target.id,
                    "deferred",
                    current_commit=app_commit,
                    detail="Benchmarker requires a one-time reinstall before automatic updates are available.",
                )
                connection.commit()
                return None
            connection.commit()
            return BenchmarkerUpdateCommand(
                job_id=target.job_id,
                target_commit=target.target_commit,
                repository_url=target.repository_url,
            )
        finally:
            connection.close()

    async def _serve_benchmarker_update(
        self,
        websocket: WebSocketServerProtocol,
        benchmarker: BenchmarkerRecord,
        command: BenchmarkerUpdateCommand,
    ) -> None:
        while True:
            status = decode_message(
                await websocket.recv(),
                "benchmarker_update_status",
                BenchmarkerUpdateStatus,
            )
            if status.job_id != command.job_id or status.target_commit != command.target_commit:
                raise ProtocolValidationError(
                    "benchmarker update status does not match the deployment"
                )
            connection = connect_database(self._config.db_path)
            try:
                target = reconcile_benchmarker_deployment(
                    connection,
                    benchmarker.id,
                    benchmarker.app_commit or "",
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
                    current_commit=benchmarker.app_commit,
                    detail=status.detail,
                )
                connection.commit()
            finally:
                connection.close()
            if status.status in {"restarting", "failed"}:
                return

    def _complete_job(
        self,
        benchmarker: BenchmarkerRecord,
        job: BenchmarkJobRecord,
        result: BenchmarkResult,
    ) -> None:
        self._validate_result(job, result)
        connection = connect_database(self._config.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            complete_benchmark_job(
                connection,
                job=job,
                benchmarker_id=benchmarker.id,
                nps=result.nps,
                elapsed_ms=result.elapsed_ms,
                output=result.output,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _record_progress(
        self,
        benchmarker: BenchmarkerRecord,
        job: BenchmarkJobRecord,
        progress: BenchmarkProgress,
    ) -> None:
        self._validate_result(job, progress)
        connection = connect_database(self._config.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            record_benchmark_progress(
                connection,
                job=job,
                benchmarker_id=benchmarker.id,
                stage=progress.stage,
                substage=progress.substage,
                status=progress.status,
                detail=progress.detail,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _fail_reported_job(
        self,
        benchmarker: BenchmarkerRecord,
        job: BenchmarkJobRecord,
        failure: BenchmarkFailed,
    ) -> None:
        self._validate_result(job, failure)
        self._fail_job(
            benchmarker,
            job,
            f"{failure.stage}: {failure.error}",
            output=failure.output,
        )

    def _fail_interrupted_job(
        self,
        benchmarker: BenchmarkerRecord,
        job: BenchmarkJobRecord,
    ) -> None:
        self._fail_job(benchmarker, job, "benchmarker disconnected during job")

    def _fail_job(
        self,
        benchmarker: BenchmarkerRecord,
        job: BenchmarkJobRecord,
        error: str,
        *,
        output: str = "",
    ) -> None:
        connection = connect_database(self._config.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            fail_benchmark_job(
                connection,
                job=job,
                benchmarker_id=benchmarker.id,
                error=error,
                output=output,
                retry_seconds=self._config.retry_interval_s,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _set_status(self, benchmarker: BenchmarkerRecord, status: str) -> None:
        connection = connect_database(self._config.db_path)
        try:
            if not update_benchmarker_status(
                connection,
                benchmarker.id,
                status,
                session_id=benchmarker.session_id or "",
            ):
                raise ProtocolValidationError("benchmarker session is no longer current")
            connection.commit()
        finally:
            connection.close()

    def _disconnect(self, benchmarker: BenchmarkerRecord) -> None:
        connection = connect_database(self._config.db_path)
        try:
            disconnect_benchmarker(
                connection,
                benchmarker.id,
                session_id=benchmarker.session_id or "",
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _validate_result(
        job: BenchmarkJobRecord,
        result: BenchmarkResult | BenchmarkFailed | BenchmarkProgress,
    ) -> None:
        if result.job_id != job.id or result.job_key != job.job_key:
            raise ProtocolValidationError("benchmark result job mismatch")
        if result.hardware_key != job.hardware_key:
            raise ProtocolValidationError("benchmark result hardware key mismatch")
        if result.build_hash != job.build_hash:
            raise ProtocolValidationError("benchmark result build hash mismatch")
