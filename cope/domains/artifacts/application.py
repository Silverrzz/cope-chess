from __future__ import annotations

import json
import os
from typing import Literal

from pydantic import Field

from cope.application.contracts import Command, CommandContext, Input, Outcome, Target
from cope.application.errors import ApplicationError
from cope.application.identity import current_principal

from .policy import MEDIA_POLICIES


class BeginUpload(Input):
    purpose: Literal["form_attachment"] = "form_attachment"
    release_id: str
    subject: str
    field_path: list[str] = Field(min_length=1, max_length=5)
    response_id: str | None = None
    filename: str = Field(min_length=1, max_length=200)
    size: int = Field(strict=True, gt=0)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: Literal["text/plain", "application/octet-stream"] = "application/octet-stream"


class Empty(Input):
    pass


class UploadOutput(Input):
    upload_id: str
    state: str
    expires_at: str
    transfer_url: str
    artifact_id: str | None


class ArtifactOutput(Input):
    artifact_id: str
    descriptor_revision: int
    digest: str
    size: int
    media_type: str
    validation_policy: str


def reservation_usage(connection) -> int:
    return connection.execute(
        """SELECT (SELECT COALESCE(sum(u.expected_size),0) FROM artifact_upload_sessions u
                      WHERE u.state IN ('receiving','verifying','stored') AND NOT EXISTS (
                        SELECT 1 FROM artifact_storage_intents i WHERE i.upload_id=u.id AND i.physical_reserved))
                 + (SELECT COALESCE(sum(u.expected_size),0) FROM artifact_storage_intents i
                    JOIN artifact_upload_sessions u ON u.id=i.upload_id WHERE i.physical_reserved) AS total""",
    ).fetchone()["total"]


def form_target(connection, principal, target, *, lock):
    from cope.domains.forms.application import resolve_submission

    return resolve_submission(connection, principal, target, lock=lock)


def form_attachment_field(connection, principal, form, release_id: str, path: list[str], *, response_id: str | None = None):
    from cope.domains.forms.storage import definition_from_json, form_permission, identity, pinned_release, response_resolver

    if response_id:
        response = response_resolver("read", owner_only=True)(connection, principal, response_id, lock=True).data
        if str(response["form_id"]) != str(form["id"]) or str(response["release_id"]) != identity(release_id) or response["withdrawn_at"]:
            raise ApplicationError("attachment_not_permitted", "This attachment is outside the response's current scope.", 403)
    elif form["closed"]:
        raise ApplicationError("form_closed", "This form is closed to new submissions.")
    release = pinned_release(connection, str(form["id"]), identity(release_id), accepting=True)
    definition = definition_from_json(release["definition"])
    if response_id and not definition.allow_amendments:
        raise ApplicationError("amendment_not_permitted", "This release does not accept amendments.", 403)
    if definition.audience != "public" and principal.credential_kind == "form_claim":
        raise ApplicationError("authentication_required", "This release requires an authenticated response.", 403)
    if definition.audience == "invited":
        form_permission(connection, principal, form, "read")
    fields = definition.fields
    selected = None
    for position, identity_value in enumerate(path):
        selected = next((field for field in fields if field.id == identity_value), None)
        if selected is None or position < len(path) - 1 and selected.kind != "group":
            raise ApplicationError("attachment_not_permitted", "Select an attachment field from this form release.", 422)
        fields = selected.fields
    if selected is None or selected.kind != "attachment":
        raise ApplicationError("attachment_not_permitted", "Select an attachment field from this form release.", 422)
    return selected


