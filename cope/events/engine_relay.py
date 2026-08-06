from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from cope.core.models import (
    AdjudicationConfig,
    ColorSlot,
    EngineRelayAssignment,
    EngineRelayMember,
    EngineRelayTeam,
    MoveNodesTimeControl,
    RoundRobinFormatOptions,
    TournamentConfig,
    TournamentFormat,
)
from cope.db import (
    create_event,
    create_event_cast_member,
    create_tournament,
    delete_tournament,
    get_common_benchmark_reference,
    get_engine,
    get_event,
    get_event_by_slug,
    get_game,
    get_tournament,
    list_engines,
    list_event_cast,
    list_games,
    list_moves,
    list_opening_suites,
    schedule_tournament,
    set_event_published,
    set_event_status,
    unschedule_tournament,
)
from cope.db.events import EventCastMemberRecord, EventRecord, utc_now
from cope.db.repo import GameRecord, MoveRecord, TournamentRecord
from cope.runner.scheduler import materialize_tournament_schedule, start_tournament

from .registry import EventModule, register_event_module


MODULE_KEY = "engine-relay"
MODULE_VERSION = 1
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True, slots=True)
class EngineRelayFixtureRecord:
    id: int
    event_id: int
    tournament_id: int
    team_a_id: int
    team_b_id: int
    anchor_a_engine_id: int
    anchor_b_engine_id: int
    title: str
    position: int
    created_at: str


class RelayTeamPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    short_name: str = Field(default="", max_length=20)
    primary_color: str = Field(default="#315fcc")
    secondary_color: str = Field(default="#8fb3ff")
    profile: str = Field(default="", max_length=1000)
    motto: str = Field(default="", max_length=180)

    @field_validator("name", "short_name", "profile", "motto")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("primary_color", "secondary_color")
    @classmethod
    def valid_color(cls, value: str) -> str:
        if not _COLOR.fullmatch(value):
            raise ValueError("team colours must be six-digit hex colours")
        return value.lower()


class RelayMemberPayload(BaseModel):
    engine_id: int = Field(gt=0)
    relay_moves: int = Field(default=4, gt=0, le=1000)
    nodes: int = Field(default=100000, gt=0, le=10_000_000_000)
    position: int = Field(default=0, ge=0, le=1000)
    label: str = Field(default="", max_length=80)

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        return value.strip()


