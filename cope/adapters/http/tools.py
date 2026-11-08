from __future__ import annotations

import sqlite3

from fastapi import Depends, FastAPI, HTTPException, Request

from cope.adapters.http.responses import _json, _publish_admin_change
from cope.application.errors import ApplicationError
from cope.db import get_tool_job, list_engine_records, list_tool_jobs, list_workers
from cope.domains.tools.catalogue import UCI_INSPECTION
from cope.domains.tools.jobs import InspectOptions, cancel_job, inspect_options
from cope.domains.tools.views import tool_catalogue, tool_job_view


def register_tools_routes(app: FastAPI) -> None:
    from cope.web import app as web_app

    @app.get("/api/admin/tools")
    def admin_tools(connection: sqlite3.Connection = Depends(web_app._database)):
        return _json(tool_catalogue(connection))

    @app.get("/api/admin/tools/who-has-this")
    def admin_who_has_this(
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        workers = list_workers(connection)
        return _json(
            {
                "engines": [
                    {
                        "id": engine.id,
                        "family_id": engine.engine_id,
                        "name": engine.name,
                        "author": engine.author,
                        "version": engine.version,
                        "repository": engine.repository_full_name,
                        "distribution": engine.distribution,
                        "artifact_ready": engine.artifact is not None,
                        "active": engine.engine_active,
                    }
                    for engine in list_engine_records(connection)
                ],
                "workers": [
                    {
                        "id": worker.id,
                        "label": worker.label,
                        "status": worker.status,
                    }
                    for worker in workers
                    if worker.status in {"connected", "ready", "busy"}
                ],
                "recent_jobs": [
                    tool_job_view(connection, job, include_items=False)
                    for job in list_tool_jobs(
                        connection,
                        tool_name=UCI_INSPECTION.key,
                        limit=12,
                    )
                ],
            }
        )

    @app.post("/api/admin/tools/who-has-this")
    def admin_create_who_has_this(
        payload: InspectOptions,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            job = inspect_options(connection, payload)
            connection.commit()
        except ApplicationError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "job": tool_job_view(connection, job, include_items=True),
                "message": "UCI option inspection queued.",
            },
            status_code=201,
        )

    @app.get("/api/admin/tools/jobs/{job_id}")
    def admin_tool_job(
        job_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        job = get_tool_job(connection, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Tool job not found.")
        return _json({"job": tool_job_view(connection, job, include_items=True)})

    @app.post("/api/admin/tools/jobs/{job_id}/cancel")
    def admin_cancel_tool_job(
        job_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            cancel_job(connection, job_id)
            connection.commit()
        except ApplicationError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        _publish_admin_change(web_app, request)
        return _json({"message": "Tool job cancelled. Its worker will be released shortly."})
