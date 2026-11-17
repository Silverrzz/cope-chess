from __future__ import annotations

import hmac
import uuid

from cope.application.errors import ApplicationError
from cope.application.identity import Principal

from .application import SubmitResponse, store_response
from .storage import definition_from_json, form_permission, pinned_release


def participant_hash(connection, form_id: str, discord_id: str) -> str:
    secret = connection.execute("SELECT secret FROM form_private_keys WHERE purpose = 'discord_participation'").fetchone()["secret"]
    return hmac.digest(secret.encode(), f"{form_id}:{discord_id}".encode(), "sha256").hex()


def participation(connection, form_id: str, discord_id: str) -> tuple[bool, str | None]:
    accepted = connection.execute(
        "SELECT 1 FROM form_discord_participation WHERE form_id = %s AND participant_hash = %s",
        (form_id, participant_hash(connection, form_id, discord_id)),
    ).fetchone()
    if not accepted:
        return False, None
    linked = connection.execute(
        "SELECT id FROM form_responses WHERE form_id = %s AND owner_actor = %s ORDER BY created_at DESC LIMIT 1",
        (form_id, "discord:" + discord_id),
    ).fetchone()
    return True, str(linked["id"]) if linked else None


def submission_release(connection, slug: str, release_id: str, *, lock: bool = False):
    row = connection.execute(
        "SELECT * FROM form_definitions WHERE slug = %s" + (" FOR UPDATE" if lock else ""),
        (slug,),
    ).fetchone()
    if not row or row["published_release"] is None:
        raise ApplicationError("form_unavailable", "This form is unavailable.", 404)
    if row["closed"]:
        raise ApplicationError("form_closed", "This form is closed to new submissions.")
    if str(row["published_release"]) != release_id:
        raise ApplicationError("form_release_changed", "This form changed. Reload the latest version before submitting.")
    release = pinned_release(connection, str(row["id"]), release_id, accepting=True) if lock else connection.execute(
        "SELECT * FROM form_releases WHERE id = %s", (release_id,)
    ).fetchone()
    return row, release, definition_from_json(release["definition"])


def submit(connection, profile, slug: str, payload: SubmitResponse, verified_role: str | None, verified_guild: str | None) -> dict:
    form, release, definition = submission_release(connection, slug, payload.release_id, lock=True)
    form_id = str(form["id"])
    if definition.discord_role_id:
        guild = connection.execute(
            "SELECT guild_id FROM cope_notifications.discord_settings WHERE singleton FOR SHARE"
        ).fetchone()["guild_id"]
        if definition.discord_role_id != verified_role or guild != verified_guild or not guild:
            raise ApplicationError("discord_role_required", "Verify the required Discord role and try again.", 403)
    if definition.audience == "invited":
        form_permission(connection, profile.principal, form, "read")
    participant = participant_hash(connection, form_id, profile.id)
    inserted = connection.execute(
        """INSERT INTO form_discord_participation(form_id, participant_hash) VALUES (%s, %s)
           ON CONFLICT DO NOTHING RETURNING form_id""", (form_id, participant),
    ).fetchone()
    if inserted is None:
        _, linked = participation(connection, form_id, profile.id)
        return {"submitted": True, "already_submitted": True, "response_id": linked}
    owner = Principal("form-anonymous:" + str(uuid.uuid4()), ()) if definition.anonymous else profile.principal
    response_id = store_response(connection, owner, form, release, payload)
    return {"submitted": True, "already_submitted": False, "response_id": None if definition.anonymous else response_id}
