from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable


EVENT_STATUSES = (
    "draft",
    "announced",
    "scheduled",
    "live",
    "intermission",
    "postponed",
    "completed",
    "cancelled",
)
CURRENT_EVENT_STATUSES = (
    "announced",
    "scheduled",
    "live",
    "intermission",
    "postponed",
)
EVENT_STAGE_STATUSES = ("pending", "active", "completed", "cancelled")
EVENT_ITEM_STATUSES = (
    "pending",
    "scheduled",
    "live",
    "intermission",
    "completed",
    "postponed",
    "cancelled",
)
EVENT_CAST_KINDS = ("engine", "team", "person", "other")
EVENT_CAST_STATUSES = ("active", "reserve", "withdrawn", "eliminated")
EVENT_UPDATE_KINDS = ("announcement", "schedule", "incident", "result", "milestone")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class EventRecord:
    id: int
    slug: str
    handler_key: str
    handler_version: int
    title: str
    subtitle: str
    summary: str
    description: str
    rules: str
    status: str
    featured: bool
    published_at: str | None
    scheduled_start_at: str | None
    scheduled_end_at: str | None
    started_at: str | None
    finished_at: str | None
    theme: dict[str, Any]
    config: dict[str, Any]
    state: dict[str, Any]
    revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class EventStageRecord:
    id: int
    event_id: int
    stage_key: str
    title: str
    summary: str
    status: str
    position: int
    scheduled_start_at: str | None
    scheduled_end_at: str | None
    started_at: str | None
    finished_at: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EventSessionRecord:
    id: int
    event_id: int
    stage_id: int | None
    session_key: str
    title: str
    summary: str
    status: str
    position: int
    scheduled_start_at: str | None
    scheduled_end_at: str | None
    started_at: str | None
    finished_at: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EventCastMemberRecord:
    id: int
    event_id: int
    parent_id: int | None
    member_key: str
    kind: str
    display_name: str
    short_name: str
    role: str
    status: str
    engine_version_id: int | None
    profile: str
    avatar_url: str
    accent_color: str
    position: int
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EventContestRecord:
    id: int
    event_id: int
    stage_id: int | None
    session_id: int | None
    contest_key: str
    title: str
    summary: str
    status: str
    position: int
    scheduled_start_at: str | None
    scheduled_end_at: str | None
    started_at: str | None
    finished_at: str | None
    result: str
    state: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EventContestCastRecord:
    contest_id: int
    cast_member_id: int
    side: str
    role: str
    position: int
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EventUpdateRecord:
    id: int
    event_id: int
    kind: str
    title: str
    body: str
    pinned: bool
    occurred_at: str
    published_at: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class EventAwardRecord:
    id: int
    event_id: int
    award_key: str
    title: str
    description: str
    recipient_cast_id: int | None
    recipient_label: str
    position: int
    awarded_at: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EventChatSettingsRecord:
    enabled: bool
    slowmode_seconds: int
    max_message_length: int
    allow_anonymous_names: bool
    retention_days: int


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def create_event(
    connection: sqlite3.Connection,
    *,
    slug: str,
    handler_key: str,
    title: str,
    handler_version: int = 1,
    subtitle: str = "",
    summary: str = "",
    description: str = "",
    rules: str = "",
    status: str = "draft",
    featured: bool = False,
    scheduled_start_at: str | None = None,
    scheduled_end_at: str | None = None,
    theme: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> int:
    slug = slug.strip().lower()
    handler_key = handler_key.strip()
    title = title.strip()
    if not _SLUG.fullmatch(slug):
        raise ValueError("event slug must contain lowercase words separated by hyphens")
    if not handler_key:
        raise ValueError("event handler key is required")
    if not title:
        raise ValueError("event title is required")
    if handler_version < 1:
        raise ValueError("event handler version must be positive")
    _require_choice(status, EVENT_STATUSES, "event status")
    scheduled_start_at = _optional_timestamp(scheduled_start_at)
    scheduled_end_at = _optional_timestamp(scheduled_end_at)
    _require_schedule_order(scheduled_start_at, scheduled_end_at)
    now = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO events (
          slug, handler_key, handler_version, title, subtitle, summary,
          description, rules, status, featured, scheduled_start_at,
          scheduled_end_at, theme, config, state, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            slug,
            handler_key,
            handler_version,
            title,
            subtitle.strip(),
            summary.strip(),
            description.strip(),
            rules.strip(),
            status,
            int(featured),
            scheduled_start_at,
            scheduled_end_at,
            _json_dump(theme or {}),
            _json_dump(config or {}),
            _json_dump(state or {}),
            now,
            now,
        ),
    )
    return int(cursor.lastrowid)


def get_event(connection: sqlite3.Connection, event_id: int) -> EventRecord | None:
    row = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return None if row is None else _event_from_row(row)


def get_event_by_slug(connection: sqlite3.Connection, slug: str) -> EventRecord | None:
    row = connection.execute("SELECT * FROM events WHERE slug = ?", (slug,)).fetchone()
    return None if row is None else _event_from_row(row)


