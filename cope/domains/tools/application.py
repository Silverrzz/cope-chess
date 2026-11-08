from __future__ import annotations

from cope.application.contracts import Command, CommandContext, Input, Outcome, Query
from cope.application.legacy_resources import LegacyResource
from cope.db.connection import LegacyTransaction

from .jobs import InspectOptions, cancel_job, inspect_options
from .views import tool_catalogue


class Empty(Input):
    pass


class ToolDescription(Input):
    name: str
    label: str
    description: str
    href: str
    status: str


class JobSummary(Input):
    id: int
    tool_name: str
    label: str
    href: str
    status: str
    completed_items: int
    total_items: int
    created_at: str
    input: dict
    worker: dict | None
    required_threads: int
    required_hash_mb: int
    progress_current: int
    progress_total: int
    progress_detail: str
    attempt: int
    error: str
    started_at: str | None
    finished_at: str | None


class ToolCatalogue(Input):
    tools: list[ToolDescription]
    recent_jobs: list[JobSummary]
    connected_workers: int


class JobOutput(Input):
    job_id: int
    status: str


TOOL_JOB = LegacyResource("job", "tool_jobs", "state_version", "d.status")


def _queue_inspection(context: CommandContext, payload: InspectOptions) -> Outcome:
    job = inspect_options(LegacyTransaction(context.connection), payload)
    target = TOOL_JOB.resolve(context.connection, context.principal, str(job.id), lock=False)
    return Outcome(target.object_id, target.version, {"job_id": job.id, "status": job.status}, job.id)


def _cancel_tool_job(context: CommandContext, payload: Empty) -> Outcome:
    target = context.target
    cancel_job(LegacyTransaction(context.connection), target.legacy_id)
    updated = TOOL_JOB.resolve(context.connection, context.principal, target.object_id, lock=False)
    return Outcome(updated.object_id, updated.version, {"job_id": updated.legacy_id, "status": updated.data["status"]}, updated.legacy_id)


def _describe_job(connection, principal, target):
    return TOOL_JOB.describe(connection, principal, target, Empty)


def commands() -> tuple[Command, ...]:
    return (
        Command("tools.inspect_options.v1", "job", "tools:write", None, InspectOptions, JobOutput,
                None, _queue_inspection, None, TOOL_JOB.project, requires_target=False, requires_version=False),
        Command("tools.cancel_job.v1", "job", "tools:write", "state_version", Empty, JobOutput,
                TOOL_JOB.resolve, _cancel_tool_job, _describe_job, TOOL_JOB.project),
    )


def catalogue(connection, principal, target, payload: Empty) -> dict:
    return tool_catalogue(LegacyTransaction(connection))


def queries() -> tuple[Query, ...]:
    return (Query("tools.catalogue.v1", "operations:read", Empty, ToolCatalogue, catalogue),)
