from __future__ import annotations

from cope.application.errors import ApplicationError
from cope.db import delete_event, delete_opening_suite, delete_tournament, reset_event
from cope.db.events import EventRecord, get_event, list_event_tournament_links

from .registry import EventDeletion, event_handler


def require_event(connection, event_id: int, *, lock: bool = False) -> EventRecord:
    event = get_event(connection, event_id, lock=lock)
    if event is None:
        raise ApplicationError("event_not_found", "Event not found.", 404)
    return event


def _inactive_tournaments(connection, event_id: int, action: str) -> list[tuple[int, str]]:
    ids = sorted({int(row["tournament_id"]) for row in list_event_tournament_links(connection, event_id=event_id)})
    tournaments = []
    for tournament_id in ids:
        row = connection.execute(
            "SELECT status FROM tournaments WHERE id = ? FOR UPDATE", (tournament_id,)
        ).fetchone()
        if row is None:
            continue
        status = row["status"]
        if status in {"running", "paused"}:
            raise ApplicationError("event_active", f"Abort active event tournaments before {action} this event.")
        tournaments.append((tournament_id, status))
    return tournaments


def remove_event(connection, event_id: int) -> EventRecord:
    event = require_event(connection, event_id, lock=True)
    tournaments = _inactive_tournaments(connection, event_id, "deleting")
    module = event_handler(event)
    cleanup = module.deletion_resources(connection, event) if module and module.deletion_resources else EventDeletion()
    for tournament_id, status in tournaments:
        if status in {"draft", "scheduled"} or cleanup.delete_tournament_history:
            delete_tournament(connection, tournament_id)
    delete_event(connection, event_id)
    for suite_id in cleanup.opening_suite_ids:
        delete_opening_suite(connection, suite_id)
    return event


def reprovision_event(connection, event_id: int) -> EventRecord:
    event = require_event(connection, event_id, lock=True)
    tournaments = _inactive_tournaments(connection, event_id, "resetting")
    module = event_handler(event)
    if module is None:
        raise ApplicationError("event_handler_unavailable", "Install the matching event module before resetting this event.")
    for tournament_id, _ in tournaments:
        delete_tournament(connection, tournament_id)
    reset_event(connection, event_id)
    if module.reset is not None:
        module.reset(connection, event_id)
    return require_event(connection, event_id)
