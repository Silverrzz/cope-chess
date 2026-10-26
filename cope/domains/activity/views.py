from __future__ import annotations

import uuid

from cope.application.errors import ApplicationError
from cope.application.views import CoherentView

from .queries import ActivityInput,ActivityItem,ActivityPage,Empty,activity_detail,list_activity


def list_snapshot(connection, principal, target, payload):
    if payload.cursor is not None:
        raise ApplicationError("view_page_unavailable","Acquire the first page as a coherent view; ordinary continuation pages retain their separate current-membership contract.",422)
    return list_activity(connection,principal,target,payload)


def list_affected(connection, principal, target, payload, events):
    if not events:
        return False
    row = connection.execute(
        """SELECT 1 FROM application_objects object WHERE object.id=ANY(%s::uuid[]) AND object.type_key IN ('operation','job')
             AND (%s::text IS NULL OR object.type_key=%s)
             AND (%s::text IS NULL OR EXISTS (SELECT 1 FROM application_object_aliases identity
                  WHERE identity.object_id=object.id AND identity.namespace=%s)) LIMIT 1""",
        ([event["object_id"] for event in events],payload.kind,payload.kind,payload.source,payload.source),
    ).fetchone()
    return row is not None


def detail_affected(connection, principal, target, payload, events):
    return any(str(event["object_id"])==str(uuid.UUID(target)) for event in events)


def views() -> tuple[CoherentView,...]:
    return (
        CoherentView("legacy.activity.list.v1","activity:inspect_legacy",ActivityInput,ActivityPage,list_snapshot,list_affected),
        CoherentView("legacy.activity.detail.v1","activity:inspect_legacy",Empty,ActivityItem,activity_detail,detail_affected,True),
    )
