from __future__ import annotations

import asyncio
from types import SimpleNamespace

from cope.core.stream import make_stream_event
import cope.web.app as web_app


class RecordingHub:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []
        self.internal: list[tuple[str, dict]] = []

    def publish(self, topic, event_type, data=None, *, source="web"):
        del source
        self.events.append((topic, event_type, data or {}))

    def publish_to_internal(self, event_type, data=None):
        self.internal.append((event_type, data or {}))


def test_admin_tournament_change_does_not_build_snapshot(monkeypatch) -> None:
    hub = RecordingHub()
    request = SimpleNamespace(
        url=SimpleNamespace(path="/api/admin/tournaments/42/status"),
        app=SimpleNamespace(state=SimpleNamespace(stream_hub=hub)),
    )
    monkeypatch.setattr(
        web_app,
        "connect_database",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("admin publication must not query the database")
        ),
    )

    web_app._publish_admin_post_streams(request)

    assert hub.internal == [
        ("runner.wake", {"reason": "/api/admin/tournaments/42/status"})
    ]
    assert hub.events == [
        ("tournament.42", "tournament.changed", {"tournament_id": 42})
    ]


def test_live_event_schedules_snapshot_instead_of_building_inline(monkeypatch) -> None:
    hub = RecordingHub()
    scheduled: list[int] = []
    app = SimpleNamespace(state=SimpleNamespace(stream_hub=hub))
    event = make_stream_event(
        "tournament.7",
        "tournament.live",
        {"tournament_id": 7, "game_id": 11},
        source="runner",
    )
    monkeypatch.setattr(
        web_app,
        "_schedule_tournament_snapshot",
        lambda _app, tournament_id: scheduled.append(tournament_id),
    )
    monkeypatch.setattr(
        web_app,
        "_tournament_snapshot",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("snapshot must not run inline")
        ),
    )

    asyncio.run(web_app._dispatch_internal_stream_event(app, event))

    assert scheduled == [7]
    assert hub.events == [
        (
            "tournament.7",
            "tournament.live",
            {"tournament_id": 7, "game_id": 11},
        )
    ]


def test_large_snapshot_is_published_as_compact_invalidation(monkeypatch) -> None:
    hub = RecordingHub()
    app = SimpleNamespace(state=SimpleNamespace(stream_hub=hub))
    monkeypatch.setattr(web_app, "MAX_BROADCAST_SNAPSHOT_GAMES", 2)
    monkeypatch.setattr(
        web_app,
        "_tournament_snapshot_for_broadcast",
        lambda *_args: None,
    )

    asyncio.run(web_app._publish_tournament_snapshot(app, 9))

    assert hub.events == [
        ("tournament.9", "tournament.changed", {"tournament_id": 9})
    ]