def begin_upload(context: CommandContext, payload: BeginUpload) -> Outcome:
    from cope.domains.forms.storage import identity, new_object, response_resolver

    subject = identity(payload.subject)
    field = form_attachment_field(context.connection, context.principal, context.target.data, payload.release_id,
                                  payload.field_path, response_id=payload.response_id)
    if payload.response_id:
        response = response_resolver("read", owner_only=True)(context.connection, context.principal, payload.response_id, lock=True).data
        if subject != str(response["attachment_subject"]):
            raise ApplicationError("attachment_not_permitted", "The attachment subject does not match this response.", 403)
    maximum = min(field.max_bytes, int(os.environ.get("COPE_ARTIFACT_UPLOAD_MAX_BYTES", "10485760")))
    if payload.size > maximum:
        raise ApplicationError("quota_exceeded", "This attachment exceeds the field or installation byte limit.", 413, max_bytes=maximum)
    if field.accepted_content_types and payload.media_type not in field.accepted_content_types:
        raise ApplicationError("validation_failed", "Choose a file type allowed by this attachment field.", 422)
    if any(character in payload.filename for character in "/\\") or any(ord(character) < 32 for character in payload.filename):
        raise ApplicationError("invalid_filename", "Use a plain attachment filename without path components.", 422)
    context.connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('artifact-reservations', 0))")
    reserved = context.connection.execute(
        """SELECT COALESCE(sum(expected_size),0) AS total,
                  COALESCE(sum(expected_size) FILTER (WHERE owner_actor = %s),0) AS owned
           FROM artifact_upload_sessions WHERE state IN ('receiving','verifying','stored')""", (context.principal.actor,),
    ).fetchone()
    if reservation_usage(context.connection) + payload.size > int(os.environ.get("COPE_ARTIFACT_TOTAL_MAX_BYTES", "1073741824")) or reserved["owned"] + payload.size > int(os.environ.get("COPE_ARTIFACT_ACTOR_MAX_BYTES", "104857600")):
        raise ApplicationError("quota_exceeded", "The attachment storage allowance is exhausted. Ask an operator to inspect retained uploads.", 409)
    upload_id = new_object(context.connection, "artifact_upload")
    context.connection.execute(
        """INSERT INTO artifact_upload_sessions(id,owner_actor,purpose,form_id,release_id,subject,field_path,filename,expected_size,expected_digest,media_type)
           VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)""",
        (upload_id, context.principal.actor, payload.purpose, context.target.object_id, identity(payload.release_id), subject,
         json.dumps(payload.field_path), payload.filename, payload.size, payload.digest, payload.media_type),
    )
    return Outcome(upload_id, 1, upload_view(context.connection, context.principal, upload_id))


def upload_row(connection, principal, upload_id: str, *, lock=False) -> dict:
    from cope.domains.forms.storage import get_form, identity

    principal = current_principal(connection, principal)
    hint = connection.execute("SELECT form_id FROM artifact_upload_sessions WHERE id = %s AND owner_actor = %s", (identity(upload_id), principal.actor)).fetchone()
    if hint is None:
        raise ApplicationError("upload_unavailable", "This upload is not available to the current credential.", 404)
    form = get_form(connection, str(hint["form_id"]), lock=lock)
    if principal.credential_kind == "form_claim" and principal.resource_scope != str(form["id"]):
        raise ApplicationError("upload_unavailable", "This upload is not available to the current credential.", 404)
    row = connection.execute("SELECT *, expires_at > clock_timestamp() AS eligible FROM artifact_upload_sessions WHERE id = %s" + (" FOR UPDATE" if lock else " FOR SHARE"), (identity(upload_id),)).fetchone()
    if row["owner_actor"] != principal.actor:
        raise ApplicationError("upload_unavailable", "This upload is not available to the current credential.", 404)
    return dict(row)


def upload_target(connection, principal, target, *, lock):
    row = upload_row(connection, principal, target, lock=lock)
    return Target(str(row["id"]), None, data=row)


def upload_view(connection, principal, upload_id: str) -> dict:
    row = upload_row(connection, principal, upload_id)
    return {"upload_id": str(row["id"]), "state": row["state"], "expires_at": row["expires_at"].isoformat(),
            "transfer_url": f"/api/artifacts/uploads/{row['id']}/bytes", "artifact_id": str(row["artifact_id"]) if row["artifact_id"] else None}