def delete_event(connection: sqlite3.Connection, event_id: int) -> EventRecord | None:
    event = get_event(connection, event_id)
    if event is None:
        return None
    connection.execute("DELETE FROM events WHERE id = ?", (event_id,))
    return event


def reset_event(connection: sqlite3.Connection, event_id: int) -> EventRecord:
    event = get_event(connection, event_id)
    if event is None:
        raise ValueError("event does not exist")
    for table in (
        "worker_event_permissions",
        "event_awards",
        "event_updates",
        "event_contests",
        "event_sessions",
        "event_stages",
        "event_chat_settings",
        "chat_messages",
    ):
        connection.execute(f"DELETE FROM {table} WHERE event_id = ?", (event_id,))
    connection.execute(
        "UPDATE event_cast_members SET status = 'active' WHERE event_id = ?",
        (event_id,),
    )
    connection.execute(
        """
        UPDATE events
        SET status = 'draft', published_at = NULL,
            scheduled_start_at = NULL, scheduled_end_at = NULL,
            started_at = NULL, finished_at = NULL, state = '{}',
            revision = revision + 1, updated_at = ?
        WHERE id = ?
        """,
        (utc_now(), event_id),
    )
    return _required_event(connection, event_id)


def list_events(
    connection: sqlite3.Connection,
    *,
    public_only: bool = False,
) -> tuple[EventRecord, ...]:
    where = "WHERE published_at IS NOT NULL AND status != 'draft'" if public_only else ""
    rows = connection.execute(
        f"""
        SELECT * FROM events
        {where}
        ORDER BY featured DESC,
                 CASE WHEN status IN ('live', 'intermission') THEN 0
                      WHEN status IN ('announced', 'scheduled', 'postponed') THEN 1
                      ELSE 2 END,
                 CASE WHEN status IN ('announced', 'scheduled', 'postponed')
                      THEN scheduled_start_at END ASC NULLS LAST,
                 CASE WHEN status IN ('completed', 'cancelled')
                      THEN COALESCE(finished_at, scheduled_start_at) END DESC NULLS LAST,
                 id DESC
        """
    )
    return tuple(_event_from_row(row) for row in rows)


def update_event_identity(
    connection: sqlite3.Connection,
    event_id: int,
    *,
    title: str,
    subtitle: str,
    summary: str,
    description: str,
    rules: str,
    featured: bool,
    scheduled_start_at: str | None,
    scheduled_end_at: str | None,
    theme: dict[str, Any],
) -> EventRecord:
    title = title.strip()
    if not title:
        raise ValueError("event title is required")
    scheduled_start_at = _optional_timestamp(scheduled_start_at)
    scheduled_end_at = _optional_timestamp(scheduled_end_at)
    _require_schedule_order(scheduled_start_at, scheduled_end_at)
    now = utc_now()
    cursor = connection.execute(
        """
        UPDATE events
        SET title = ?, subtitle = ?, summary = ?, description = ?, rules = ?,
            featured = ?, scheduled_start_at = ?, scheduled_end_at = ?,
            theme = ?, revision = revision + 1, updated_at = ?
        WHERE id = ?
        """,
        (
            title,
            subtitle.strip(),
            summary.strip(),
            description.strip(),
            rules.strip(),
            int(featured),
            scheduled_start_at,
            scheduled_end_at,
            _json_dump(theme),
            now,
            event_id,
        ),
    )
    if cursor.rowcount == 0:
        raise ValueError("event does not exist")
    return _required_event(connection, event_id)


def update_event_state(
    connection: sqlite3.Connection,
    event_id: int,
    state: dict[str, Any],
) -> EventRecord:
    cursor = connection.execute(
        """
        UPDATE events
        SET state = ?, revision = revision + 1, updated_at = ?
        WHERE id = ?
        """,
        (_json_dump(state), utc_now(), event_id),
    )
    if cursor.rowcount == 0:
        raise ValueError("event does not exist")
    return _required_event(connection, event_id)


def set_event_status(
    connection: sqlite3.Connection,
    event_id: int,
    status: str,
) -> EventRecord:
    _require_choice(status, EVENT_STATUSES, "event status")
    now = utc_now()
    started = ", started_at = COALESCE(started_at, ?)" if status == "live" else ""
    finished = ", finished_at = COALESCE(finished_at, ?)" if status in {"completed", "cancelled"} else ""
    parameters: list[Any] = [status]
    if started:
        parameters.append(now)
    if finished:
        parameters.append(now)
    parameters.extend((now, event_id))
    cursor = connection.execute(
        f"""
        UPDATE events
        SET status = ?{started}{finished}, revision = revision + 1, updated_at = ?
        WHERE id = ?
        """,
        parameters,
    )
    if cursor.rowcount == 0:
        raise ValueError("event does not exist")
    return _required_event(connection, event_id)


