from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException, Request

from cope.adapters.http.responses import _json, _publish_admin_change
from cope.application.errors import ApplicationError
from cope.db import (
    CURRENT_EVENT_STATUSES,
    EventRecord,
    get_chat_settings,
    get_event_by_slug,
    get_event_chat_settings,
    list_events,
)
from cope.events import provision_event_module
from cope.events.lifecycle import remove_event, reprovision_event, require_event
from cope.events.views import event_catalogue, event_detail, event_summary
from cope.web.requests import read_form


def register_event_routes(app: FastAPI) -> None:
    from cope.web import app as web_app

    @app.get("/api/events/current")
    def current_public_event(connection: sqlite3.Connection = Depends(web_app._database)):
        event = next(
            (
                item
                for item in list_events(connection, public_only=True)
                if item.status in CURRENT_EVENT_STATUSES
            ),
            None,
        )
        return _json({
            "event": (
                event_summary(connection, event, admin=False)
                if event is not None
                else None
            ),
        })

    @app.get("/api/events")
    def public_events(connection: sqlite3.Connection = Depends(web_app._database)):
        events = list_events(connection, public_only=True)
        current = next(
            (item for item in events if item.status in CURRENT_EVENT_STATUSES),
            None,
        )
        return _json({
            "server_time": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "current": (
                event_summary(connection, current, admin=False)
                if current is not None
                else None
            ),
            "events": [
                event_summary(connection, event, admin=False)
                for event in events
            ],
        })

    @app.get("/api/events/{slug}")
    def public_event(
        slug: str,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = _require_viewable_event(connection, slug, request)
        arena_available = (
            web_app._event_arena_is_public(event)
            or web_app._admin_request_authenticated(request)
        )
        payload = event_detail(connection, event, admin=False, arena_available=arena_available)
        payload["arena_available"] = arena_available
        payload["spectator_count"] = request.app.state.stream_hub.event_spectator_count(
            event.id
        )
        return _json(payload)

    @app.post("/api/events/{slug}/chat")
    async def public_event_chat(
        slug: str,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = _require_viewable_event(connection, slug, request)
        form = await read_form(request)
        settings = get_event_chat_settings(
            connection,
            event.id,
            defaults=get_chat_settings(connection),
        )

        def create_message():
            return web_app._create_event_chat_message_from_form(
                connection,
                form,
                event_id=event.id,
                settings=settings,
            )

        message = await asyncio.to_thread(create_message)
        if message is not None:
            web_app._publish_event_chat_message(request, event.id, message)
        return _json({"message": message}, status_code=201)

    @app.get("/api/admin/events")
    def admin_events(connection: sqlite3.Connection = Depends(web_app._database)):
        return _json(event_catalogue(connection))

    @app.get("/api/admin/events/{event_id}")
    def admin_event(
        event_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            event = require_event(connection, event_id)
        except ApplicationError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        return _json(event_detail(connection, event, admin=True))

    @app.delete("/api/admin/events/{event_id}")
    def admin_delete_event(
        event_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = _apply_event_change(connection, remove_event, event_id)
        request.app.state.stream_hub.link_event_tournaments(event_id, ())
        _publish_admin_change(web_app, request)
        return _json({"message": f"{event.title} deleted."})

    @app.post("/api/admin/events/{event_id}/reset")
    def admin_reset_event(
        event_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = _apply_event_change(connection, reprovision_event, event_id)
        request.app.state.stream_hub.link_event_tournaments(event_id, ())
        request.app.state.stream_hub.publish(
            f"event.{event_id}",
            "event.changed",
            {"event_id": event_id},
            source="web",
        )
        _publish_admin_change(web_app, request)
        return _json({"message": f"{event.title} reset to a new draft."})

    @app.post("/api/admin/event-modules/{module_key}/provision")
    def admin_provision_event_module(
        module_key: str,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = _apply_event_change(connection, provision_event_module, module_key)
        _publish_admin_change(web_app, request)
        return _json(
            {
                "id": event.id,
                "event": event_summary(connection, event, admin=True),
                "message": f"{event.title} created.",
            },
            status_code=201,
        )


def _require_viewable_event(
    connection: sqlite3.Connection,
    slug: str,
    request: Request,
) -> EventRecord:
    from cope.web import app as web_app

    event = get_event_by_slug(connection, slug)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found.")
    if web_app._event_is_public(event):
        return event
    if not web_app._admin_request_authenticated(request):
        raise HTTPException(status_code=401, detail="Admin session required.")
    return event


def _apply_event_change(connection, handler, target):
    try:
        event = handler(connection, target)
        connection.commit()
    except ApplicationError as exc:
        connection.rollback()
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        connection.rollback()
        raise HTTPException(status_code=422 if handler is provision_event_module else 409, detail=str(exc)) from exc
    return event