class RelayFixturePayload(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    team_a_id: int = Field(gt=0)
    team_b_id: int = Field(gt=0)
    cycles: int = Field(default=1, gt=0, le=1000)
    opening_suite_id: int | None = Field(default=None, gt=0)
    concurrency: int = Field(default=1, gt=0, le=256)
    engine_threads: int = Field(default=1, gt=0, le=1024)
    engine_hash_mb: int = Field(default=256, gt=0, le=1_048_576)
    lag_compensation_ms: int = Field(default=50, ge=0, le=60_000)
    adjudication: AdjudicationConfig = Field(default_factory=AdjudicationConfig)
    uci_options: dict[str, str | int | bool] = Field(default_factory=dict)
    scheduled_start_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        return value.strip()

    @field_validator("uci_options")
    @classmethod
    def valid_uci_options(
        cls,
        value: dict[str, str | int | bool],
    ) -> dict[str, str | int | bool]:
        for name in value:
            if not name.strip():
                raise ValueError("UCI option names cannot be blank")
            if name.strip().lower() in {"threads", "hash"}:
                raise ValueError("Threads and Hash use the fixture resource controls")
        return value

    @field_validator("scheduled_start_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("scheduled start time must include a timezone")
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def distinct_teams(self) -> RelayFixturePayload:
        if self.team_a_id == self.team_b_id:
            raise ValueError("a relay fixture requires two different teams")
        return self


class RelaySchedulePayload(BaseModel):
    scheduled_start_at: datetime

    @field_validator("scheduled_start_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled start time must include a timezone")
        return value.astimezone(UTC)


class RelayVisibilityPayload(BaseModel):
    status: str = Field(pattern=r"^(draft|announced|scheduled|live|intermission|postponed|completed|cancelled)$")
    published: bool


def _fixture_from_row(row: Any) -> EngineRelayFixtureRecord:
    return EngineRelayFixtureRecord(
        id=int(row["id"]),
        event_id=int(row["event_id"]),
        tournament_id=int(row["tournament_id"]),
        team_a_id=int(row["team_a_id"]),
        team_b_id=int(row["team_b_id"]),
        anchor_a_engine_id=int(row["anchor_a_engine_id"]),
        anchor_b_engine_id=int(row["anchor_b_engine_id"]),
        title=str(row["title"]),
        position=int(row["position"]),
        created_at=str(row["created_at"]),
    )


def _get_fixture_for_tournament(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> EngineRelayFixtureRecord | None:
    row = connection.execute(
        "SELECT * FROM engine_relay_fixtures WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone()
    return None if row is None else _fixture_from_row(row)


def _list_fixtures(
    connection: sqlite3.Connection,
    event_id: int,
) -> tuple[EngineRelayFixtureRecord, ...]:
    return tuple(
        _fixture_from_row(row)
        for row in connection.execute(
            "SELECT * FROM engine_relay_fixtures WHERE event_id = ? ORDER BY position, id",
            (event_id,),
        )
    )


def _cast_by_id(connection: sqlite3.Connection, event_id: int) -> dict[int, EventCastMemberRecord]:
    return {member.id: member for member in list_event_cast(connection, event_id)}


def _teams(connection: sqlite3.Connection, event_id: int) -> tuple[EventCastMemberRecord, ...]:
    return tuple(
        member
        for member in list_event_cast(connection, event_id)
        if member.kind == "team" and member.parent_id is None
    )


def _team_members(
    connection: sqlite3.Connection,
    event_id: int,
    team_id: int,
) -> tuple[EventCastMemberRecord, ...]:
    members = [
        member
        for member in list_event_cast(connection, event_id)
        if member.kind == "engine" and member.parent_id == team_id
    ]
    members.sort(key=lambda item: (int(item.metadata.get("relay_order", item.position)), item.id))
    return tuple(members)


def _member_settings(member: EventCastMemberRecord) -> tuple[int, int, int, str]:
    return (
        max(1, int(member.metadata.get("relay_moves", 4))),
        max(1, int(member.metadata.get("nodes", 100000))),
        max(0, int(member.metadata.get("relay_order", member.position))),
        str(member.metadata.get("label", "")),
    )


def _team_assignment(
    connection: sqlite3.Connection,
    event_id: int,
    team: EventCastMemberRecord,
) -> EngineRelayTeam:
    members = _team_members(connection, event_id, team.id)
    return EngineRelayTeam(
        team_id=team.id,
        name=team.display_name,
        short_name=team.short_name,
        primary_color=team.accent_color,
        secondary_color=str(team.metadata.get("secondary_color", "")),
        members=tuple(
            EngineRelayMember(
                engine_id=int(member.engine_version_id or 0),
                relay_moves=_member_settings(member)[0],
                nodes=_member_settings(member)[1],
                position=_member_settings(member)[2],
            )
            for member in members
            if member.engine_version_id is not None
        ),
    )


def relay_assignment_for_game(
    connection: sqlite3.Connection,
    game: GameRecord,
) -> EngineRelayAssignment | None:
    fixture = _get_fixture_for_tournament(connection, game.tournament_id)
    if fixture is None:
        return None
    cast = _cast_by_id(connection, fixture.event_id)
    team_a = cast.get(fixture.team_a_id)
    team_b = cast.get(fixture.team_b_id)
    if team_a is None or team_b is None:
        raise RuntimeError("relay fixture team is missing")
    if (
        game.white_engine_id == fixture.anchor_a_engine_id
        and game.black_engine_id == fixture.anchor_b_engine_id
    ):
        white_team, black_team = team_a, team_b
    elif (
        game.white_engine_id == fixture.anchor_b_engine_id
        and game.black_engine_id == fixture.anchor_a_engine_id
    ):
        white_team, black_team = team_b, team_a
    else:
        raise RuntimeError("relay fixture game anchors do not match its teams")
    return EngineRelayAssignment(
        event_id=fixture.event_id,
        fixture_id=fixture.id,
        teams={
            ColorSlot.WHITE: _team_assignment(connection, fixture.event_id, white_team),
            ColorSlot.BLACK: _team_assignment(connection, fixture.event_id, black_team),
        },
    )


def relay_engine_ids_for_game(
    connection: sqlite3.Connection,
    game: GameRecord,
) -> tuple[int, ...]:
    relay = relay_assignment_for_game(connection, game)
    if relay is None:
        return ()
    return tuple(
        member.engine_id
        for team in relay.teams.values()
        for member in team.members
    )


def relay_engine_count_for_tournament(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> int:
    fixture = _get_fixture_for_tournament(connection, tournament_id)
    if fixture is None:
        return 2
    return sum(
        len(_team_members(connection, fixture.event_id, team_id))
        for team_id in (fixture.team_a_id, fixture.team_b_id)
    )


def relay_engine_colors(assignment: Any) -> dict[int, str]:
    relay = assignment.assignment.engine_relay
    if relay is None:
        return {
            engine_id: color.name.lower()
            for color, engine_id in assignment.assignment.slots.items()
        }
    return {
        member.engine_id: color.name.lower()
        for color, team in relay.teams.items()
        for member in team.members
    }


def relay_member_at(team: EngineRelayTeam, moves_played: int) -> EngineRelayMember:
    cycle = sum(member.relay_moves for member in team.members)
    offset = moves_played % cycle
    for member in team.members:
        if offset < member.relay_moves:
            return member
        offset -= member.relay_moves
    return team.members[0]


def _moves_played(moves: tuple[MoveRecord, ...], side: ColorSlot) -> int:
    white = side == ColorSlot.WHITE
    return sum(
        1
        for move in moves
        if not move.is_book and ((move.ply % 2 == 1) == white)
    )


def _team_payload(
    connection: sqlite3.Connection,
    event_id: int,
    team: EventCastMemberRecord,
) -> dict[str, Any]:
    roster = []
    for member in _team_members(connection, event_id, team.id):
        relay_moves, nodes, position, label = _member_settings(member)
        engine = get_engine(connection, int(member.engine_version_id or 0))
        roster.append(
            {
                "id": member.id,
                "engine_id": member.engine_version_id,
                "name": engine.name if engine is not None else member.display_name,
                "version": engine.version if engine is not None else "",
                "display_name": member.display_name,
                "label": label,
                "relay_moves": relay_moves,
                "nodes": nodes,
                "position": position,
            }
        )
    return {
        "id": team.id,
        "name": team.display_name,
        "short_name": team.short_name,
        "profile": team.profile,
        "motto": str(team.metadata.get("motto", "")),
        "primary_color": team.accent_color or "#315fcc",
        "secondary_color": str(team.metadata.get("secondary_color", "#8fb3ff")),
        "roster": roster,
        "locked": _team_locked(connection, team.id),
    }


def _side_team_ids(
    fixture: EngineRelayFixtureRecord,
    game: GameRecord,
) -> tuple[int, int]:
    if game.white_engine_id == fixture.anchor_a_engine_id:
        return fixture.team_a_id, fixture.team_b_id
    return fixture.team_b_id, fixture.team_a_id


def _fixture_payload(
    connection: sqlite3.Connection,
    fixture: EngineRelayFixtureRecord,
) -> dict[str, Any]:
    tournament = get_tournament(connection, fixture.tournament_id)
    games = () if tournament is None else list_games(connection, tournament.id)
    cast = _cast_by_id(connection, fixture.event_id)
    team_assignments = {
        team_id: _team_assignment(connection, fixture.event_id, cast[team_id])
        for team_id in (fixture.team_a_id, fixture.team_b_id)
        if team_id in cast
    }
    game_payloads = []
    for game in games:
        white_team_id, black_team_id = _side_team_ids(fixture, game)
        moves = list_moves(connection, game.id)
        white_team = team_assignments.get(white_team_id)
        black_team = team_assignments.get(black_team_id)
        white_active = None if white_team is None else relay_member_at(
            white_team,
            _moves_played(moves, ColorSlot.WHITE),
        ).engine_id
        black_active = None if black_team is None else relay_member_at(
            black_team,
            _moves_played(moves, ColorSlot.BLACK),
        ).engine_id
        game_payloads.append(
            {
                **asdict(game),
                "white_team_id": white_team_id,
                "black_team_id": black_team_id,
                "white_active_engine_id": white_active,
                "black_active_engine_id": black_active,
            }
        )
    return {
        **asdict(fixture),
        "tournament": None if tournament is None else asdict(tournament),
        "games": game_payloads,
        "team_a_name": cast.get(fixture.team_a_id).display_name if fixture.team_a_id in cast else "Team A",
        "team_b_name": cast.get(fixture.team_b_id).display_name if fixture.team_b_id in cast else "Team B",
    }


def _payload(connection: sqlite3.Connection, event: EventRecord, *, admin: bool) -> dict[str, Any]:
    teams = [_team_payload(connection, event.id, team) for team in _teams(connection, event.id)]
    fixtures = [_fixture_payload(connection, fixture) for fixture in _list_fixtures(connection, event.id)]
    payload: dict[str, Any] = {
        "teams": teams,
        "fixtures": fixtures,
        "format": "engine-relay",
    }
    if admin:
        payload["engine_options"] = [
            {
                "id": engine.engine_id,
                "name": engine.name,
                "version": engine.version,
                "author": engine.author,
            }
            for engine in list_engines(connection, active_only=True)
        ]
        payload["opening_suites"] = [
            {"id": suite.id, "name": suite.name, "description": suite.description}
            for suite in list_opening_suites(connection)
        ]
    return payload


def _public_payload(connection: sqlite3.Connection, event: EventRecord) -> dict[str, Any]:
    return _payload(connection, event, admin=False)


def _admin_payload(connection: sqlite3.Connection, event: EventRecord) -> dict[str, Any]:
    return _payload(connection, event, admin=True)


def _required_event(connection: sqlite3.Connection, event_id: int) -> EventRecord:
    event = get_event(connection, event_id)
    if event is None or event.handler_key != MODULE_KEY or event.handler_version != MODULE_VERSION:
        raise HTTPException(status_code=404, detail="Engine relay event not found.")
    return event


def _required_team(
    connection: sqlite3.Connection,
    event_id: int,
    team_id: int,
) -> EventCastMemberRecord:
    team = next((item for item in _teams(connection, event_id) if item.id == team_id), None)
    if team is None:
        raise HTTPException(status_code=404, detail="Relay team not found.")
    return team


def _required_member(
    connection: sqlite3.Connection,
    event_id: int,
    team_id: int,
    member_id: int,
) -> EventCastMemberRecord:
    member = next(
        (item for item in _team_members(connection, event_id, team_id) if item.id == member_id),
        None,
    )
    if member is None:
        raise HTTPException(status_code=404, detail="Relay roster member not found.")
    return member


def _required_fixture(
    connection: sqlite3.Connection,
    event_id: int,
    fixture_id: int,
) -> EngineRelayFixtureRecord:
    fixture = next((item for item in _list_fixtures(connection, event_id) if item.id == fixture_id), None)
    if fixture is None:
        raise HTTPException(status_code=404, detail="Relay fixture not found.")
    return fixture


def _team_locked(connection: sqlite3.Connection, team_id: int) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM engine_relay_fixtures fixture
        JOIN tournaments tournament ON tournament.id = fixture.tournament_id
        WHERE (fixture.team_a_id = ? OR fixture.team_b_id = ?)
          AND tournament.status IN ('running', 'paused', 'finished', 'aborted')
        LIMIT 1
        """,
        (team_id, team_id),
    ).fetchone()
    return row is not None


def _validate_member_compatibility(
    connection: sqlite3.Connection,
    event_id: int,
    team_id: int,
    engine_id: int,
) -> None:
    fixtures = tuple(
        _fixture_from_row(row)
        for row in connection.execute(
            """
            SELECT * FROM engine_relay_fixtures
            WHERE event_id = ? AND (team_a_id = ? OR team_b_id = ?)
            """,
            (event_id, team_id, team_id),
        )
    )
    for fixture in fixtures:
        other_team_id = fixture.team_b_id if fixture.team_a_id == team_id else fixture.team_a_id
        engine_ids = {
            engine_id,
            *(
                int(member.engine_version_id)
                for member in _team_members(connection, event_id, team_id)
                if member.engine_version_id is not None
            ),
            *(
                int(member.engine_version_id)
                for member in _team_members(connection, event_id, other_team_id)
                if member.engine_version_id is not None
            ),
        }
        engines = tuple(get_engine(connection, value) for value in engine_ids)
        if any(item is None for item in engines) or get_common_benchmark_reference(
            connection,
            tuple(item for item in engines if item is not None),
        ) is None:
            raise HTTPException(
                status_code=409,
                detail=f"This engine does not share a benchmark hardware reference with fixture {fixture.title}.",
            )


def _touch_event(connection: sqlite3.Connection, event_id: int) -> None:
    connection.execute(
        "UPDATE events SET revision = revision + 1, updated_at = ? WHERE id = ?",
        (utc_now(), event_id),
    )


def _publish_change(request: Request, event_id: int) -> None:
    request.app.state.stream_hub.publish(
        f"event.{event_id}",
        "event.changed",
        {"event_id": event_id},
        source="web",
    )
    request.app.state.stream_hub.publish(
        "admin",
        "admin.changed",
        {"event_id": event_id},
        source="web",
    )


def _json(payload: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(jsonable_encoder(payload), status_code=status_code)


def _register_api(app: FastAPI) -> None:
    from cope.web import app as web_app

    @app.get("/api/events/{slug}/engine-relay")
    def public_engine_relay(
        slug: str,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = get_event_by_slug(connection, slug)
        if (
            event is None
            or event.handler_key != MODULE_KEY
            or event.handler_version != MODULE_VERSION
            or event.published_at is None
            or event.status == "draft"
        ):
            raise HTTPException(status_code=404, detail="Engine relay event not found.")
        return _json(_public_payload(connection, event))

    @app.get("/api/admin/events/{event_id}/engine-relay")
    def admin_engine_relay(
        event_id: int,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = _required_event(connection, event_id)
        return _json(_admin_payload(connection, event))

    @app.post("/api/admin/events/{event_id}/engine-relay/teams")
    def create_team(
        event_id: int,
        payload: RelayTeamPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        row = connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS position FROM event_cast_members WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        position = int(row["position"])
        team_id = create_event_cast_member(
            connection,
            event_id,
            member_key=f"team-{position + 1}",
            kind="team",
            display_name=payload.name,
            short_name=payload.short_name,
            profile=payload.profile,
            accent_color=payload.primary_color,
            position=position,
            metadata={"secondary_color": payload.secondary_color, "motto": payload.motto},
        )
        connection.commit()
        _publish_change(request, event_id)
        return _json({"id": team_id, "message": "Relay team created."}, 201)

    @app.put("/api/admin/events/{event_id}/engine-relay/teams/{team_id}")
    def update_team(
        event_id: int,
        team_id: int,
        payload: RelayTeamPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        _required_team(connection, event_id, team_id)
        connection.execute(
            """
            UPDATE event_cast_members
            SET display_name = ?, short_name = ?, profile = ?, accent_color = ?, metadata = ?
            WHERE id = ? AND event_id = ?
            """,
            (
                payload.name,
                payload.short_name,
                payload.profile,
                payload.primary_color,
                json.dumps({"secondary_color": payload.secondary_color, "motto": payload.motto}, separators=(",", ":")),
                team_id,
                event_id,
            ),
        )
        _touch_event(connection, event_id)
        connection.commit()
        _publish_change(request, event_id)
        return _json({"message": "Relay team updated."})

    @app.delete("/api/admin/events/{event_id}/engine-relay/teams/{team_id}")
    def delete_team(
        event_id: int,
        team_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        _required_team(connection, event_id, team_id)
        fixture = connection.execute(
            "SELECT 1 FROM engine_relay_fixtures WHERE team_a_id = ? OR team_b_id = ? LIMIT 1",
            (team_id, team_id),
        ).fetchone()
        if fixture is not None:
            raise HTTPException(status_code=409, detail="Remove this team from every fixture before deleting it.")
        connection.execute("DELETE FROM event_cast_members WHERE parent_id = ?", (team_id,))
        connection.execute("DELETE FROM event_cast_members WHERE id = ?", (team_id,))
        _touch_event(connection, event_id)
        connection.commit()
        _publish_change(request, event_id)
        return _json({"message": "Relay team deleted."})

    @app.post("/api/admin/events/{event_id}/engine-relay/teams/{team_id}/members")
    def create_member(
        event_id: int,
        team_id: int,
        payload: RelayMemberPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        _required_team(connection, event_id, team_id)
        if _team_locked(connection, team_id):
            raise HTTPException(status_code=409, detail="This roster is locked because one of its fixtures has started.")
        engine = get_engine(connection, payload.engine_id)
        if engine is None:
            raise HTTPException(status_code=422, detail="Select an available engine version.")
        duplicate = next(
            (
                item
                for item in list_event_cast(connection, event_id)
                if item.kind == "engine" and item.engine_version_id == payload.engine_id
            ),
            None,
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="An engine version can only belong to one relay team.")
        _validate_member_compatibility(connection, event_id, team_id, payload.engine_id)
        row = connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS position FROM event_cast_members WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        cast_position = int(row["position"])
        member_id = create_event_cast_member(
            connection,
            event_id,
            member_key=f"team-{team_id}-engine-{payload.engine_id}",
            kind="engine",
            display_name=" ".join((engine.name, engine.version)),
            position=cast_position,
            parent_id=team_id,
            role="relay engine",
            engine_version_id=payload.engine_id,
            metadata={
                "relay_moves": payload.relay_moves,
                "nodes": payload.nodes,
                "relay_order": payload.position,
                "label": payload.label,
            },
        )
        connection.commit()
        _publish_change(request, event_id)
        return _json({"id": member_id, "message": "Engine added to the relay roster."}, 201)

    @app.put("/api/admin/events/{event_id}/engine-relay/teams/{team_id}/members/{member_id}")
    def update_member(
        event_id: int,
        team_id: int,
        member_id: int,
        payload: RelayMemberPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        current = _required_member(connection, event_id, team_id, member_id)
        if _team_locked(connection, team_id):
            raise HTTPException(status_code=409, detail="This roster is locked because one of its fixtures has started.")
        if payload.engine_id != current.engine_version_id:
            raise HTTPException(status_code=409, detail="Remove and re-add a roster slot to change its engine version.")
        connection.execute(
            "UPDATE event_cast_members SET metadata = ? WHERE id = ?",
            (
                json.dumps(
                    {
                        "relay_moves": payload.relay_moves,
                        "nodes": payload.nodes,
                        "relay_order": payload.position,
                        "label": payload.label,
                    },
                    separators=(",", ":"),
                ),
                member_id,
            ),
        )
        _touch_event(connection, event_id)
        connection.commit()
        _publish_change(request, event_id)
        return _json({"message": "Relay engine settings updated."})

    @app.delete("/api/admin/events/{event_id}/engine-relay/teams/{team_id}/members/{member_id}")
    def delete_member(
        event_id: int,
        team_id: int,
        member_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        member = _required_member(connection, event_id, team_id, member_id)
        if _team_locked(connection, team_id):
            raise HTTPException(status_code=409, detail="This roster is locked because one of its fixtures has started.")
        anchor = connection.execute(
            """
            SELECT 1 FROM engine_relay_fixtures
            WHERE (team_a_id = ? AND anchor_a_engine_id = ?)
               OR (team_b_id = ? AND anchor_b_engine_id = ?)
            LIMIT 1
            """,
            (team_id, member.engine_version_id, team_id, member.engine_version_id),
        ).fetchone()
        if anchor is not None:
            raise HTTPException(status_code=409, detail="This engine anchors an existing fixture and cannot be removed.")
        connection.execute("DELETE FROM event_cast_members WHERE id = ?", (member_id,))
        _touch_event(connection, event_id)
        connection.commit()
        _publish_change(request, event_id)
        return _json({"message": "Engine removed from the relay roster."})

    @app.post("/api/admin/events/{event_id}/engine-relay/fixtures")
    def create_fixture(
        event_id: int,
        payload: RelayFixturePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        team_a = _required_team(connection, event_id, payload.team_a_id)
        team_b = _required_team(connection, event_id, payload.team_b_id)
        if payload.opening_suite_id is not None and not any(
            suite.id == payload.opening_suite_id
            for suite in list_opening_suites(connection)
        ):
            raise HTTPException(status_code=422, detail="Select an available opening suite.")
        if not _team_members(connection, event_id, team_a.id) or not _team_members(
            connection,
            event_id,
            team_b.id,
        ):
            raise HTTPException(status_code=422, detail="Both teams need at least one engine before creating a fixture.")
        relay_a = _team_assignment(connection, event_id, team_a)
        relay_b = _team_assignment(connection, event_id, team_b)
        engine_ids = [member.engine_id for team in (relay_a, relay_b) for member in team.members]
        if len(set(engine_ids)) != len(engine_ids):
            raise HTTPException(status_code=409, detail="An engine version cannot play for both relay teams.")
        engines = tuple(get_engine(connection, engine_id) for engine_id in engine_ids)
        if any(engine is None for engine in engines):
            raise HTTPException(status_code=422, detail="Every relay engine must still be available.")
        if get_common_benchmark_reference(connection, tuple(engine for engine in engines if engine is not None)) is None:
            raise HTTPException(status_code=409, detail="The full relay roster does not share a benchmark hardware reference yet.")
        anchor_a = relay_a.members[0].engine_id
        anchor_b = relay_b.members[0].engine_id
        config = TournamentConfig(
            format=TournamentFormat.ROUND_ROBIN,
            format_options=RoundRobinFormatOptions(cycles=payload.cycles),
            participants=[anchor_a, anchor_b],
            time_control=MoveNodesTimeControl(nodes=max(member.nodes for team in (relay_a, relay_b) for member in team.members)),
            concurrency=payload.concurrency,
            opening_suite_id=payload.opening_suite_id,
            adjudication=payload.adjudication,
            rated=False,
            lag_compensation_ms=payload.lag_compensation_ms,
            engine_threads=payload.engine_threads,
            engine_hash_mb=payload.engine_hash_mb,
            uci_options=payload.uci_options,
        )
        tournament_id = create_tournament(connection, payload.title, config)
        row = connection.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS position FROM engine_relay_fixtures WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        position = int(row["position"])
        cursor = connection.execute(
            """
            INSERT INTO engine_relay_fixtures (
              event_id, tournament_id, team_a_id, team_b_id,
              anchor_a_engine_id, anchor_b_engine_id, title, position, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                tournament_id,
                payload.team_a_id,
                payload.team_b_id,
                anchor_a,
                anchor_b,
                payload.title,
                position,
                utc_now(),
            ),
        )
        fixture_id = int(cursor.lastrowid)
        if payload.scheduled_start_at is not None:
            tournament = get_tournament(connection, tournament_id)
            if tournament is None:
                raise RuntimeError("relay tournament disappeared during creation")
            materialize_tournament_schedule(connection, tournament)
            schedule_tournament(
                connection,
                tournament_id,
                payload.scheduled_start_at.isoformat(timespec="seconds"),
            )
        _touch_event(connection, event_id)
        connection.commit()
        _publish_change(request, event_id)
        return _json(
            {
                "id": fixture_id,
                "tournament_id": tournament_id,
                "message": "Relay fixture created as an unrated tournament.",
            },
            201,
        )

    @app.delete("/api/admin/events/{event_id}/engine-relay/fixtures/{fixture_id}")
    def delete_fixture(
        event_id: int,
        fixture_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        fixture = _required_fixture(connection, event_id, fixture_id)
        tournament = get_tournament(connection, fixture.tournament_id)
        if tournament is not None and tournament.status not in {"draft", "scheduled"}:
            raise HTTPException(status_code=409, detail="Only an unstarted relay fixture can be deleted.")
        delete_tournament(connection, fixture.tournament_id)
        _touch_event(connection, event_id)
        connection.commit()
        _publish_change(request, event_id)
        return _json({"message": "Relay fixture deleted."})

    @app.post("/api/admin/events/{event_id}/engine-relay/fixtures/{fixture_id}/schedule")
    @app.patch("/api/admin/events/{event_id}/engine-relay/fixtures/{fixture_id}/schedule")
    def schedule_fixture(
        event_id: int,
        fixture_id: int,
        payload: RelaySchedulePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        fixture = _required_fixture(connection, event_id, fixture_id)
        tournament = get_tournament(connection, fixture.tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="Relay tournament not found.")
        try:
            preparation = materialize_tournament_schedule(connection, tournament)
            scheduled = schedule_tournament(
                connection,
                tournament.id,
                payload.scheduled_start_at.isoformat(timespec="seconds"),
            )
            connection.commit()
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _publish_change(request, event_id)
        return _json(
            {
                "status": scheduled.status,
                "created_games": preparation.created_games,
                "message": "Relay fixture scheduled.",
            }
        )

    @app.delete("/api/admin/events/{event_id}/engine-relay/fixtures/{fixture_id}/schedule")
    def unschedule_fixture(
        event_id: int,
        fixture_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        fixture = _required_fixture(connection, event_id, fixture_id)
        try:
            unschedule_tournament(connection, fixture.tournament_id)
            connection.commit()
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _publish_change(request, event_id)
        return _json({"status": "draft", "message": "Relay fixture returned to draft."})

    @app.post("/api/admin/events/{event_id}/engine-relay/fixtures/{fixture_id}/start")
    def start_fixture(
        event_id: int,
        fixture_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        fixture = _required_fixture(connection, event_id, fixture_id)
        try:
            preparation = start_tournament(connection, fixture.tournament_id)
            connection.commit()
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _publish_change(request, event_id)
        return _json(
            {
                "status": "running",
                "created_games": preparation.created_games,
                "message": "Relay fixture started.",
            }
        )

    @app.put("/api/admin/events/{event_id}/engine-relay/visibility")
    def set_visibility(
        event_id: int,
        payload: RelayVisibilityPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        _required_event(connection, event_id)
        if payload.published and payload.status == "draft":
            raise HTTPException(status_code=422, detail="Choose a public event status before publishing.")
        set_event_status(connection, event_id, payload.status)
        set_event_published(connection, event_id, payload.published)
        connection.commit()
        _publish_change(request, event_id)
        return _json({"message": "Event visibility updated."})


def _provision(connection: sqlite3.Connection) -> int:
    event_id = create_event(
        connection,
        slug="openbench-engine-clash",
        handler_key=MODULE_KEY,
        handler_version=MODULE_VERSION,
        title="OpenBench Engine Clash",
        subtitle="One board. Two teams. Every handoff changes the fight.",
        summary="Open-source chess engines relay through a single game, carrying the position forward under individual node odds.",
        description="A relay exhibition built around the engines developed and tested across OpenBench communities. Each team fields a configured running order, and every engine owns a fixed stretch of team moves before handing the same position to the next specialist.",
        rules="Each fixture runs through the normal COPE tournament scheduler and remains unrated. Teams relay after their configured number of moves. Every roster member receives its own node allowance, and all moves retain the identity and telemetry of the engine that played them.",
        status="draft",
        featured=True,
        theme={
            "primary": "#f97316",
            "accent": "#22d3ee",
            "background": "#07111f",
            "surface": "#0e1d2e",
            "text": "#f8fafc",
        },
        config={"module": MODULE_KEY},
    )
    create_event_cast_member(
        connection,
        event_id,
        member_key="team-one",
        kind="team",
        display_name="Team One",
        short_name="ONE",
        role="relay team",
        accent_color="#f97316",
        position=0,
        metadata={"secondary_color": "#fdba74", "motto": "Built for the handoff"},
    )
    create_event_cast_member(
        connection,
        event_id,
        member_key="team-two",
        kind="team",
        display_name="Team Two",
        short_name="TWO",
        role="relay team",
        accent_color="#22d3ee",
        position=1,
        metadata={"secondary_color": "#a5f3fc", "motto": "Every node counts"},
    )
    return event_id


register_event_module(
    EventModule(
        key=MODULE_KEY,
        label="Engine Relay",
        version=MODULE_VERSION,
        provision=_provision,
        public_payload=_public_payload,
        admin_payload=_admin_payload,
        register_api=_register_api,
    )
)
