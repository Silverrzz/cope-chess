from __future__ import annotations

import uuid
import os

from cope.application.errors import ApplicationError
from cope.application.identity import current_principal

from .application import reservation_usage, upload_row, upload_view


def prepare_transfer(connection, principal, upload_id: str) -> dict:
    principal = current_principal(connection, principal)
    principal.require("forms:submit")
    row = upload_row(connection,principal,upload_id,lock=True)
    if row["state"] not in {"receiving","verifying","stored"}:
        raise ApplicationError("upload_unavailable", "This upload does not accept a byte transfer.",409)
    if row["state"] != "stored" and not row["eligible"]:
        raise ApplicationError("upload_expired", "This upload reservation expired.",409)
    if row["lease_until"] and connection.execute("SELECT %s > clock_timestamp() AS active",(row["lease_until"],)).fetchone()["active"]:
        raise ApplicationError("transfer_in_progress", "This upload already has an active transfer.",409)
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("artifact-transfers:"+principal.actor,))
    active = connection.execute("SELECT count(*) AS count FROM artifact_upload_sessions WHERE owner_actor=%s AND lease_until>clock_timestamp()",(principal.actor,)).fetchone()["count"]
    if active >= 3:
        raise ApplicationError("transfer_limit", "Wait for another attachment transfer to finish.",429)
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('artifact-reservations',0))")
    extra = row["expected_size"] if connection.execute("SELECT 1 FROM artifact_storage_intents WHERE upload_id=%s AND physical_reserved LIMIT 1",(row["id"],)).fetchone() else 0
    if reservation_usage(connection)+extra > int(os.environ.get("COPE_ARTIFACT_TOTAL_MAX_BYTES","1073741824")):
        raise ApplicationError("quota_exceeded","Storage cannot reserve another transfer attempt until its outstanding intents are reconciled.",409)
    intent, physical_key, lease = (str(uuid.uuid4()) for _ in range(3))
    connection.execute("UPDATE artifact_storage_intents SET state='abandoned',error_code='lease_expired' WHERE upload_id=%s AND state='pending'",(row["id"],))
    connection.execute("INSERT INTO artifact_storage_intents(id,upload_id,physical_key,lease) VALUES (%s,%s,%s,%s)",(intent,row["id"],physical_key,lease))
    connection.execute("UPDATE artifact_upload_sessions SET lease=%s,lease_until=clock_timestamp()+interval '150 seconds' WHERE id=%s",(lease,row["id"]))
    return {**row,"intent":intent,"physical_key":physical_key,"transfer_lease":lease,"replay":row["state"] in {"verifying","stored"}}


def finish_transfer(connection, principal, transfer: dict, policy: str) -> dict:
    principal = current_principal(connection,principal)
    principal.require("forms:submit")
    row = upload_row(connection,principal,str(transfer["id"]),lock=True)
    if str(row["lease"]) != transfer["transfer_lease"] or not connection.execute("SELECT %s > clock_timestamp() AS active",(row["lease_until"],)).fetchone()["active"]:
        raise ApplicationError("transfer_expired", "This transfer lost its lease. Retry the original upload.",409)
    if transfer["replay"]:
        connection.execute("UPDATE artifact_storage_intents SET state='abandoned',error_code='duplicate_transfer' WHERE id=%s AND lease=%s",(transfer["intent"],transfer["transfer_lease"]))
    else:
        if not row["eligible"] or row["state"] != "receiving":
            raise ApplicationError("upload_expired", "This upload no longer accepts transferred bytes.",409)
        connection.execute("UPDATE artifact_storage_intents SET state='installed',validation_policy=%s,validated_at=clock_timestamp() WHERE id=%s AND lease=%s AND state='pending'",(policy,transfer["intent"],transfer["transfer_lease"]))
        connection.execute("UPDATE artifact_upload_sessions SET state='verifying',installed_intent=%s WHERE id=%s",(transfer["intent"],row["id"]))
    connection.execute("UPDATE artifact_upload_sessions SET lease=NULL,lease_until=NULL WHERE id=%s",(row["id"],))
    return upload_view(connection,principal,str(row["id"]))


def fail_transfer(connection, principal, transfer: dict, code: str) -> None:
    row = connection.execute("SELECT id FROM artifact_upload_sessions WHERE id=%s AND owner_actor=%s FOR UPDATE",(transfer["id"],principal.actor)).fetchone()
    if row is None:
        return
    connection.execute("UPDATE artifact_storage_intents SET state='abandoned',error_code=%s WHERE id=%s AND state='pending'",(code[:100],transfer["intent"]))
    connection.execute("UPDATE artifact_upload_sessions SET lease=NULL,lease_until=NULL WHERE id=%s AND lease=%s",(row["id"],transfer["transfer_lease"]))


def confirm_empty_intent(connection, principal, transfer: dict) -> None:
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('artifact-reservations',0))")
    connection.execute("UPDATE artifact_storage_intents SET physical_reserved=FALSE WHERE id=%s AND lease=%s AND state='abandoned'",(transfer["intent"],transfer["transfer_lease"]))
