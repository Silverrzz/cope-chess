from datetime import UTC

from fastapi import Depends, HTTPException, Request
from pydantic import AwareDatetime, BaseModel

from cope.adapters.http.responses import _json, _publish_admin_change
from cope.db.events import (
    create_event,
    get_event,
    set_event_published,
    set_event_status,
    update_event_identity,
)

from .registry import EventModule, register_event_module


MODULE_KEY = "secret-queen"
MODULE_VERSION = 1
ANNOUNCEMENT_STATUSES = {"draft", "announced", "scheduled"}


class AnnouncementSettings(BaseModel):
    scheduled_start_at: AwareDatetime | None
    published: bool


def _provision(connection):
    return create_event(
        connection,
        slug=MODULE_KEY,
        handler_key=MODULE_KEY,
        handler_version=MODULE_VERSION,
        title="Secret Queen",
        summary="One pawn on each side is secretly a queen.",
        featured=True,
        theme={
            "primary": "#8f4feb",
            "accent": "#c5a3ff",
            "background": "#08060c",
            "surface": "#15101d",
            "text": "#f7f5fa",
        },
    )


def _register_api(app):
    from cope.web import app as web_app

    @app.put("/api/admin/events/{event_id}/secret-queen/announcement")
    def save_announcement(
        event_id: int,
        payload: AnnouncementSettings,
        request: Request,
        connection=Depends(web_app._database),
    ):
        event = get_event(connection, event_id, lock=True)
        if event is None or (event.handler_key, event.handler_version) != (MODULE_KEY, MODULE_VERSION):
            raise HTTPException(404, "Secret Queen event not found.")
        if event.status not in ANNOUNCEMENT_STATUSES:
            raise HTTPException(409, "The announcement can only be edited before the event starts.")

        start = payload.scheduled_start_at
        scheduled_start_at = start.astimezone(UTC).isoformat(timespec="seconds") if start else None
        status = "scheduled" if start else "announced" if payload.published else "draft"
        try:
            if scheduled_start_at != event.scheduled_start_at:
                update_event_identity(
                    connection,
                    event_id,
                    title=event.title,
                    subtitle=event.subtitle,
                    summary=event.summary,
                    description=event.description,
                    rules=event.rules,
                    featured=event.featured,
                    scheduled_start_at=scheduled_start_at,
                    scheduled_end_at=event.scheduled_end_at,
                    theme=event.theme,
                )
            if status != event.status:
                set_event_status(connection, event_id, status)
            if payload.published != bool(event.published_at):
                set_event_published(connection, event_id, payload.published)
            connection.commit()
        except ValueError as error:
            connection.rollback()
            raise HTTPException(422, str(error)) from error

        request.app.state.stream_hub.publish(
            f"event.{event_id}", "event.changed", {"event_id": event_id}, source="web"
        )
        _publish_admin_change(web_app, request)
        return _json({"message": "Announcement saved."})


register_event_module(
    EventModule(
        key=MODULE_KEY,
        label="Secret Queen",
        version=MODULE_VERSION,
        provision=_provision,
        register_api=_register_api,
        public_arena_statuses=frozenset(),
    )
)
