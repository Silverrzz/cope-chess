from __future__ import annotations

import asyncio
import hashlib
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse
from starlette.requests import ClientDisconnect

from cope.application import ApplicationError, CommandRequest, execute_command
from cope.adapters.http.forms import form_principal
from cope.control.api import application_result, database
from cope.domains.artifacts.transfers import confirm_empty_intent, fail_transfer, finish_transfer, prepare_transfer
from cope.infrastructure.artifact_files import ArtifactWriter, open_replica
from cope.infrastructure.postgres.application import run_application


def register_artifact_routes(app):
    router = APIRouter()

    @router.post("/commands")
    def command(payload: CommandRequest, identity=Depends(form_principal),connection=Depends(database)):
        if payload.command not in {"artifacts.begin_upload.v1","artifacts.finalize_upload.v1","artifacts.abandon_upload.v1"}:
            raise HTTPException(422,"This endpoint accepts attachment upload commands")
        return application_result(connection,identity,execute_command,payload)

    @router.put("/uploads/{upload_id}/bytes")
    async def upload(upload_id: str,request: Request,identity=Depends(form_principal),connection=Depends(database)):
        transfer = None
        writer = None
        try:
            transfer = run_application(connection,identity,prepare_transfer,upload_id)
            writer = await run_in_threadpool(ArtifactWriter,transfer["physical_key"],transfer["expected_size"],transfer["expected_digest"],transfer["media_type"])
            async with asyncio.timeout(120):
                async for chunk in request.stream():
                    await run_in_threadpool(writer.write,chunk)
            policy = await run_in_threadpool(writer.validate if transfer["replay"] else writer.install)
            return run_application(connection,identity,finish_transfer,transfer,policy)
        except ApplicationError as error:
            if transfer:
                run_application(connection,identity,fail_transfer,transfer,error.code)
            raise HTTPException(error.status,error.payload()) from error
        except (TimeoutError,OSError,asyncio.CancelledError,ClientDisconnect):
            if transfer:
                run_application(connection,identity,fail_transfer,transfer,"transfer_interrupted")
            raise HTTPException(503,"The transfer was interrupted. Retry the same reserved upload.")
        finally:
            if writer:
                writer.close()
                if transfer and not writer.destination.exists() and not writer.staging.exists():
                    run_application(connection,identity,confirm_empty_intent,transfer)

    @router.get("/{artifact_id}/download")
    def download(artifact_id: str,identity=Depends(form_principal),connection=Depends(database)):
        from cope.domains.artifacts.retention import authorized_download

        try:
            selected = run_application(connection,identity,authorized_download,artifact_id)
            stream = open_replica(selected["physical_key"],selected["size"])
            digest = hashlib.file_digest(stream,"sha256").hexdigest()
            if digest != selected["digest"]:
                stream.close()
                raise ApplicationError("artifact_not_ready","The retained attachment needs storage repair.",503)
            stream.seek(0)
        except ApplicationError as error:
            raise HTTPException(error.status,error.payload()) from error
        except OSError as error:
            raise HTTPException(503,"The retained attachment is unavailable and needs storage repair.") from error

        def chunks():
            try:
                while chunk := stream.read(65536):
                    yield chunk
            finally:
                stream.close()
        return StreamingResponse(chunks(),media_type="application/octet-stream",headers={
            "Content-Disposition":"attachment; filename*=UTF-8''"+quote(selected["filename"],safe=""),
            "Content-Length":str(selected["size"]),"Cache-Control":"private, no-store", "X-Content-Type-Options":"nosniff",
            "Content-Security-Policy":"sandbox; default-src 'none'"})

    app.include_router(router,prefix="/api/artifacts",tags=["Artifacts"])
