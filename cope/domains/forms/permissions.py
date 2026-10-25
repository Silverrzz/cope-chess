from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field

from cope.application.contracts import Command, CommandContext, Input, Outcome, Query
from cope.application.errors import ApplicationError

from .storage import form_resolver


class SetPermissions(Input):
    actor: str = Field(min_length=1,max_length=150)
    permissions: list[Literal["read","edit","review","responses"]] = Field(max_length=4)


class PermissionsOutput(Input):
    form_id: str
    actor: str
    permissions: list[str]


class Empty(Input):
    pass


class AccessOutput(Input):
    state_version: int
    owner_actor: str
    grants: list[dict]
    identities: list[dict]


def manage_target(connection, principal, target, *, lock):
    resolved = form_resolver("edit","state_version")(connection,principal,target,lock=lock)
    if "*" not in principal.scopes and resolved.data["owner_actor"] != principal.actor:
        raise ApplicationError("permission_denied","Only the form owner or an administrator can change response access.",403)
    return resolved


def current_identity(connection, actor: str) -> None:
    if actor in {"browser:admin","browser:manager"}:
        return
    try:
        kind, identifier = actor.split(":",1)
        if kind != "token" or str(uuid.UUID(identifier)) != identifier:
            raise ValueError()
    except (ValueError,AttributeError) as exc:
        raise ApplicationError("identity_unavailable","Choose a current account or credential identity.",422) from exc
    if connection.execute("SELECT id FROM control_tokens WHERE id=%s AND revoked_at IS NULL AND expires_at>clock_timestamp() FOR SHARE",(identifier,)).fetchone() is None:
        raise ApplicationError("identity_unavailable","Choose a current account or credential identity.",422)


def set_permissions(context: CommandContext, payload: SetPermissions) -> Outcome:
    if len(payload.permissions) != len(set(payload.permissions)):
        raise ApplicationError("invalid_permissions","Choose each permission once.",422)
    if payload.permissions:
        current_identity(context.connection,payload.actor)
    if payload.actor == context.target.data["owner_actor"]:
        raise ApplicationError("owner_access","The form owner's authority is managed by ownership, rather than an invitation.",422)
    old = {row["permission"] for row in context.connection.execute("SELECT permission FROM form_permissions WHERE form_id=%s AND actor=%s",(context.target.object_id,payload.actor)).fetchall()}
    requested = set(payload.permissions)
    if old != requested:
        context.connection.execute("DELETE FROM form_permissions WHERE form_id=%s AND actor=%s",(context.target.object_id,payload.actor))
        for permission in sorted(requested):
            context.connection.execute("INSERT INTO form_permissions(form_id,actor,permission,granted_by) VALUES (%s,%s,%s,%s)",(context.target.object_id,payload.actor,permission,context.principal.actor))
        context.connection.execute("UPDATE application_objects SET state_version=state_version+1 WHERE id=%s",(context.target.object_id,))
    return Outcome(context.target.object_id,context.target.version+(old != requested),{"form_id":context.target.object_id,"actor":payload.actor,"permissions":sorted(requested)})


def access_view(connection, principal, target, payload: Empty) -> dict:
    resolved = manage_target(connection,principal,target,lock=False)
    grants = connection.execute("SELECT actor,array_agg(permission ORDER BY permission) AS permissions FROM form_permissions WHERE form_id=%s GROUP BY actor ORDER BY actor",(resolved.object_id,)).fetchall()
    identities = [{"actor":"browser:manager","label":"Shared manager account"}]
    if "*" in principal.scopes:
        tokens = connection.execute("SELECT id,label FROM control_tokens WHERE revoked_at IS NULL AND expires_at>clock_timestamp() ORDER BY label,id LIMIT 200").fetchall()
        identities.extend({"actor":"token:"+str(row["id"]),"label":row["label"]} for row in tokens)
    return {"state_version":resolved.version,"owner_actor":resolved.data["owner_actor"],"grants":[dict(row) for row in grants],"identities":identities}


def project_access(connection, principal, result):
    manage_target(connection,principal,result["object_id"],lock=False)
    return result


def describe_access(connection,principal,target):
    return {"object_id":target.object_id,"version":target.version}


def commands() -> tuple[Command, ...]:
    return (Command("forms.permissions.set.v1","form","forms:manage_access","state_version",SetPermissions,PermissionsOutput,
                    manage_target,set_permissions,describe_access,project_access),)


def queries() -> tuple[Query, ...]:
    return (Query("forms.permissions.v1","forms:manage_access",Empty,AccessOutput,access_view,True),)
