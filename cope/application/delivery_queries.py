from __future__ import annotations

from typing import Literal

from .contracts import Input,Query
from .event_consumers import consumer_catalog


class Empty(Input):
    pass


class BoundedCount(Input):
    count: int
    more: bool
    limit: int = 1000


class ConsumerStatus(Input):
    key: str
    registered: bool
    state: Literal["current","pending","backoff","recovery_required","unavailable","not_started"]
    applied_position: str
    pending: BoundedCount
    failure_count: int
    error: str | None
    retry_at: str | None
    updated_at: str | None


class DeliveryStatus(Input):
    incarnation: str
    schema_version: int
    retained_after: str
    last_delivery_position: str
    last_delivered_at: str | None
    pending_source: BoundedCount
    consumers: list[ConsumerStatus]
    consumers_truncated: bool
    basis: Literal["single_statement_inspector"] = "single_statement_inspector"


def bounded(value: int) -> dict:
    return {"count":min(value,1000),"more":value>1000,"limit":1000}


def delivery_status(connection, principal, target, payload: Empty) -> dict:
    registered = {consumer.key for consumer in consumer_catalog()}
    if len(registered)>100:
        raise RuntimeError("The delivery inspector requires a paginated consumer catalogue for this installation")
    row = connection.execute(
        """WITH tail AS (SELECT COALESCE((SELECT position FROM application_event_log ORDER BY position DESC LIMIT 1),0) AS position,
                         (SELECT delivered_at FROM application_event_log ORDER BY position DESC LIMIT 1) AS delivered_at),
           pending AS (SELECT count(*) AS count FROM (SELECT id FROM application_outbox WHERE delivered_position IS NULL LIMIT 1001) sample),
           available_log AS (SELECT count(*) AS count FROM (SELECT position FROM application_event_log LIMIT 1001) sample),
           consumer_page AS (SELECT c.*,c.retry_at>clock_timestamp() AS cooling,
               (SELECT count(*) FROM (SELECT position FROM application_event_log WHERE position>c.applied_position LIMIT 1001) sample) AS pending
               FROM application_consumer_checkpoints c ORDER BY (consumer=ANY(%s::text[])) DESC,consumer LIMIT 101)
           SELECT state.incarnation,state.schema_version,state.retained_after::text,tail.position::text AS last_delivery_position,
                  tail.delivered_at AS last_delivered_at,pending.count AS pending_source,available_log.count AS available_log,
                  COALESCE((SELECT jsonb_agg(to_jsonb(consumer_page) ORDER BY consumer) FROM consumer_page),'[]'::jsonb) AS consumers
           FROM application_delivery_state state CROSS JOIN tail CROSS JOIN pending CROSS JOIN available_log WHERE singleton""",(sorted(registered),),
    ).fetchone()
    consumers = []
    for item in row["consumers"]:
        recovery = str(item["incarnation"]) != str(row["incarnation"]) or item["applied_position"]<int(row["retained_after"]) or item["applied_position"]>int(row["last_delivery_position"])
        state = "recovery_required" if recovery else "unavailable" if item["consumer"] not in registered else "backoff" if item["last_error"] and item["cooling"] else "unavailable" if item["last_error"] else "pending" if item["pending"] else "current"
        consumers.append({"key":item["consumer"],"registered":item["consumer"] in registered,"state":state,
                          "applied_position":str(item["applied_position"]),"pending":bounded(item["pending"]),
                          "failure_count":item["failure_count"],"error":item["last_error"] or None,
                          "retry_at":item["retry_at"] if item["last_error"] else None,"updated_at":item["updated_at"]})
    observed = {item["key"] for item in consumers}
    for key in sorted(registered-observed):
        consumers.append({"key":key,"registered":True,"state":"not_started","applied_position":"0","pending":bounded(row["available_log"]),
                          "failure_count":0,"error":None,"retry_at":None,"updated_at":None})
    consumers.sort(key=lambda item:(not item["registered"],item["key"]))
    return {"incarnation":str(row["incarnation"]),"schema_version":row["schema_version"],"retained_after":row["retained_after"],
            "last_delivery_position":row["last_delivery_position"],"last_delivered_at":row["last_delivered_at"].isoformat() if row["last_delivered_at"] else None,
            "pending_source":bounded(row["pending_source"]),"consumers":consumers[:100],"consumers_truncated":len(consumers)>100}


def queries() -> tuple[Query,...]:
    return (Query("events.status.v1","events:inspect",Empty,DeliveryStatus,delivery_status),)
