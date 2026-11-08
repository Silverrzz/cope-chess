from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from cope.db import (
    EventRecord,
    event_resource_counts,
    get_chat_settings,
    get_event_chat_settings,
    list_chat_messages,
    list_event_awards,
    list_event_cast,
    list_event_contest_cast,
    list_event_contests,
    list_event_sessions,
    list_event_stages,
    list_event_updates,
    list_events,
)
from cope.db.events import EVENT_STATUSES

from .registry import event_extension_payload, event_modules, get_event_module


def event_catalogue(connection) -> dict:
    return {
        "events": [event_summary(connection, event, admin=True) for event in list_events(connection)],
        "statuses": list(EVENT_STATUSES),
        "registered_modules": [
            {"key": module.key, "label": module.label, "version": module.version}
            for module in event_modules().values()
        ],
    }


def event_summary(
    connection,
    event: EventRecord,
    *,
    admin: bool,
) -> dict[str, Any]:
    module = get_event_module(event.handler_key)
    sessions = list_event_sessions(connection, event.id)
    next_session = next(
        (
            session
            for session in sessions
            if session.status in {"live", "intermission", "scheduled", "pending", "postponed"}
        ),
        None,
    )
    payload = {
        "record": asdict(event) if admin else _public_event_record(event),
        "counts": event_resource_counts(connection, event.id),
        "next_session": (
            None if next_session is None
            else asdict(next_session) if admin
            else _public_event_child(next_session)
        ),
        "handler": {
            "key": event.handler_key,
            "required_version": event.handler_version,
            "available": module is not None,
            "current": module is not None and module.version == event.handler_version,
            "label": module.label if module is not None else event.handler_key,
            "installed_version": module.version if module is not None else None,
        },
    }
    return payload


def event_detail(
    connection,
    event: EventRecord,
    *,
    admin: bool,
    arena_available: bool = True,
) -> dict[str, Any]:
    module = get_event_module(event.handler_key)
    defaults = get_chat_settings(connection)
    chat_settings = get_event_chat_settings(connection, event.id, defaults=defaults)
    stages = list_event_stages(connection, event.id)
    sessions = list_event_sessions(connection, event.id)
    cast = list_event_cast(connection, event.id)
    contests = list_event_contests(connection, event.id)
    contest_cast = list_event_contest_cast(connection, event.id)
    updates = list_event_updates(connection, event.id, public_only=not admin)
    awards = list_event_awards(connection, event.id)
    if not admin:
        stages = tuple(_public_event_child(item) for item in stages)
        sessions = tuple(_public_event_child(item) for item in sessions)
        cast = tuple(_public_event_child(item) for item in cast)
        contests = tuple(_public_event_child(item, remove=("state",)) for item in contests)
        contest_cast = tuple(_public_event_child(item) for item in contest_cast)
        awards = tuple(_public_event_child(item) for item in awards)
    return {
        "server_time": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "event": event if admin else _public_event_record(event),
        "handler": {
            "key": event.handler_key,
            "required_version": event.handler_version,
            "available": module is not None,
            "current": module is not None and module.version == event.handler_version,
            "label": module.label if module is not None else event.handler_key,
            "installed_version": module.version if module is not None else None,
        },
        "stages": stages,
        "sessions": sessions,
        "cast": cast,
        "contests": contests,
        "contest_cast": contest_cast,
        "updates": updates,
        "awards": awards,
        "counts": event_resource_counts(connection, event.id),
        "chat_messages": (
            list_chat_messages(connection, limit=None, event_id=event.id, system=False)
            + list_chat_messages(connection, limit=100, event_id=event.id, system=True)
        ),
        "chat_settings": chat_settings,
        "custom": event_extension_payload(connection, event, admin=admin, arena_available=arena_available),
    }


def _public_event_record(event: EventRecord) -> dict[str, Any]:
    payload = asdict(event)
    payload.pop("config", None)
    payload.pop("state", None)
    return payload


def _public_event_child(item: Any, *, remove: tuple[str, ...] = ()) -> dict[str, Any]:
    payload = asdict(item)
    payload.pop("metadata", None)
    for key in remove:
        payload.pop(key, None)
    return payload