def finalize_upload(context: CommandContext, payload: Empty) -> Outcome:
    from cope.domains.forms.storage import new_object

    row = context.target.data
    if row["state"] == "stored":
        return Outcome(str(row["artifact_id"]), 1, artifact_view(context.connection, context.principal, str(row["artifact_id"])))
    if not row["eligible"]:
        raise ApplicationError("upload_expired", "This upload reservation expired.", 409)
    if row["state"] != "verifying" or not row["installed_intent"]:
        raise ApplicationError("artifact_not_ready", "Complete and validate the byte transfer before finalizing the upload.", 409)
    intent = context.connection.execute("SELECT * FROM artifact_storage_intents WHERE id = %s AND upload_id = %s AND state = 'installed' FOR SHARE",
                                         (row["installed_intent"], row["id"])).fetchone()
    if intent is None or intent["validation_policy"] != MEDIA_POLICIES[row["media_type"]]:
        raise ApplicationError("artifact_not_ready", "The upload has no current storage validation receipt.", 409)
    artifact_id = new_object(context.connection, "artifact")
    blob = context.connection.execute("INSERT INTO artifact_blobs(digest,size) VALUES (%s,%s) RETURNING id", (row["expected_digest"],row["expected_size"])).fetchone()
    context.connection.execute("INSERT INTO artifact_replicas(blob_id,physical_key,installed_intent) VALUES (%s,%s,%s)", (blob["id"],intent["physical_key"],intent["id"]))
    context.connection.execute(
        """INSERT INTO artifact_descriptors(artifact_id,revision,upload_id,blob_id,owner_actor,purpose,filename,media_type,validation_policy)
           VALUES (%s,1,%s,%s,%s,%s,%s,%s,%s)""",
        (artifact_id,row["id"],blob["id"],context.principal.actor,row["purpose"],row["filename"],row["media_type"],intent["validation_policy"]),
    )
    context.connection.execute("UPDATE artifact_upload_sessions SET state='stored',artifact_id=%s,lease=NULL,lease_until=NULL WHERE id=%s", (artifact_id,row["id"]))
    return Outcome(artifact_id, 1, artifact_view(context.connection, context.principal, artifact_id))


def artifact_view(connection, principal, artifact_id: str) -> dict:
    from cope.domains.forms.storage import identity

    principal = current_principal(connection, principal)
    row = connection.execute(
        """SELECT d.*, b.digest,b.size FROM artifact_descriptors d JOIN artifact_blobs b ON b.id=d.blob_id
           WHERE d.artifact_id=%s AND d.revision=1 AND d.owner_actor=%s""", (identity(artifact_id),principal.actor),
    ).fetchone()
    if row is None:
        raise ApplicationError("artifact_unavailable", "This attachment is not available to the current credential.", 404)
    upload_row(connection, principal, str(row["upload_id"]))
    return {"artifact_id": str(row["artifact_id"]), "descriptor_revision": 1, "digest": row["digest"], "size": row["size"],
            "media_type": row["media_type"], "validation_policy": row["validation_policy"]}


def abandon_upload(context: CommandContext, payload: Empty) -> Outcome:
    row = context.target.data
    if row["state"] == "stored":
        raise ApplicationError("artifact_retained", "Stored artifacts require a separate retention and removal decision.", 409)
    if row["lease_until"] and context.connection.execute("SELECT %s > clock_timestamp() AS active", (row["lease_until"],)).fetchone()["active"]:
        raise ApplicationError("transfer_in_progress", "Wait for the active transfer to finish or expire.", 409)
    context.connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('artifact-reservations',0))")
    context.connection.execute("UPDATE artifact_upload_sessions SET state='abandoned' WHERE id=%s", (row["id"],))
    return Outcome(str(row["id"]), None, upload_view(context.connection,context.principal,str(row["id"])))


def project_upload(connection, principal, result):
    upload_view(connection, principal, result["output"]["upload_id"])
    return result


def project_artifact(connection, principal, result):
    artifact_view(connection, principal, result["object_id"])
    return result


def describe_upload(connection, principal, target):
    return upload_view(connection,principal,target.object_id)


def commands() -> tuple[Command, ...]:
    return (
        Command("artifacts.begin_upload.v1", "artifact_upload", "forms:submit", None, BeginUpload, UploadOutput,
                form_target, begin_upload, lambda connection,principal,target: {"form_id":target.object_id}, project_upload, True, False),
        Command("artifacts.finalize_upload.v1", "artifact", "forms:submit", None, Empty, ArtifactOutput,
                upload_target, finalize_upload, describe_upload, project_artifact, True, False),
        Command("artifacts.abandon_upload.v1", "artifact_upload", "forms:submit", None, Empty, UploadOutput,
                upload_target, abandon_upload, describe_upload, project_upload, True, False),
    )
