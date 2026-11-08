from __future__ import annotations

import asyncio
import json
import sqlite3

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from cope.adapters.http.responses import _json
from cope.db import connect_database, database_schema_version
from cope.environment_clone import (
    CLONE_PROTOCOL_VERSION,
    RemoteCloneError,
    authenticated_source_opener,
    cancel_clone_job,
    clone_catalog_payload,
    clone_job_payload,
    create_clone_from_source,
    environment_instance_id,
    environment_inventory,
    list_clone_jobs,
    normalize_source_url,
    remote_json,
    resume_clone_job,
)


class EnvironmentClonePayload(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    admin_token: str = Field(min_length=1, max_length=1000)
    datasets: list[str] = Field(min_length=1, max_length=100)


class EnvironmentClonePreflightPayload(BaseModel):
    source: str = Field(min_length=1, max_length=500)
    admin_token: str = Field(min_length=1, max_length=1000)


def register_environment_clone_routes(app: FastAPI) -> None:
    from cope.web import app as web_app

    @app.get("/api/admin/tools/environment-clone")
    def admin_environment_clone(
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        return _json(
            {
                **clone_catalog_payload(),
                "instance_id": environment_instance_id(connection),
                "inventory": environment_inventory(connection),
                "recent_jobs": list_clone_jobs(connection),
            }
        )

    @app.post("/api/admin/tools/environment-clone/preflight")
    async def admin_environment_clone_preflight(
        payload: EnvironmentClonePreflightPayload,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        destination_instance = environment_instance_id(connection)

        def inspect_source():
            source_url = normalize_source_url(payload.source)
            opener = authenticated_source_opener(source_url, payload.admin_token)
            result = remote_json(opener, source_url, "/api/admin/environment-export/capabilities")
            result.pop("csrf_token", None)
            result["source_url"] = source_url
            return result

        try:
            source = await asyncio.to_thread(inspect_source)
        except (ValueError, RemoteCloneError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if source.get("instance_id") == destination_instance:
            raise HTTPException(status_code=409, detail="The source and destination are the same Cope installation.")
        source["compatible"] = (
            int(source.get("protocol_version", 0)) == CLONE_PROTOCOL_VERSION
            and int(source.get("schema_version", 0)) == database_schema_version(connection)
        )
        source["destination_inventory"] = environment_inventory(connection)
        return _json(source)

    @app.post("/api/admin/tools/environment-clone")
    async def admin_create_environment_clone(payload: EnvironmentClonePayload):
        def create_job():
            connection = connect_database(app.state.db_path)
            try:
                return create_clone_from_source(
                    connection,
                    payload.source,
                    payload.admin_token,
                    payload.datasets,
                )
            finally:
                connection.close()

        try:
            job = await asyncio.to_thread(create_job)
        except (ValueError, RemoteCloneError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _json({"job": job, "message": "Environment clone queued."}, status_code=201)

    @app.get("/api/admin/tools/environment-clone/jobs/{job_id}")
    def admin_environment_clone_job(
        job_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        job = clone_job_payload(connection, job_id, include_events=True)
        if job is None:
            raise HTTPException(status_code=404, detail="Environment clone job not found.")
        return _json({"job": job})

    @app.post("/api/admin/tools/environment-clone/jobs/{job_id}/cancel")
    def admin_cancel_environment_clone_job(
        job_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if clone_job_payload(connection, job_id, include_events=False) is None:
            raise HTTPException(status_code=404, detail="Environment clone job not found.")
        if not cancel_clone_job(connection, job_id):
            raise HTTPException(status_code=409, detail="This environment clone is no longer cancellable.")
        return _json({"message": "Environment clone cancellation requested."})

    @app.post("/api/admin/tools/environment-clone/jobs/{job_id}/resume")
    def admin_resume_environment_clone_job(
        job_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        if clone_job_payload(connection, job_id, include_events=False) is None:
            raise HTTPException(status_code=404, detail="Environment clone job not found.")
        if not resume_clone_job(connection, job_id):
            raise HTTPException(status_code=409, detail="This environment clone cannot be resumed.")
        return _json({"message": "Environment clone queued to resume."})

    @app.get("/api/admin/tools/environment-clone/jobs/{job_id}/events")
    async def admin_environment_clone_events(job_id: int, request: Request):
        def snapshot():
            connection = connect_database(request.app.state.db_path)
            try:
                return clone_job_payload(connection, job_id, include_events=True)
            finally:
                connection.close()

        if await asyncio.to_thread(snapshot) is None:
            raise HTTPException(status_code=404, detail="Environment clone job not found.")

        async def stream():
            last_signature = ""
            while True:
                job = await asyncio.to_thread(snapshot)
                if job is None:
                    break
                signature = f"{job['status']}|{job['updated_at']}|{len(job['events'])}"
                if signature != last_signature:
                    yield f"event: clone.snapshot\ndata: {json.dumps(job, separators=(',', ':'))}\n\n"
                    last_signature = signature
                if job["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(1)
            yield ": complete\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
