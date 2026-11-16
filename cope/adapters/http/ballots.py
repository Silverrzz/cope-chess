from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path as PathParameter, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import Field
from starlette.responses import FileResponse, JSONResponse

from cope.adapters.http.authentication import admin_session_role, request_is_secure_or_local
from cope.adapters.http.forms import form_principal, require_origin
from cope.application.contracts import Input
from cope.application.identity import Principal
from cope.control.api import application_result, database
from cope.db import get_platform_settings
from cope.domains.forms import ballot_protocol, ballots

Slug = Annotated[str, PathParameter(max_length=80, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]


class OpenForm(Input):
    revision: int = Field(strict=True, ge=1)


def public_principal(request: Request, connection=Depends(database)):
    if not request_is_secure_or_local(request):
        raise HTTPException(403, "Ballot forms require HTTPS.")
    if request.method == "POST":
        require_origin(request)
    if get_platform_settings(connection).privatise_platform and not admin_session_role(request):
        raise HTTPException(401, "Sign in to view forms on this private platform.")
    return Principal("public", ())


def attachment(data: dict, filename: str) -> JSONResponse:
    return JSONResponse(jsonable_encoder(data), headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def register_ballot_routes(app) -> None:
    router = APIRouter(prefix="/api/forms/secret-ballots", tags=["Secret ballots"])

    @router.get("/directory")
    def directory(limit: int = Query(24, ge=1, le=60), offset: int = Query(0, ge=0),
                  identity=Depends(public_principal), connection=Depends(database)):
        return application_result(connection, identity, ballots.directory, limit, offset)

    @router.get("/offline-tool")
    def offline_tool(identity=Depends(public_principal)):
        return FileResponse(Path(ballot_protocol.__file__), filename="cope_ballot.py", media_type="text/plain")

    @router.get("/admin")
    def admin_list(limit: int = Query(30, ge=1, le=60), offset: int = Query(0, ge=0),
                   identity=Depends(form_principal), connection=Depends(database)):
        return application_result(connection, identity, ballots.admin_list, limit, offset)

    @router.post("/admin", status_code=201)
    def create(payload: ballots.NewBallotForm, identity=Depends(form_principal), connection=Depends(database)):
        return application_result(connection, identity, ballots.create_form, payload)

    @router.get("/admin/{slug}")
    def manage(slug: Slug, identity=Depends(form_principal), connection=Depends(database)):
        return application_result(connection, identity, ballots.admin_detail, slug)

    @router.put("/admin/{slug}")
    def save(slug: Slug, payload: ballots.DraftEdit, identity=Depends(form_principal), connection=Depends(database)):
        return application_result(connection, identity, ballots.save_draft, slug, payload)

    @router.put("/admin/{slug}/visibility")
    def visibility(slug: Slug, payload: ballots.Visibility, identity=Depends(form_principal), connection=Depends(database)):
        return application_result(connection, identity, ballots.set_visibility, slug, payload)

    @router.post("/admin/{slug}/open")
    def open_form(slug: Slug, payload: OpenForm, identity=Depends(form_principal), connection=Depends(database)):
        return application_result(connection, identity, ballots.open_form, slug, payload.revision)

    @router.post("/admin/{slug}/close")
    def close_form(slug: Slug, identity=Depends(form_principal), connection=Depends(database)):
        return application_result(connection, identity, ballots.close_form, slug)

    @router.post("/admin/{slug}/shares")
    def submit_share(slug: Slug, payload: ballots.Share, identity=Depends(form_principal), connection=Depends(database)):
        return application_result(connection, identity, ballots.submit_share, slug, payload)

    @router.get("/forms/{slug}")
    def details(slug: Slug, identity=Depends(public_principal), connection=Depends(database)):
        return application_result(connection, identity, ballots.public_detail, slug)

    @router.get("/forms/{slug}/manifest")
    def manifest(slug: Slug, download: bool = False, identity=Depends(public_principal), connection=Depends(database)):
        result = application_result(connection, identity, ballots.public_manifest, slug)
        return attachment(result, f"{slug}-manifest.json") if download else result

    @router.post("/forms/{slug}/cast")
    def cast(slug: Slug, payload: ballots.Envelope, identity=Depends(public_principal), connection=Depends(database)):
        return application_result(connection, identity, ballots.cast, slug, payload)

    @router.get("/forms/{slug}/receipts/{fingerprint}")
    def find_receipt(slug: Slug, fingerprint: Annotated[str, PathParameter(pattern=r"^[0-9a-f]{64}$")],
                     identity=Depends(public_principal), connection=Depends(database)):
        return application_result(connection, identity, ballots.find_receipt, slug, fingerprint)

    @router.get("/forms/{slug}/audit")
    def audit(slug: Slug, identity=Depends(public_principal), connection=Depends(database)):
        result = application_result(connection, identity, ballots.audit, slug)
        return attachment(result, f"{slug}-audit.json")

    app.include_router(router)
