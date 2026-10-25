from __future__ import annotations

from cope.application.event_consumers import EventConsumer,consumer_catalog
from cope.db import connect_database


class ConsumerRecoveryRequired(RuntimeError):
    pass


class ConsumerSchemaUnavailable(RuntimeError):
    pass


def dispatch_outbox(connection, limit: int = 200) -> int:
    if not 1 <= limit <= 500:
        raise ValueError("Delivery batches must contain between one and 500 events")
    leader = connection.execute("SELECT pg_try_advisory_xact_lock(hashtextextended('application-delivery.v1',0)) AS acquired").fetchone()["acquired"]
    if not leader:
        return 0
    events = connection.execute(
        "SELECT id FROM application_outbox WHERE delivered_position IS NULL ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED",(limit,),
    ).fetchall()
    for event in events:
        delivered = connection.execute(
            """INSERT INTO application_event_log(position,event_id)
               VALUES (nextval('application_delivery_position'),%s) RETURNING position""",(event["id"],),
        ).fetchone()
        connection.execute("UPDATE application_outbox SET delivered_position=%s WHERE id=%s",(delivered["position"],event["id"]))
    return len(events)


def consume_events(connection, consumer: EventConsumer, limit: int = 100) -> int:
    if not 1 <= limit <= 500:
        raise ValueError("Consumer batches must contain between one and 500 events")
    delivery = connection.execute("SELECT incarnation,retained_after FROM application_delivery_state WHERE singleton FOR SHARE").fetchone()
    connection.execute("INSERT INTO application_consumer_checkpoints(consumer,incarnation) VALUES (%s,%s) ON CONFLICT DO NOTHING",(consumer.key,delivery["incarnation"]))
    checkpoint = connection.execute("SELECT * FROM application_consumer_checkpoints WHERE consumer=%s AND retry_at<=clock_timestamp() FOR UPDATE SKIP LOCKED",(consumer.key,)).fetchone()
    if checkpoint is None:
        return 0
    tail = connection.execute("SELECT COALESCE(max(position),0) AS position FROM application_event_log").fetchone()["position"]
    if checkpoint["incarnation"] != delivery["incarnation"] or checkpoint["applied_position"] < delivery["retained_after"] or checkpoint["applied_position"]>tail:
        raise ConsumerRecoveryRequired(f"Durable consumer {consumer.key} requires an explicit rebuild or recovery decision")
    events = connection.execute(
        """SELECT log.position,log.schema_version,source.id AS event_id,source.object_id,source.kind,source.payload,source.created_at
           FROM application_event_log log JOIN application_outbox source ON source.id=log.event_id
           WHERE log.position>%s ORDER BY log.position LIMIT %s""",(checkpoint["applied_position"],limit),
    ).fetchall()
    for event in events:
        if event["schema_version"] not in consumer.schema_versions:
            raise ConsumerSchemaUnavailable(f"Durable consumer {consumer.key} cannot apply this required event schema")
        received = connection.execute(
            "INSERT INTO application_consumer_receipts(consumer,event_id) VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING event_id",
            (consumer.key,event["event_id"]),
        ).fetchone()
        if received:
            consumer.apply(connection,dict(event))
    if events:
        connection.execute("UPDATE application_consumer_checkpoints SET applied_position=%s,updated_at=clock_timestamp(),failure_count=0,last_error='' WHERE consumer=%s",(events[-1]["position"],consumer.key))
    return len(events)


def run_event_cycle(database_url: str) -> int:
    connection = connect_database(database_url)
    total = 0
    try:
        total += dispatch_outbox(connection.native_connection())
        connection.commit()
        for consumer in consumer_catalog():
            try:
                total += consume_events(connection.native_connection(),consumer)
                connection.commit()
            except Exception as error:
                connection.rollback()
                code = "consumer_rebase_required" if isinstance(error,ConsumerRecoveryRequired) else "consumer_schema_unavailable" if isinstance(error,ConsumerSchemaUnavailable) else "consumer_handler_failed"
                raw = connection.native_connection()
                raw.execute(
                    """INSERT INTO application_consumer_checkpoints(consumer,incarnation,failure_count,last_error,retry_at)
                       SELECT %s,incarnation,1,%s,clock_timestamp()+interval '2 seconds' FROM application_delivery_state WHERE singleton
                       ON CONFLICT(consumer) DO UPDATE SET failure_count=application_consumer_checkpoints.failure_count+1,
                       last_error=EXCLUDED.last_error,retry_at=clock_timestamp()+LEAST(300,power(2,LEAST(application_consumer_checkpoints.failure_count+1,8))) * interval '1 second'""",
                    (consumer.key,code),
                )
                connection.commit()
        return total
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
