import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from cope.core.models import WorkerControlSettings, WorkerResourceTelemetry

from .local_state import WorkerSettings, read_json, write_json


LOG = logging.getLogger("cope.worker")


@dataclass
class ManagedWorker:
    home: Path
    version: str
    physical_cores: int
    supported: bool = False
    snapshot: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)

    def stop_mode(self) -> str | None:
        return read_json(self.home / "stop.json").get("mode")

    def update_request(self) -> dict:
        return read_json(self.home / "update.json")

    def control(self) -> WorkerControlSettings:
        settings = WorkerSettings.read(self.home)
        return WorkerControlSettings(
            threads=settings.threads or self.physical_cores,
            memory_mb=settings.memory_mb,
            draining=self.stop_mode() == "grace" or bool(self.update_request()),
            auto_update=settings.auto_update,
        )

    def report(self, state: str, **fields) -> None:
        status = {
            "state": state, "version": self.version,
            "launch_id": os.environ.get("COPE_WORKER_LAUNCH_ID"), **fields,
        }
        if status != self.snapshot:
            try:
                write_json(self.home / "status.json", status)
            except OSError:
                LOG.exception("could not publish local worker status")
            else:
                self.snapshot = status

    def sample(self, telemetry: WorkerResourceTelemetry) -> None:
        self.metrics = {
            "cpu_percent": telemetry.cpu_percent,
            "worker_cpu_threads": round(telemetry.coordinator_cpu_cores + telemetry.engine_cpu_cores, 2),
            "worker_memory_mb": round(telemetry.coordinator_memory_mb + telemetry.engine_memory_mb),
            "available_memory_mb": round(telemetry.memory_available_mb),
        }