def set_event_published(
    connection: sqlite3.Connection,
    event_id: int,
    published: bool,
) -> EventRecord:
    row = connection.execute(
        "SELECT * FROM events WHERE id = ? FOR UPDATE",
        (event_id,),
    ).fetchone()
    if row is None:
        raise ValueError("event does not exist")
    current = _event_from_row(row)
    if published and current.status == "draft":
        raise ValueError("a draft event cannot be published")
    published_at = (current.published_at or utc_now()) if published else None
    connection.execute(
        """
        UPDATE events
        SET published_at = ?, revision = revision + 1, updated_at = ?
        WHERE id = ?
        """,
        (published_at, utc_now(), event_id),
    )
    return _required_event(connection, event_id)


def reconcile_engine_relay_event(
    connection: sqlite3.Connection,
    event_id: int,
) -> EventRecord:
    row = connection.execute(
        "SELECT * FROM events WHERE id = ? FOR UPDATE",
        (event_id,),
    ).fetchone()
    if row is None:
        raise ValueError("event does not exist")
    current = _event_from_row(row)
    if current.handler_key not in {"engine-relay", "engine-relay-finale"}:
        return current
    fixtures = tuple(
        connection.execute(
            """
            SELECT fixture.id AS fixture_id, tournament.id AS tournament_id,
                   tournament.status, tournament.scheduled_start_at,
                   tournament.started_at, tournament.finished_at
            FROM engine_relay_fixtures fixture
            JOIN tournaments tournament ON tournament.id = fixture.tournament_id
            WHERE fixture.event_id = ?
            ORDER BY fixture.position, fixture.id
            """,
            (event_id,),
        )
    )
    statuses = {str(row["status"]) for row in fixtures}
    if not fixtures:
        status = "announced" if current.published_at is not None else "draft"
    elif "running" in statuses:
        status = "live"
    elif "paused" in statuses:
        status = "intermission"
    elif statuses <= {"finished", "aborted"}:
        status = "completed" if "finished" in statuses else "cancelled"
    elif statuses.intersection({"finished", "aborted"}):
        status = "intermission"
    elif "scheduled" in statuses:
        status = "scheduled"
    else:
        status = "announced"
    scheduled_values = [
        str(row["scheduled_start_at"])
        for row in fixtures
        if row["scheduled_start_at"] is not None
    ]
    started_values = [
        str(row["started_at"])
        for row in fixtures
        if row["started_at"] is not None
    ]
    finished_values = [
        str(row["finished_at"])
        for row in fixtures
        if row["finished_at"] is not None
    ]
    scheduled_start_at = min(scheduled_values) if scheduled_values else None
    started_at = min(started_values) if started_values else None
    finished_at = (
        max(finished_values)
        if finished_values and status in {"completed", "cancelled"}
        else None
    )
    status_counts = {
        name: sum(1 for row in fixtures if str(row["status"]) == name)
        for name in ("draft", "scheduled", "running", "paused", "finished", "aborted")
    }
    state = {
        **current.state,
        "lifecycle": {
            "version": 1,
            "source": "engine-relay-fixtures",
            "status": status,
            "fixture_count": len(fixtures),
            "status_counts": status_counts,
            "active_fixture_ids": [
                int(row["fixture_id"])
                for row in fixtures
                if str(row["status"]) in {"running", "paused"}
            ],
            "active_tournament_ids": [
                int(row["tournament_id"])
                for row in fixtures
                if str(row["status"]) in {"running", "paused"}
            ],
            "scheduled_start_at": scheduled_start_at,
            "started_at": started_at,
            "finished_at": finished_at,
        },
    }
    state_json = _json_dump(state)
    connection.execute(
        """
        UPDATE events
        SET status = ?, scheduled_start_at = ?,
            started_at = COALESCE(started_at, ?), finished_at = ?,
            state = ?, revision = revision + 1, updated_at = ?
        WHERE id = ?
          AND (
            status IS DISTINCT FROM ?
            OR scheduled_start_at IS DISTINCT FROM ?
            OR started_at IS DISTINCT FROM COALESCE(started_at, ?)
            OR finished_at IS DISTINCT FROM ?
            OR state IS DISTINCT FROM ?
          )
        """,
        (
            status,
            scheduled_start_at,
            started_at,
            finished_at,
            state_json,
            utc_now(),
            event_id,
            status,
            scheduled_start_at,
            started_at,
            finished_at,
            state_json,
        ),
    )
    return _required_event(connection, event_id)


