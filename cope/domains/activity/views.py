from __future__ import annotations

import uuid

from cope.application.errors import ApplicationError
from cope.application.views import CoherentView

from .queries import ActivityInput,ActivityItem,ActivityPage,Empty,activity_detail,list_activity,source_statements


def list_snapshot(connection, principal, target, payload):
    if payload.cursor is not None:
        raise ApplicationError("view_page_unavailable","Acquire the first page as a coherent view; ordinary continuation pages retain their separate current-membership contract.",422)
    return list_activity(connection,principal,target,payload)


def list_affected(connection, principal, target, payload, events):
    if not events:
        return False
    row = connection.execute(
        """SELECT 1 FROM application_objects object WHERE object.id=ANY(%s::uuid[]) AND object.type_key IN ('operation','job')
             AND EXISTS (SELECT 1 FROM application_object_aliases legacy WHERE legacy.object_id=object.id AND left(legacy.namespace,7)='legacy.')
             AND (%s::text IS NULL OR object.type_key=%s)
             AND (%s::text IS NULL OR EXISTS (SELECT 1 FROM application_object_aliases identity
                  WHERE identity.object_id=object.id AND identity.namespace=%s)) LIMIT 1""",
        ([event["object_id"] for event in events],payload.kind,payload.kind,payload.source,payload.source),
    ).fetchone()
    if row is not None:
        return True
    cut = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
    statement,parameters = source_statements(principal,cut,payload.source)
    visible = connection.execute(f'''SELECT 1 FROM ({statement}) visible WHERE visible.id=ANY(%s::uuid[]) AND left(visible.source,10)='canonical.'
        AND (%s::text IS NULL OR visible.kind=%s) LIMIT 1''',(*parameters,[event['object_id'] for event in events],payload.kind,payload.kind)).fetchone()
    return visible is not None


def detail_affected(connection, principal, target, payload, events):
    return any(str(event["object_id"])==str(uuid.UUID(target)) for event in events)


def views() -> tuple[CoherentView,...]:
    return (
        CoherentView("activity.list.v1","activity:inspect_legacy",ActivityInput,ActivityPage,list_snapshot,list_affected),
        CoherentView("legacy.activity.list.v1","activity:inspect_legacy",ActivityInput,ActivityPage,list_snapshot,list_affected),
        CoherentView("legacy.activity.detail.v1","activity:inspect_legacy",Empty,ActivityItem,activity_detail,detail_affected,True),
    )
