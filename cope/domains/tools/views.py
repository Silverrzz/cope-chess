from __future__ import annotations

from dataclasses import asdict
from typing import Any

from cope.db import list_tool_job_items, list_tool_jobs, list_workers

from .catalogue import TOOLS, describe_job


def tool_catalogue(connection) -> dict:
    return {
        "tools": [asdict(tool) for tool in TOOLS],
        "recent_jobs": [tool_job_view(connection, job, include_items=False) for job in list_tool_jobs(connection, limit=12)],
        "connected_workers": sum(worker.status in {"connected", "ready", "busy"} for worker in list_workers(connection)),
    }


def tool_job_view(connection, job, *, include_items: bool) -> dict[str, Any]:
    input_payload = dict(job.input)
    puzzles = input_payload.pop("puzzles", None)
    if isinstance(puzzles, list):
        input_payload["puzzle_count"] = len(puzzles)
    presentation = describe_job(job)
    payload: dict[str, Any] = {
        "label": presentation.label,
        "href": presentation.href,
        "id": job.id,
        "tool_name": job.tool_name,
        "status": job.status,
        "input": input_payload,
        "worker": (
            None
            if job.worker_id is None
            else {"id": job.worker_id, "label": job.worker_label or f"Worker {job.worker_id}"}
        ),
        "total_items": job.total_items,
        "completed_items": job.completed_items,
        "required_threads": job.required_threads,
        "required_hash_mb": job.required_hash_mb,
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
        "progress_detail": job.progress_detail,
        "attempt": job.attempt,
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }
    if include_items:
        payload["items"] = [
            {
                "id": item.id,
                "engine_id": item.engine_version_id,
                "engine_name": item.engine_name,
                "engine_version": item.engine_version,
                "position": item.position,
                "status": item.status,
                "result": item.result,
                "error": item.error,
                "started_at": item.started_at,
                "finished_at": item.finished_at,
            }
            for item in list_tool_job_items(connection, job.id)
        ]
    return payload
