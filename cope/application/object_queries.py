from __future__ import annotations

import uuid
from typing import Literal

from .contracts import Input,Query
from .errors import ApplicationError


class Empty(Input):
    pass


class RemovalOutput(Input):
    id: str
    type_key: str
    source: str
    source_id: str
    reason: Literal["source_deleted","source_missing"]
    observed_at: str
    state_version: int
    cleanup_state: Literal["not_recorded"]
    restore_supported: Literal[False]


def removal_detail(connection, principal, target, payload: Empty) -> dict:
    try:
        resource_id = str(uuid.UUID(target))
    except (ValueError,TypeError,AttributeError) as error:
        raise ApplicationError("invalid_target","Supply an Object UUID.",422) from error
    row = connection.execute(
        """SELECT object.id::text,object.type_key,removal.source_namespace AS source,
                  removal.source_alias AS source_id,removal.reason,removal.observed_at::text,object.state_version,
                  'not_recorded'::text AS cleanup_state,FALSE AS restore_supported
           FROM application_object_removals removal JOIN application_objects object ON object.id=removal.object_id
           WHERE removal.object_id=%s::uuid""",(resource_id,),
    ).fetchone()
    if row is None:
        raise ApplicationError("removal_unavailable","No removal record is available for this Object.",404)
    return dict(row)


def queries() -> tuple[Query,...]:
    return (Query("objects.removal.v1","objects:inspect_removed",Empty,RemovalOutput,removal_detail,True),)
