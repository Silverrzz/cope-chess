from __future__ import annotations

import asyncio
import logging

from cope.db.connection import connect_database
from cope.infrastructure.artifact_files import replica_path

LOG = logging.getLogger(__name__)


def prune_uploads(connection, limit: int = 24) -> None:
    rows = connection.execute(
        """SELECT upload.id, upload.artifact_id, upload.expires_at <= clock_timestamp() AS expired, upload.state
           FROM artifact_upload_sessions upload
           WHERE (upload.lease_until IS NULL OR upload.lease_until < clock_timestamp() - interval '15 minutes')
             AND (
               (upload.expires_at <= clock_timestamp() AND upload.state IN ('receiving','verifying','stored')
                AND NOT EXISTS (SELECT 1 FROM artifact_retention_roots root WHERE root.artifact_id=upload.artifact_id))
               OR EXISTS (
                 SELECT 1 FROM artifact_storage_intents intent WHERE intent.upload_id=upload.id AND intent.physical_reserved
                   AND (intent.state='abandoned' OR (intent.state='pending' AND intent.created_at < clock_timestamp() - interval '15 minutes'))
               )
             )
           ORDER BY upload.expires_at, upload.id LIMIT %s FOR UPDATE OF upload SKIP LOCKED""",
        (limit,),
    ).fetchall()
    for row in rows:
        expired = row["expired"] and row["state"] in {"receiving", "verifying", "stored"}
        if expired and row["artifact_id"] is not None:
            expired = connection.execute(
                "SELECT 1 FROM artifact_retention_roots WHERE artifact_id=%s LIMIT 1", (row["artifact_id"],)
            ).fetchone() is None
        if expired:
            connection.execute(
                "UPDATE artifact_upload_sessions SET state='expired', artifact_id=NULL, lease=NULL, lease_until=NULL WHERE id=%s",
                (row["id"],),
            )
        intents = connection.execute(
            """SELECT intent.id, intent.physical_key FROM artifact_storage_intents intent
               WHERE intent.upload_id=%s AND intent.physical_reserved AND (
                 %s OR intent.state='abandoned' OR (intent.state='pending' AND intent.created_at < clock_timestamp() - interval '15 minutes')
               ) FOR UPDATE""",
            (row["id"], expired),
        ).fetchall()
        for intent in intents:
            destination = replica_path(str(intent["physical_key"]))
            connection.execute("UPDATE artifact_replicas SET state='deleted' WHERE installed_intent=%s", (intent["id"],))
            destination.unlink(missing_ok=True)
            destination.with_suffix(".partial").unlink(missing_ok=True)
            connection.execute(
                """UPDATE artifact_storage_intents SET physical_reserved=FALSE,
                   state='abandoned', validated_at=NULL, error_code='expired_upload'
                   WHERE id=%s""",
                (intent["id"],),
            )
    connection.execute(
        """DELETE FROM form_rate_limits WHERE ctid IN (
           SELECT ctid FROM form_rate_limits WHERE hour < clock_timestamp() - interval '2 days' LIMIT 500
        )"""
    )


def maintain_guest_uploads(database_url: str) -> None:
    database = connect_database(database_url)
    try:
        with database.native_connection().transaction():
            prune_uploads(database.native_connection())
    finally:
        database.close()


async def run_guest_upload_maintenance(database_url: str) -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(maintain_guest_uploads, database_url)
        except Exception:
            LOG.exception("Guest form upload maintenance failed")
