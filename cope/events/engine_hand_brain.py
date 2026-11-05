from cope.db.events import create_event, get_event_by_slug, set_event_published
from .registry import EventModule, register_event_module


def _provision(connection):
    existing = get_event_by_slug(connection, "engine-hand-brain")
    if existing is not None:
        return existing.id
    event_id = create_event(
        connection,
        slug="engine-hand-brain",
        handler_key="engine-hand-brain",
        title="Engine Hand&Brain",
        subtitle="",
        summary="Competitors TBD.",
        description="",
        status="scheduled",
        featured=True,
        scheduled_start_at="2026-11-07T17:00:00+00:00",
        theme={"primary": "#b9133e", "accent": "#ff7293", "background": "#13080e", "surface": "#29101b", "text": "#ffe9e9"},
        config={"module": "engine-hand-brain", "announcement_only": True},
    )
    set_event_published(connection, event_id, True)
    return event_id


register_event_module(EventModule(key="engine-hand-brain", label="Engine Hand&Brain", version=1, provision=_provision))

