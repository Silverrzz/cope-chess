from __future__ import annotations

import json

from cope.application.errors import ApplicationError
from cope.application.identity import current_principal


def resolve_attachment(connection, principal, form: dict, release: dict, subject: str | None, field, artifact_id: str,
                       path: tuple, *, response_id: str | None = None) -> tuple[str, dict]:
    from cope.domains.forms.storage import identity

    if subject is None:
        raise ApplicationError("attachment_subject_required", "This response needs its upload subject identity.",422)
    row = connection.execute(
        """SELECT d.*,u.form_id,u.release_id,u.subject,u.field_path,u.expires_at>clock_timestamp() AS eligible,
                  b.size,b.digest,r.id AS replica_id
           FROM artifact_descriptors d JOIN artifact_upload_sessions u ON u.id=d.upload_id
           JOIN artifact_blobs b ON b.id=d.blob_id
           JOIN artifact_replicas r ON r.blob_id=b.id AND r.state='ready'
           WHERE d.artifact_id=%s AND d.revision=1 AND d.owner_actor=%s AND u.state='stored'
           ORDER BY r.id LIMIT 1 FOR SHARE OF d,u,r""",(identity(artifact_id),principal.actor),
    ).fetchone()
    definition_path = [part for part in path if not _row_identity(str(part))]
    if row is None or str(row["form_id"]) != str(form["id"]) or str(row["release_id"]) != str(release["id"]) or str(row["subject"]) != identity(subject) or row["field_path"] != definition_path:
        raise ApplicationError("attachment_not_permitted", "Choose a completed attachment uploaded for this response and field.",403)
    retained = False
    if response_id:
        retained = connection.execute("SELECT 1 FROM artifact_retention_roots WHERE artifact_id=%s AND descriptor_revision=1 AND consumer_response=%s LIMIT 1",(row["artifact_id"],identity(response_id))).fetchone() is not None
    if not row["eligible"] and not retained or row["size"] > field.max_bytes or field.accepted_content_types and row["media_type"] not in field.accepted_content_types:
        raise ApplicationError("attachment_not_permitted", "This attachment no longer satisfies the current response policy.",403)
    return str(row["artifact_id"]), {"artifact_id":str(row["artifact_id"]),"descriptor_revision":1,"field_path":list(path)}


def _row_identity(part: str) -> bool:
    import uuid

    try:
        uuid.UUID(part)
        return True
    except ValueError:
        return False


def retain_attachments(connection, response_id: str, revision: int, attachments: list[dict]) -> None:
    for attachment in attachments:
        connection.execute(
            """INSERT INTO artifact_retention_roots(artifact_id,descriptor_revision,consumer_response,consumer_revision,field_path,reason)
               VALUES (%s,%s,%s,%s,%s::jsonb,'form_response_evidence') ON CONFLICT DO NOTHING""",
            (attachment["artifact_id"],attachment["descriptor_revision"],response_id,revision,json.dumps(attachment["field_path"])),
        )


def authorized_download(connection, principal, artifact_id: str) -> dict:
    from cope.domains.forms.storage import identity

    principal = current_principal(connection,principal)
    principal.require("forms:responses:read")
    row = connection.execute(
        """SELECT d.filename,b.size,b.digest,r.physical_key FROM artifact_descriptors d
           JOIN artifact_blobs b ON b.id=d.blob_id JOIN artifact_replicas r ON r.blob_id=b.id AND r.state='ready'
           JOIN artifact_upload_sessions u ON u.id=d.upload_id
           WHERE d.artifact_id=%s AND d.revision=1 AND (
             (d.owner_actor=%s AND (%s::uuid IS NULL OR u.form_id=%s::uuid)) OR EXISTS (
               SELECT 1 FROM artifact_retention_roots root JOIN form_responses response ON response.id=root.consumer_response
               JOIN form_definitions form ON form.id=response.form_id
               WHERE root.artifact_id=d.artifact_id AND root.descriptor_revision=d.revision
               AND (%s OR response.owner_actor=%s OR form.owner_actor=%s OR EXISTS (
                 SELECT 1 FROM form_permissions permission WHERE permission.form_id=form.id AND permission.actor=%s
                 AND permission.permission IN ('review','responses')))))
           ORDER BY r.id LIMIT 1 FOR SHARE OF d,r""",
        (identity(artifact_id),principal.actor,principal.resource_scope,principal.resource_scope,"*" in principal.scopes,principal.actor,principal.actor,principal.actor),
    ).fetchone()
    if row is None:
        raise ApplicationError("artifact_unavailable","This attachment is not available to the current credential.",404)
    return {**dict(row),"physical_key":str(row["physical_key"])}
