from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PROTOCOL_VERSION = 13
ENGINE_PROCESS_MEMORY_OVERHEAD_MB = 256
WORKER_MEMORY_RESERVE_MIN_MB = 2048
UciOptionValue = str | int | bool


def worker_memory_capacity_mb(total_ram_mb: int) -> int:
    reserve = max(WORKER_MEMORY_RESERVE_MIN_MB, total_ram_mb // 10)
    return max(1, total_ram_mb - reserve)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkerResources(StrictModel):
    threads: int = Field(gt=0)
    hash_mb: int = Field(gt=0)

    def can_run(self, required: WorkerResources) -> bool:
        return self.threads >= required.threads and self.hash_mb >= required.hash_mb


class TimeControlCategory(StrEnum):
    INCREMENT = "increment"
    MOVETIME = "movetime"
    MOVESTOGO = "movestogo"
    MOVENODES = "movenodes"


class IncrementTimeControl(StrictModel):
    category: Literal["increment"] = "increment"
    initial_ms: int = Field(gt=0)
    increment_ms: int = Field(ge=0)


class MoveTimeControl(StrictModel):
    category: Literal["movetime"] = "movetime"
    move_time_ms: int = Field(gt=0)


class MovesToGoTimeControl(StrictModel):
    category: Literal["movestogo"] = "movestogo"
    initial_ms: int = Field(gt=0)
    moves_to_go: int = Field(gt=0)


class MoveNodesTimeControl(StrictModel):
    category: Literal["movenodes"] = "movenodes"
    nodes: int = Field(gt=0)


TimeControl = Annotated[
    IncrementTimeControl | MoveTimeControl | MovesToGoTimeControl | MoveNodesTimeControl,
    Field(discriminator="category"),
]


class DrawAdjudicationRule(StrictModel):
    min_fullmove: int = Field(gt=0)
    max_abs_cp: int = Field(ge=0)
    consecutive_plies: int = Field(gt=0)

    @field_validator("consecutive_plies")
    @classmethod
    def require_engine_agreement(cls, value: int) -> int:
        return max(value, 2)


class ResignAdjudicationRule(StrictModel):
    min_abs_cp: int = Field(gt=0)
    consecutive_plies: int = Field(gt=0)

    @field_validator("consecutive_plies")
    @classmethod
    def require_engine_agreement(cls, value: int) -> int:
        return max(value, 2)


class AdjudicationConfig(StrictModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    draw: DrawAdjudicationRule | None = None
    resign: ResignAdjudicationRule | None = None
    max_moves: int | None = Field(default=None, gt=0)


class EngineArtifactSpec(StrictModel):
    url: str = Field(min_length=1, max_length=1000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0)
    format: Literal["cope-tar-gzip-v1"] = "cope-tar-gzip-v1"
    entrypoint: str = Field(default="engine", pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
    platform: Literal["linux-x86_64"] = "linux-x86_64"

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, value: str) -> str:
        if value.startswith("/") or ".." in value.split("/"):
            raise ValueError("artifact entrypoint must stay inside the artifact")
        return value


class EngineSpec(StrictModel):
    engine_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=80)
    author: str = Field(default="", max_length=120)
    version: str = Field(min_length=1, max_length=80)
    repository_url: str = Field(min_length=1, max_length=1000)
    source_ref: str = Field(min_length=1, max_length=200)
    dockerfile: str = Field(default="", max_length=100_000)
    build_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact: EngineArtifactSpec | None = None
    uci_options: dict[str, UciOptionValue] = Field(default_factory=dict)

    @field_validator("uci_options")
    @classmethod
    def validate_uci_options(cls, value: dict[str, UciOptionValue]) -> dict[str, UciOptionValue]:
        for name in value:
            if not name.strip():
                raise ValueError("uci option names must be non-empty")
        return value



class TournamentFormat(StrEnum):
    ROUND_ROBIN = "round_robin"
    SWISS = "swiss"
    KNOCKOUT = "knockout"
    GAUNTLET = "gauntlet"


class KnockoutTiebreak(StrEnum):
    ARMAGEDDON = "armageddon"
    EXTRA_PAIR = "extra_pair"


class RoundRobinFormatOptions(StrictModel):
    cycles: int = Field(default=1, gt=0)


class SwissFormatOptions(StrictModel):
    rounds: int = Field(gt=0)


class KnockoutFormatOptions(StrictModel):
    tiebreak: Literal["extra_pair"] = "extra_pair"


class GauntletFormatOptions(StrictModel):
    hero_engine_id: int = Field(gt=0)
    cycles: int = Field(default=1, gt=0)


FormatOptions = (
    RoundRobinFormatOptions | SwissFormatOptions | KnockoutFormatOptions | GauntletFormatOptions
)


class TournamentConfig(StrictModel):
    format: TournamentFormat
    format_options: FormatOptions
    participants: list[int] = Field(min_length=2)
    time_control: TimeControl
    concurrency: int = Field(default=1, gt=0)
    opening_suite_id: int | None = Field(default=None, gt=0)
    adjudication: AdjudicationConfig
    rated: bool = True
    lag_compensation_ms: int = Field(default=50, ge=0)
    engine_threads: int = Field(default=1, gt=0)
    engine_hash_mb: int = Field(default=16, gt=0)
    uci_options: dict[str, UciOptionValue] = Field(default_factory=dict)

    @field_validator("participants")
    @classmethod
    def validate_participants(cls, value: list[int]) -> list[int]:
        if any(engine_id <= 0 for engine_id in value):
            raise ValueError("participant engine ids must be positive")
        if len(set(value)) != len(value):
            raise ValueError("participants must be unique")
        return value

    @field_validator("uci_options")
    @classmethod
    def validate_tournament_uci_options(
        cls,
        value: dict[str, UciOptionValue],
    ) -> dict[str, UciOptionValue]:
        for name in value:
            if not name.strip():
                raise ValueError("UCI option names must be non-empty")
            if name.strip().lower() in {"threads", "hash"}:
                raise ValueError(
                    "Threads and Hash are controlled by tournament resource settings"
                )
        return value

    @model_validator(mode="after")
    def validate_format_options(self) -> TournamentConfig:
        expected: dict[TournamentFormat, type[StrictModel]] = {
            TournamentFormat.ROUND_ROBIN: RoundRobinFormatOptions,
            TournamentFormat.SWISS: SwissFormatOptions,
            TournamentFormat.KNOCKOUT: KnockoutFormatOptions,
            TournamentFormat.GAUNTLET: GauntletFormatOptions,
        }
        expected_type = expected[self.format]
        if not isinstance(self.format_options, expected_type):
            raise ValueError(f"{self.format.value} requires {expected_type.__name__}")

        if isinstance(self.format_options, GauntletFormatOptions):
            if self.format_options.hero_engine_id not in self.participants:
                raise ValueError("gauntlet hero_engine_id must be in participants")

        return self


class ColorSlot(StrEnum):
    WHITE = "W"
    BLACK = "B"


class GameAssignment(StrictModel):
    assignment_id: int = Field(gt=0)
    assignment_key: str = Field(min_length=16, max_length=128)
    game_id: int = Field(gt=0)
    slots: dict[ColorSlot, int]
    time_control: TimeControl
    uci_options_overrides: dict[int, dict[str, UciOptionValue]] = Field(default_factory=dict)

    @field_validator("slots")
    @classmethod
    def validate_slots(cls, value: dict[ColorSlot, int]) -> dict[ColorSlot, int]:
        if not 1 <= len(value) <= 2:
            raise ValueError("slots must contain one or two colour assignments")
        for engine_id in value.values():
            if engine_id <= 0:
                raise ValueError("slot engine ids must be positive")
        return value

    @field_validator("uci_options_overrides")
    @classmethod
    def validate_overrides(
        cls,
        value: dict[int, dict[str, UciOptionValue]],
    ) -> dict[int, dict[str, UciOptionValue]]:
        for engine_id, options in value.items():
            if engine_id <= 0:
                raise ValueError("override engine ids must be positive")
            for name in options:
                if not name.strip():
                    raise ValueError("uci option override names must be non-empty")
        return value

    def message_fields(self) -> dict[str, int | str]:
        return {
            "assignment_id": self.assignment_id,
            "assignment_key": self.assignment_key,
            "game_id": self.game_id,
        }


class WorkflowStep(StrictModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=120)
    order: int = Field(ge=0)
    owner: Literal["server", "worker", "shared"]


class OpeningLine(StrictModel):
    name: str = ""
    start_fen: str = Field(min_length=1)
    moves: tuple[str, ...] = ()
    fen: str = Field(min_length=1)


def game_setup_workflow() -> tuple[WorkflowStep, ...]:
    return (
        WorkflowStep(key="assignment", label="Assignment accepted", order=0, owner="shared"),
        WorkflowStep(key="engines", label="Engine artifacts", order=1, owner="shared"),
        WorkflowStep(key="benchmark", label="Hardware benchmark", order=2, owner="shared"),
        WorkflowStep(key="startup", label="Engine startup", order=3, owner="shared"),
        WorkflowStep(key="opening", label="Opening setup", order=4, owner="shared"),
        WorkflowStep(key="play", label="Engine play", order=5, owner="shared"),
        WorkflowStep(key="conclude", label="Game conclusion", order=6, owner="server"),
        WorkflowStep(key="cleanup", label="Worker cleanup", order=7, owner="worker"),
    )


class GameBenchmarkReference(StrictModel):
    hardware_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine_nps: dict[int, int]
    timeout_s: int = Field(default=600, gt=0)

    @field_validator("engine_nps")
    @classmethod
    def validate_engine_nps(cls, value: dict[int, int]) -> dict[int, int]:
        if not value or any(engine_id <= 0 or nps <= 0 for engine_id, nps in value.items()):
            raise ValueError("benchmark reference requires positive engine ids and NPS values")
        return value


class WorkerGameAssignment(StrictModel):
    assignment: GameAssignment
    tournament_name: str = Field(min_length=1)
    round: int = Field(gt=0)
    initial_fen: str = Field(min_length=1)
    opening_name: str | None = None
    opening_moves: tuple[str, ...] = ()
    max_plies: int = Field(gt=0)
    engines: dict[int, EngineSpec]
    required_resources: WorkerResources
    benchmark_reference: GameBenchmarkReference
    workflow: tuple[WorkflowStep, ...] = Field(default_factory=game_setup_workflow)

    @field_validator("engines")
    @classmethod
    def validate_engines(cls, value: dict[int, EngineSpec]) -> dict[int, EngineSpec]:
        if not value:
            raise ValueError("assignment must include engine specs")
        for engine_id, spec in value.items():
            if engine_id <= 0:
                raise ValueError("engine ids must be positive")
            if spec.engine_id != engine_id:
                raise ValueError("engine spec id must match its assignment key")
        return value

    @field_validator("workflow")
    @classmethod
    def validate_workflow(cls, value: tuple[WorkflowStep, ...]) -> tuple[WorkflowStep, ...]:
        if not value:
            raise ValueError("assignment workflow must contain at least one step")
        if len({step.key for step in value}) != len(value):
            raise ValueError("assignment workflow step keys must be unique")
        if len({step.order for step in value}) != len(value):
            raise ValueError("assignment workflow step orders must be unique")
        return tuple(sorted(value, key=lambda step: step.order))

    @model_validator(mode="after")
    def validate_benchmark_reference(self) -> WorkerGameAssignment:
        if set(self.benchmark_reference.engine_nps) != set(self.engines):
            raise ValueError("benchmark reference must include every assigned engine")
        return self


class AssignmentMessage(StrictModel):
    assignment_id: int = Field(gt=0)
    assignment_key: str = Field(min_length=16, max_length=128)
    game_id: int = Field(gt=0)

    def matches_assignment(self, assignment: GameAssignment) -> bool:
        return (
            self.assignment_id == assignment.assignment_id
            and self.assignment_key == assignment.assignment_key
            and self.game_id == assignment.game_id
        )


class AssignmentProgress(AssignmentMessage):
    stage: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    stage_label: str = Field(min_length=1, max_length=120)
    stage_order: int = Field(ge=0)
    substage: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    status: Literal["pending", "running", "completed", "failed"]
    detail: str = Field(min_length=1, max_length=4000)
    engine_id: int | None = Field(default=None, gt=0)
    engine_name: str | None = Field(default=None, min_length=1, max_length=80)
    current: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_progress(self) -> AssignmentProgress:
        if self.current is not None and self.total is not None and self.current > self.total:
            raise ValueError("progress current cannot exceed total")
        if (self.engine_id is None) != (self.engine_name is None):
            raise ValueError("engine progress requires both engine id and name")
        return self


class EngineCommand(AssignmentMessage):
    engine_id: int = Field(gt=0)
    command: str = Field(min_length=1)


class EngineCommandStarted(AssignmentMessage):
    engine_id: int = Field(gt=0)


class EngineClock(EngineCommandStarted):
    elapsed_ms: int = Field(ge=0)


class EngineStop(EngineCommandStarted):
    pass


class EngineCommandResult(AssignmentMessage):
    engine_id: int = Field(gt=0)
    lines: list[str]
    elapsed_ms: int = Field(ge=0)


class EngineInfo(EngineClock):
    lines: list[str]


class AssignmentComplete(AssignmentMessage):
    pass


class AssignmentCleanupComplete(AssignmentMessage):
    pass


class EngineHardwareScore(StrictModel):
    benchmark_nps: int = Field(gt=0)
    worker_nps: int = Field(gt=0)
    hardware_score: float = Field(gt=0, allow_inf_nan=False)
    elapsed_ms: int = Field(ge=0)


class AssignmentReady(AssignmentMessage):
    prepared_engine_ids: list[int] = Field(min_length=1)
    hardware_scores: dict[int, EngineHardwareScore]

    @field_validator("prepared_engine_ids")
    @classmethod
    def validate_prepared_engine_ids(cls, value: list[int]) -> list[int]:
        if any(engine_id <= 0 for engine_id in value):
            raise ValueError("prepared engine ids must be positive")
        if len(set(value)) != len(value):
            raise ValueError("prepared engine ids must be unique")
        return value

    @field_validator("hardware_scores")
    @classmethod
    def validate_hardware_scores(
        cls,
        value: dict[int, EngineHardwareScore],
    ) -> dict[int, EngineHardwareScore]:
        if not value or any(engine_id <= 0 for engine_id in value):
            raise ValueError("hardware scores require positive engine ids")
        return value

    @model_validator(mode="after")
    def validate_ready_engines(self) -> AssignmentReady:
        if set(self.prepared_engine_ids) != set(self.hardware_scores):
            raise ValueError("prepared engines and hardware scores must match")
        return self


class AssignmentFailed(AssignmentMessage):
    engine_id: int = Field(gt=0)
    engine_name: str = Field(min_length=1, max_length=80)
    stage: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    error: str = Field(min_length=1, max_length=8000)


class BenchInfo(StrictModel):
    nps_probe: int | None = Field(default=None, gt=0)


class HardwareInfo(StrictModel):
    cpu_model: str = Field(min_length=1)
    physical_cores: int = Field(gt=0)
    logical_cores: int = Field(gt=0)
    ram_gb: int = Field(gt=0)
    ram_mb: int | None = Field(default=None, gt=0)
    gpu: str | None = None
    os: str = Field(min_length=1)
    python: str = Field(min_length=1)
    bench: BenchInfo = Field(default_factory=BenchInfo)

    @model_validator(mode="after")
    def validate_core_counts(self) -> HardwareInfo:
        if self.logical_cores < self.physical_cores:
            raise ValueError("logical_cores must be >= physical_cores")
        return self

    @property
    def total_ram_mb(self) -> int:
        return self.ram_mb or self.ram_gb * 1024


class WorkerActiveAssignmentsMixin(StrictModel):
    active_assignment_ids: list[int] = Field(default_factory=list)
    machine_id: str = Field(min_length=8, max_length=128)

    @field_validator("active_assignment_ids")
    @classmethod
    def validate_active_assignment_ids(cls, value: list[int]) -> list[int]:
        if any(assignment_id <= 0 for assignment_id in value):
            raise ValueError("active assignment ids must be positive")
        if len(set(value)) != len(value):
            raise ValueError("active assignment ids must be unique")
        return value


class WorkerTokenHello(WorkerActiveAssignmentsMixin):
    token: str = Field(min_length=1)
    label_hint: str = Field(default="", max_length=80)
    hw: HardwareInfo
    app_version: str = Field(min_length=1)


class WorkerSessionHello(WorkerActiveAssignmentsMixin):
    session_id: str = Field(min_length=1)
    hw: HardwareInfo
    app_version: str = Field(min_length=1)


class WorkerUpdateCommand(StrictModel):
    job_id: int = Field(gt=0)
    target_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    repository_url: str = Field(min_length=1, max_length=1000)


class WorkerUpdateStatus(StrictModel):
    job_id: int = Field(gt=0)
    target_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    status: Literal["accepted", "installing", "restarting", "failed"]
    detail: str = Field(default="", max_length=4000)


class WorkerWelcome(StrictModel):
    worker_id: int = Field(gt=0)
    session_id: str = Field(min_length=1)
    heartbeat_interval_ms: int = Field(gt=0)
    capacity: WorkerResources
    update: WorkerUpdateCommand | None = None


class BenchmarkerTokenHello(StrictModel):
    token: str = Field(min_length=1)
    label_hint: str = Field(default="", max_length=80)
    machine_id: str = Field(min_length=8, max_length=128)
    hardware_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    hw: HardwareInfo
    app_version: str = Field(min_length=1)
    supports_updates: bool = False
    supports_progress: bool = False


class BenchmarkerSessionHello(StrictModel):
    session_id: str = Field(min_length=1)
    machine_id: str = Field(min_length=8, max_length=128)
    hardware_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    hw: HardwareInfo
    app_version: str = Field(min_length=1)
    supports_updates: bool = False
    supports_progress: bool = False


class BenchmarkerUpdateCommand(WorkerUpdateCommand):
    pass


class BenchmarkerUpdateStatus(WorkerUpdateStatus):
    pass


class BenchmarkerWelcome(StrictModel):
    benchmarker_id: int = Field(gt=0)
    session_id: str = Field(min_length=1)
    poll_interval_ms: int = Field(gt=0)
    update: BenchmarkerUpdateCommand | None = None


class BenchmarkAssignment(StrictModel):
    job_id: int = Field(gt=0)
    job_key: str = Field(min_length=16, max_length=128)
    hardware_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine: EngineSpec
    preparation_timeout_s: int = Field(default=1800, gt=0)
    timeout_s: int = Field(gt=0)


class BenchmarkProgress(StrictModel):
    job_id: int = Field(gt=0)
    job_key: str = Field(min_length=16, max_length=128)
    hardware_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    stage: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    substage: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    status: Literal["running", "completed"]
    detail: str = Field(min_length=1, max_length=4000)


class BenchmarkResult(StrictModel):
    job_id: int = Field(gt=0)
    job_key: str = Field(min_length=16, max_length=128)
    hardware_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact: EngineArtifactSpec
    nps: int = Field(gt=0)
    elapsed_ms: int = Field(ge=0)
    output: str = Field(default="", max_length=64_000)


class BenchmarkFailed(StrictModel):
    job_id: int = Field(gt=0)
    job_key: str = Field(min_length=16, max_length=128)
    hardware_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    stage: Literal["build", "upload", "bench", "parse", "unsupported", "internal"]
    error: str = Field(min_length=1, max_length=8000)
    output: str = Field(default="", max_length=64_000)


class Envelope(StrictModel):
    v: Literal[13] = PROTOCOL_VERSION
    type: str = Field(min_length=1)
    seq: int = Field(ge=0)
    t_mono_ms: int = Field(ge=0)
    data: dict[str, Any] = Field(default_factory=dict)
