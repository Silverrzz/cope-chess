from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class EventConsumer:
    key: str
    schema_versions: tuple[int,...]
    apply: Callable


def update_object_invalidation(connection, event: dict) -> None:
    connection.execute(
        """INSERT INTO application_object_changes(object_id,delivered_position,kind,changed_at)
           VALUES (%s,%s,%s,%s) ON CONFLICT(object_id) DO UPDATE SET
           delivered_position=EXCLUDED.delivered_position,kind=EXCLUDED.kind,changed_at=EXCLUDED.changed_at
           WHERE application_object_changes.delivered_position<EXCLUDED.delivered_position""",
        (event["object_id"],event["position"],event["kind"],event["created_at"]),
    )


def consumer_catalog() -> tuple[EventConsumer,...]:
    providers = (EventConsumer("object_invalidation.v1",(1,),update_object_invalidation),)
    if len({provider.key for provider in providers}) != len(providers):
        raise RuntimeError("Duplicate durable consumer registration")
    return providers