def reconcile_engine_relay_events_for_tournament(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> tuple[EventRecord, ...]:
    event_ids = tuple(
        int(row["event_id"])
        for row in connection.execute(
            "SELECT event_id FROM engine_relay_fixtures WHERE tournament_id = ?",
            (tournament_id,),
        )
    )
    reconciled = [
        reconcile_engine_relay_event(connection, event_id)
        for event_id in event_ids
    ]
    gauntlet = connection.execute(
        """
        SELECT gauntlet.event_id, tournament.status
        FROM puzzle_gauntlet_events gauntlet
        JOIN tournaments tournament ON tournament.id = gauntlet.tournament_id
        WHERE gauntlet.tournament_id = ?
        """,
        (tournament_id,),
    ).fetchone()
    if gauntlet is not None:
        event = get_event(connection, int(gauntlet["event_id"]))
        if event is not None:
            status_map = {
                "scheduled": "scheduled",
                "running": "live",
                "paused": "intermission",
                "finished": "completed",
                "aborted": "cancelled",
            }
            phase_map = {
                "scheduled": "countdown",
                "running": "live",
                "paused": "live",
                "finished": "completed",
                "aborted": "completed",
            }
            tournament_status = str(gauntlet["status"])
            state = {**event.state, "phase": phase_map.get(tournament_status, event.state.get("phase", "countdown"))}
            connection.execute(
                """
                UPDATE events
                SET status = ?, state = ?,
                    started_at = CASE WHEN ? = 'live' THEN COALESCE(started_at, ?) ELSE started_at END,
                    finished_at = CASE WHEN ? IN ('completed', 'cancelled') THEN COALESCE(finished_at, ?) ELSE finished_at END,
                    revision = revision + 1, updated_at = ?
                WHERE id = ?
                """,
                (
                    status_map.get(tournament_status, event.status),
                    _json_dump(state),
                    status_map.get(tournament_status, event.status),
                    utc_now(),
                    status_map.get(tournament_status, event.status),
                    utc_now(),
                    utc_now(),
                    event.id,
                ),
            )
            updated = get_event(connection, event.id)
            if updated is not None:
                reconciled.append(updated)
    return tuple(reconciled)


def reconcile_all_engine_relay_events(
    connection: sqlite3.Connection,
) -> tuple[EventRecord, ...]:
    event_ids = tuple(
        int(row["id"])
        for row in connection.execute(
            "SELECT id FROM events WHERE handler_key IN ('engine-relay', 'engine-relay-finale') ORDER BY id"
        )
    )
    return tuple(
        reconcile_engine_relay_event(connection, event_id)
        for event_id in event_ids
    )


def create_event_stage(
    connection: sqlite3.Connection,
    event_id: int,
    *,
    stage_key: str,
    title: str,
    position: int,
    summary: str = "",
    status: str = "pending",
    scheduled_start_at: str | None = None,
    scheduled_end_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    _require_event_child_inputs(stage_key, title, position)
    _require_choice(status, EVENT_STAGE_STATUSES, "event stage status")
    start, end = _schedule(scheduled_start_at, scheduled_end_at)
    cursor = connection.execute(
        """
        INSERT INTO event_stages (
          event_id, stage_key, title, summary, status, position,
          scheduled_start_at, scheduled_end_at, metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (event_id, stage_key, title, summary, status, position, start, end, _json_dump(metadata or {})),
    )
    _touch_event(connection, event_id)
    return int(cursor.lastrowid)


def list_event_stages(connection: sqlite3.Connection, event_id: int) -> tuple[EventStageRecord, ...]:
    return tuple(
        _stage_from_row(row)
        for row in connection.execute(
            "SELECT * FROM event_stages WHERE event_id = ? ORDER BY position, id",
            (event_id,),
        )
    )


def set_event_stage_status(
    connection: sqlite3.Connection,
    stage_id: int,
    status: str,
) -> EventStageRecord:
    _require_choice(status, EVENT_STAGE_STATUSES, "event stage status")
    row = connection.execute(
        "SELECT event_id FROM event_stages WHERE id = ?",
        (stage_id,),
    ).fetchone()
    if row is None:
        raise ValueError("event stage does not exist")
    now = utc_now()
    started_at = now if status == "active" else None
    finished_at = now if status in {"completed", "cancelled"} else None
    connection.execute(
        """
        UPDATE event_stages
        SET status = ?,
            started_at = CASE WHEN ? IS NULL THEN started_at ELSE COALESCE(started_at, ?) END,
            finished_at = CASE WHEN ? IS NULL THEN finished_at ELSE COALESCE(finished_at, ?) END
        WHERE id = ?
        """,
        (status, started_at, started_at, finished_at, finished_at, stage_id),
    )
    _touch_event(connection, int(row["event_id"]))
    updated = connection.execute("SELECT * FROM event_stages WHERE id = ?", (stage_id,)).fetchone()
    if updated is None:
        raise RuntimeError("event stage disappeared after update")
    return _stage_from_row(updated)


def create_event_session(
    connection: sqlite3.Connection,
    event_id: int,
    *,
    session_key: str,
    title: str,
    position: int,
    stage_id: int | None = None,
    summary: str = "",
    status: str = "pending",
    scheduled_start_at: str | None = None,
    scheduled_end_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    _require_event_child_inputs(session_key, title, position)
    _require_choice(status, EVENT_ITEM_STATUSES, "event session status")
    _require_related_event(connection, "event_stages", stage_id, event_id)
    start, end = _schedule(scheduled_start_at, scheduled_end_at)
    cursor = connection.execute(
        """
        INSERT INTO event_sessions (
          event_id, stage_id, session_key, title, summary, status, position,
          scheduled_start_at, scheduled_end_at, metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (event_id, stage_id, session_key, title, summary, status, position, start, end, _json_dump(metadata or {})),
    )
    _touch_event(connection, event_id)
    return int(cursor.lastrowid)


def list_event_sessions(connection: sqlite3.Connection, event_id: int) -> tuple[EventSessionRecord, ...]:
    return tuple(
        _session_from_row(row)
        for row in connection.execute(
            "SELECT * FROM event_sessions WHERE event_id = ? ORDER BY position, id",
            (event_id,),
        )
    )


def set_event_session_status(
    connection: sqlite3.Connection,
    session_id: int,
    status: str,
) -> EventSessionRecord:
    _require_choice(status, EVENT_ITEM_STATUSES, "event session status")
    row = connection.execute(
        "SELECT event_id FROM event_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        raise ValueError("event session does not exist")
    now = utc_now()
    started_at = now if status == "live" else None
    finished_at = now if status in {"completed", "cancelled"} else None
    connection.execute(
        """
        UPDATE event_sessions
        SET status = ?,
            started_at = CASE WHEN ? IS NULL THEN started_at ELSE COALESCE(started_at, ?) END,
            finished_at = CASE WHEN ? IS NULL THEN finished_at ELSE COALESCE(finished_at, ?) END
        WHERE id = ?
        """,
        (status, started_at, started_at, finished_at, finished_at, session_id),
    )
    _touch_event(connection, int(row["event_id"]))
    updated = connection.execute("SELECT * FROM event_sessions WHERE id = ?", (session_id,)).fetchone()
    if updated is None:
        raise RuntimeError("event session disappeared after update")
    return _session_from_row(updated)


def create_event_cast_member(
    connection: sqlite3.Connection,
    event_id: int,
    *,
    member_key: str,
    kind: str,
    display_name: str,
    position: int,
    parent_id: int | None = None,
    short_name: str = "",
    role: str = "",
    status: str = "active",
    engine_version_id: int | None = None,
    profile: str = "",
    avatar_url: str = "",
    accent_color: str = "",
    metadata: dict[str, Any] | None = None,
) -> int:
    _require_event_child_inputs(member_key, display_name, position)
    _require_choice(kind, EVENT_CAST_KINDS, "event cast kind")
    _require_choice(status, EVENT_CAST_STATUSES, "event cast status")
    _require_related_event(connection, "event_cast_members", parent_id, event_id)
    cursor = connection.execute(
        """
        INSERT INTO event_cast_members (
          event_id, parent_id, member_key, kind, display_name, short_name,
          role, status, engine_version_id, profile, avatar_url, accent_color,
          position, metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            parent_id,
            member_key,
            kind,
            display_name,
            short_name,
            role,
            status,
            engine_version_id,
            profile,
            avatar_url,
            accent_color,
            position,
            _json_dump(metadata or {}),
        ),
    )
    _touch_event(connection, event_id)
    return int(cursor.lastrowid)


def list_event_cast(connection: sqlite3.Connection, event_id: int) -> tuple[EventCastMemberRecord, ...]:
    return tuple(
        _cast_from_row(row)
        for row in connection.execute(
            "SELECT * FROM event_cast_members WHERE event_id = ? ORDER BY position, id",
            (event_id,),
        )
    )


def set_event_cast_status(
    connection: sqlite3.Connection,
    cast_member_id: int,
    status: str,
) -> EventCastMemberRecord:
    _require_choice(status, EVENT_CAST_STATUSES, "event cast status")
    row = connection.execute(
        "SELECT event_id FROM event_cast_members WHERE id = ?",
        (cast_member_id,),
    ).fetchone()
    if row is None:
        raise ValueError("event cast member does not exist")
    connection.execute(
        "UPDATE event_cast_members SET status = ? WHERE id = ?",
        (status, cast_member_id),
    )
    _touch_event(connection, int(row["event_id"]))
    updated = connection.execute(
        "SELECT * FROM event_cast_members WHERE id = ?",
        (cast_member_id,),
    ).fetchone()
    if updated is None:
        raise RuntimeError("event cast member disappeared after update")
    return _cast_from_row(updated)


def create_event_contest(
    connection: sqlite3.Connection,
    event_id: int,
    *,
    contest_key: str,
    title: str,
    position: int,
    stage_id: int | None = None,
    session_id: int | None = None,
    summary: str = "",
    status: str = "pending",
    scheduled_start_at: str | None = None,
    scheduled_end_at: str | None = None,
    result: str = "",
    state: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    _require_event_child_inputs(contest_key, title, position)
    _require_choice(status, EVENT_ITEM_STATUSES, "event contest status")
    _require_related_event(connection, "event_stages", stage_id, event_id)
    _require_related_event(connection, "event_sessions", session_id, event_id)
    start, end = _schedule(scheduled_start_at, scheduled_end_at)
    cursor = connection.execute(
        """
        INSERT INTO event_contests (
          event_id, stage_id, session_id, contest_key, title, summary, status,
          position, scheduled_start_at, scheduled_end_at, result, state, metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            stage_id,
            session_id,
            contest_key,
            title,
            summary,
            status,
            position,
            start,
            end,
            result,
            _json_dump(state or {}),
            _json_dump(metadata or {}),
        ),
    )
    _touch_event(connection, event_id)
    return int(cursor.lastrowid)


def add_event_contest_cast(
    connection: sqlite3.Connection,
    contest_id: int,
    cast_member_id: int,
    *,
    position: int,
    side: str = "",
    role: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    row = connection.execute(
        """
        SELECT contest.event_id, member.event_id AS member_event_id
        FROM event_contests contest
        JOIN event_cast_members member ON member.id = ?
        WHERE contest.id = ?
        """,
        (cast_member_id, contest_id),
    ).fetchone()
    if row is None or row["event_id"] != row["member_event_id"]:
        raise ValueError("contest and cast member must belong to the same event")
    connection.execute(
        """
        INSERT INTO event_contest_cast (
          contest_id, cast_member_id, side, role, position, metadata
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (contest_id, cast_member_id, side, role, position, _json_dump(metadata or {})),
    )
    _touch_event(connection, int(row["event_id"]))


def list_event_contests(connection: sqlite3.Connection, event_id: int) -> tuple[EventContestRecord, ...]:
    return tuple(
        _contest_from_row(row)
        for row in connection.execute(
            "SELECT * FROM event_contests WHERE event_id = ? ORDER BY position, id",
            (event_id,),
        )
    )


def update_event_contest_state(
    connection: sqlite3.Connection,
    contest_id: int,
    *,
    status: str | None = None,
    result: str | None = None,
    state: dict[str, Any] | None = None,
) -> EventContestRecord:
    current_row = connection.execute(
        "SELECT * FROM event_contests WHERE id = ?",
        (contest_id,),
    ).fetchone()
    if current_row is None:
        raise ValueError("event contest does not exist")
    current = _contest_from_row(current_row)
    next_status = current.status if status is None else status
    _require_choice(next_status, EVENT_ITEM_STATUSES, "event contest status")
    now = utc_now()
    started_at = now if next_status == "live" else None
    finished_at = now if next_status in {"completed", "cancelled"} else None
    connection.execute(
        """
        UPDATE event_contests
        SET status = ?, result = ?, state = ?,
            started_at = CASE WHEN ? IS NULL THEN started_at ELSE COALESCE(started_at, ?) END,
            finished_at = CASE WHEN ? IS NULL THEN finished_at ELSE COALESCE(finished_at, ?) END
        WHERE id = ?
        """,
        (
            next_status,
            current.result if result is None else result.strip(),
            _json_dump(current.state if state is None else state),
            started_at,
            started_at,
            finished_at,
            finished_at,
            contest_id,
        ),
    )
    _touch_event(connection, current.event_id)
    updated = connection.execute(
        "SELECT * FROM event_contests WHERE id = ?",
        (contest_id,),
    ).fetchone()
    if updated is None:
        raise RuntimeError("event contest disappeared after update")
    return _contest_from_row(updated)


def list_event_contest_cast(connection: sqlite3.Connection, event_id: int) -> tuple[EventContestCastRecord, ...]:
    return tuple(
        _contest_cast_from_row(row)
        for row in connection.execute(
            """
            SELECT link.*
            FROM event_contest_cast link
            JOIN event_contests contest ON contest.id = link.contest_id
            WHERE contest.event_id = ?
            ORDER BY contest.position, link.position
            """,
            (event_id,),
        )
    )


def create_event_update(
    connection: sqlite3.Connection,
    event_id: int,
    *,
    kind: str,
    title: str,
    body: str = "",
    pinned: bool = False,
    occurred_at: str | None = None,
    published: bool = True,
) -> int:
    _require_choice(kind, EVENT_UPDATE_KINDS, "event update kind")
    if not title.strip():
        raise ValueError("event update title is required")
    now = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO event_updates (
          event_id, kind, title, body, pinned, occurred_at, published_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            kind,
            title.strip(),
            body.strip(),
            int(pinned),
            _optional_timestamp(occurred_at) or now,
            now if published else None,
            now,
        ),
    )
    _touch_event(connection, event_id)
    return int(cursor.lastrowid)


def list_event_updates(
    connection: sqlite3.Connection,
    event_id: int,
    *,
    public_only: bool = False,
) -> tuple[EventUpdateRecord, ...]:
    published = "AND published_at IS NOT NULL" if public_only else ""
    return tuple(
        _update_from_row(row)
        for row in connection.execute(
            f"""
            SELECT * FROM event_updates
            WHERE event_id = ? {published}
            ORDER BY pinned DESC, occurred_at DESC, id DESC
            """,
            (event_id,),
        )
    )


def create_event_award(
    connection: sqlite3.Connection,
    event_id: int,
    *,
    award_key: str,
    title: str,
    position: int,
    description: str = "",
    recipient_cast_id: int | None = None,
    recipient_label: str = "",
    awarded_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    _require_event_child_inputs(award_key, title, position)
    _require_related_event(connection, "event_cast_members", recipient_cast_id, event_id)
    cursor = connection.execute(
        """
        INSERT INTO event_awards (
          event_id, award_key, title, description, recipient_cast_id,
          recipient_label, position, awarded_at, metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            award_key,
            title,
            description,
            recipient_cast_id,
            recipient_label,
            position,
            _optional_timestamp(awarded_at),
            _json_dump(metadata or {}),
        ),
    )
    _touch_event(connection, event_id)
    return int(cursor.lastrowid)


def list_event_awards(connection: sqlite3.Connection, event_id: int) -> tuple[EventAwardRecord, ...]:
    return tuple(
        _award_from_row(row)
        for row in connection.execute(
            "SELECT * FROM event_awards WHERE event_id = ? ORDER BY position, id",
            (event_id,),
        )
    )


def get_event_chat_settings(
    connection: sqlite3.Connection,
    event_id: int,
    *,
    defaults: Any,
) -> EventChatSettingsRecord:
    row = connection.execute(
        "SELECT * FROM event_chat_settings WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    return EventChatSettingsRecord(
        enabled=defaults.enabled if row is None or row["enabled"] is None else bool(row["enabled"]),
        slowmode_seconds=(
            defaults.slowmode_seconds
            if row is None or row["slowmode_seconds"] is None
            else int(row["slowmode_seconds"])
        ),
        max_message_length=(
            defaults.max_message_length
            if row is None or row["max_message_length"] is None
            else int(row["max_message_length"])
        ),
        allow_anonymous_names=(
            defaults.allow_anonymous_names
            if row is None or row["allow_anonymous_names"] is None
            else bool(row["allow_anonymous_names"])
        ),
        retention_days=(
            defaults.retention_days
            if row is None or row["retention_days"] is None
            else int(row["retention_days"])
        ),
    )


def set_event_chat_settings(
    connection: sqlite3.Connection,
    event_id: int,
    *,
    enabled: bool | None = None,
    slowmode_seconds: int | None = None,
    max_message_length: int | None = None,
    allow_anonymous_names: bool | None = None,
    retention_days: int | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO event_chat_settings (
          event_id, enabled, slowmode_seconds, max_message_length,
          allow_anonymous_names, retention_days
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO UPDATE SET
          enabled = excluded.enabled,
          slowmode_seconds = excluded.slowmode_seconds,
          max_message_length = excluded.max_message_length,
          allow_anonymous_names = excluded.allow_anonymous_names,
          retention_days = excluded.retention_days
        """,
        (
            event_id,
            None if enabled is None else int(enabled),
            slowmode_seconds,
            max_message_length,
            None if allow_anonymous_names is None else int(allow_anonymous_names),
            retention_days,
        ),
    )
    _touch_event(connection, event_id)


def event_resource_counts(connection: sqlite3.Connection, event_id: int) -> dict[str, int]:
    tables = {
        "stages": "event_stages",
        "sessions": "event_sessions",
        "cast": "event_cast_members",
        "contests": "event_contests",
        "updates": "event_updates",
        "awards": "event_awards",
    }
    result: dict[str, int] = {}
    for key, table in tables.items():
        row = connection.execute(
            f"SELECT COUNT(*) AS count FROM {table} WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        result[key] = int(row["count"]) if row is not None else 0
    return result


def event_public_stats(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM events
        WHERE published_at IS NOT NULL AND status != 'draft'
        GROUP BY status
        """
    ).fetchall()
    counts = {str(row["status"]): int(row["count"]) for row in rows}
    active = sum(counts.get(status, 0) for status in ("live", "intermission"))
    upcoming = sum(counts.get(status, 0) for status in ("announced", "scheduled", "postponed"))
    completed = counts.get("completed", 0)
    return {
        "total": sum(counts.values()),
        "live": active,
        "upcoming": upcoming,
        "completed": completed,
    }


def _touch_event(connection: sqlite3.Connection, event_id: int) -> None:
    cursor = connection.execute(
        """
        UPDATE events
        SET revision = revision + 1, updated_at = ?
        WHERE id = ?
        """,
        (utc_now(), event_id),
    )
    if cursor.rowcount == 0:
        raise ValueError("event does not exist")


def _required_event(connection: sqlite3.Connection, event_id: int) -> EventRecord:
    event = get_event(connection, event_id)
    if event is None:
        raise ValueError("event does not exist")
    return event


def _require_related_event(
    connection: sqlite3.Connection,
    table: str,
    record_id: int | None,
    event_id: int,
) -> None:
    if record_id is None:
        return
    row = connection.execute(
        f"SELECT event_id FROM {table} WHERE id = ?",
        (record_id,),
    ).fetchone()
    if row is None or int(row["event_id"]) != event_id:
        raise ValueError("related record must belong to the same event")


def _require_event_child_inputs(key: str, title: str, position: int) -> None:
    if not key.strip():
        raise ValueError("event item key is required")
    if not title.strip():
        raise ValueError("event item title is required")
    if position < 0:
        raise ValueError("event item position cannot be negative")


def _require_choice(value: str, choices: Iterable[str], label: str) -> None:
    if value not in choices:
        raise ValueError(f"unsupported {label}: {value}")


def _schedule(start: str | None, end: str | None) -> tuple[str | None, str | None]:
    normalized_start = _optional_timestamp(start)
    normalized_end = _optional_timestamp(end)
    _require_schedule_order(normalized_start, normalized_end)
    return normalized_start, normalized_end


def _require_schedule_order(start: str | None, end: str | None) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError("scheduled end must be after scheduled start")


def _optional_timestamp(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be a valid ISO date and time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC).isoformat(timespec="seconds")


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _json_load(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


def _event_from_row(row) -> EventRecord:
    return EventRecord(
        id=row["id"],
        slug=row["slug"],
        handler_key=row["handler_key"],
        handler_version=row["handler_version"],
        title=row["title"],
        subtitle=row["subtitle"],
        summary=row["summary"],
        description=row["description"],
        rules=row["rules"],
        status=row["status"],
        featured=bool(row["featured"]),
        published_at=row["published_at"],
        scheduled_start_at=row["scheduled_start_at"],
        scheduled_end_at=row["scheduled_end_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        theme=_json_load(row["theme"]),
        config=_json_load(row["config"]),
        state=_json_load(row["state"]),
        revision=row["revision"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _stage_from_row(row) -> EventStageRecord:
    return EventStageRecord(
        id=row["id"], event_id=row["event_id"], stage_key=row["stage_key"],
        title=row["title"], summary=row["summary"], status=row["status"],
        position=row["position"], scheduled_start_at=row["scheduled_start_at"],
        scheduled_end_at=row["scheduled_end_at"], started_at=row["started_at"],
        finished_at=row["finished_at"], metadata=_json_load(row["metadata"]),
    )


def _session_from_row(row) -> EventSessionRecord:
    return EventSessionRecord(
        id=row["id"], event_id=row["event_id"], stage_id=row["stage_id"],
        session_key=row["session_key"], title=row["title"], summary=row["summary"],
        status=row["status"], position=row["position"],
        scheduled_start_at=row["scheduled_start_at"], scheduled_end_at=row["scheduled_end_at"],
        started_at=row["started_at"], finished_at=row["finished_at"],
        metadata=_json_load(row["metadata"]),
    )


def _cast_from_row(row) -> EventCastMemberRecord:
    return EventCastMemberRecord(
        id=row["id"], event_id=row["event_id"], parent_id=row["parent_id"],
        member_key=row["member_key"], kind=row["kind"], display_name=row["display_name"],
        short_name=row["short_name"], role=row["role"], status=row["status"],
        engine_version_id=row["engine_version_id"], profile=row["profile"],
        avatar_url=row["avatar_url"], accent_color=row["accent_color"],
        position=row["position"], metadata=_json_load(row["metadata"]),
    )


def _contest_from_row(row) -> EventContestRecord:
    return EventContestRecord(
        id=row["id"], event_id=row["event_id"], stage_id=row["stage_id"],
        session_id=row["session_id"], contest_key=row["contest_key"], title=row["title"],
        summary=row["summary"], status=row["status"], position=row["position"],
        scheduled_start_at=row["scheduled_start_at"], scheduled_end_at=row["scheduled_end_at"],
        started_at=row["started_at"], finished_at=row["finished_at"], result=row["result"],
        state=_json_load(row["state"]), metadata=_json_load(row["metadata"]),
    )


def _contest_cast_from_row(row) -> EventContestCastRecord:
    return EventContestCastRecord(
        contest_id=row["contest_id"], cast_member_id=row["cast_member_id"],
        side=row["side"], role=row["role"], position=row["position"],
        metadata=_json_load(row["metadata"]),
    )


def _update_from_row(row) -> EventUpdateRecord:
    return EventUpdateRecord(
        id=row["id"], event_id=row["event_id"], kind=row["kind"], title=row["title"],
        body=row["body"], pinned=bool(row["pinned"]), occurred_at=row["occurred_at"],
        published_at=row["published_at"], created_at=row["created_at"],
    )


def _award_from_row(row) -> EventAwardRecord:
    return EventAwardRecord(
        id=row["id"], event_id=row["event_id"], award_key=row["award_key"],
        title=row["title"], description=row["description"],
        recipient_cast_id=row["recipient_cast_id"], recipient_label=row["recipient_label"],
        position=row["position"], awarded_at=row["awarded_at"],
        metadata=_json_load(row["metadata"]),
    )
