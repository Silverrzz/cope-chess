from __future__ import annotations

import json
import re
import secrets
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from cope.core.models import (
    AdjudicationConfig,
    ColorSlot,
    EngineRelayAssignment,
    EngineRelayKibitzer,
    EngineRelayMember,
    EngineRelayTeam,
    IncrementTimeControl,
    KnockoutFormatOptions,
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
    list_engine_records,
    list_event_cast,
    list_games,
    list_moves,
    list_opening_suites,
    list_rating_lists,
    list_rating_rows,
    reconcile_engine_relay_event,
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
FINALE_MODULE_KEY = "engine-relay-finale"
MODULE_VERSION = 1
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_CHEER_CLIENT = re.compile(r"^[A-Za-z0-9-]{16,64}$")


@dataclass(frozen=True, slots=True)
class EngineRelayFixtureRecord:
    id: int
    event_id: int
    tournament_id: int
    team_a_id: int
    team_b_id: int
    anchor_a_engine_id: int
    anchor_b_engine_id: int
    kibitzer_engine_id: int | None
    kibitzer_threads: int | None
    kibitzer_hash_mb: int | None
    title: str
    position: int
    created_at: str


@dataclass(frozen=True, slots=True)
class EngineRelayFixtureTeamRecord:
    fixture_id: int
    team_id: int
    anchor_engine_id: int
    position: int


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


class RelayCheerPayload(BaseModel):
    team_id: int = Field(gt=0)
    side: Literal["left", "right"] | None = None


class RelayMemberPayload(BaseModel):
    engine_id: int = Field(gt=0)
    threads: int = Field(default=1, gt=0, le=1024)
    hash_mb: int = Field(default=256, gt=0, le=1_048_576)
    position: int = Field(default=0, ge=0, le=1000)
    label: str = Field(default="", max_length=80)

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        return value.strip()


class RelayFixturePayload(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    team_ids: list[int] = Field(default_factory=list)
    team_a_id: int | None = Field(default=None, gt=0)
    team_b_id: int | None = Field(default=None, gt=0)
    cycles: int = Field(default=1, gt=0, le=1000)
    opening_suite_id: int | None = Field(default=None, gt=0)
    concurrency: int = Field(default=1, gt=0, le=256)
    initial_ms: int = Field(default=60_000, gt=0)
    increment_ms: int = Field(default=1_000, ge=0)
    lag_compensation_ms: int = Field(default=50, ge=0, le=60_000)
    adjudication: AdjudicationConfig = Field(default_factory=AdjudicationConfig)
    uci_options: dict[str, str | int | bool] = Field(default_factory=dict)
    kibitzer_engine_id: int | None = Field(default=None, gt=0)
    kibitzer_threads: int = Field(default=1, gt=0, le=1024)
    kibitzer_hash_mb: int = Field(default=256, gt=0, le=1_048_576)
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
                raise ValueError("Threads and Hash use the relay engine resource controls")
        return value

    @field_validator("scheduled_start_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("scheduled start time must include a timezone")
        return None if value is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def selected_teams(self) -> RelayFixturePayload:
        team_ids = self.team_ids
        if not team_ids and self.team_a_id is not None and self.team_b_id is not None:
            team_ids = [self.team_a_id, self.team_b_id]
        if len(team_ids) < 2:
            raise ValueError("a relay fixture requires at least two teams")
        if any(team_id <= 0 for team_id in team_ids):
            raise ValueError("relay team ids must be positive")
        if len(set(team_ids)) != len(team_ids):
            raise ValueError("relay fixture teams must be unique")
        self.team_ids = team_ids
        self.team_a_id = team_ids[0]
        self.team_b_id = team_ids[1]
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
    published: bool
    status: str | None = Field(
        default=None,
        pattern=r"^(draft|announced|scheduled|live|intermission|postponed|completed|cancelled)$",
    )


def _fixture_from_row(row: Any) -> EngineRelayFixtureRecord:
    return EngineRelayFixtureRecord(
        id=int(row["id"]),
        event_id=int(row["event_id"]),
        tournament_id=int(row["tournament_id"]),
        team_a_id=int(row["team_a_id"]),
        team_b_id=int(row["team_b_id"]),
        anchor_a_engine_id=int(row["anchor_a_engine_id"]),
        anchor_b_engine_id=int(row["anchor_b_engine_id"]),
        kibitzer_engine_id=None if row["kibitzer_engine_id"] is None else int(row["kibitzer_engine_id"]),
        kibitzer_threads=None if row["kibitzer_threads"] is None else int(row["kibitzer_threads"]),
        kibitzer_hash_mb=None if row["kibitzer_hash_mb"] is None else int(row["kibitzer_hash_mb"]),
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


def _fixture_teams(
    connection: sqlite3.Connection,
    fixture: EngineRelayFixtureRecord,
) -> tuple[EngineRelayFixtureTeamRecord, ...]:
    rows = connection.execute(
        """
        SELECT fixture_id, team_id, anchor_engine_id, position
        FROM engine_relay_fixture_teams
        WHERE fixture_id = ?
        ORDER BY position, team_id
        """,
        (fixture.id,),
    ).fetchall()
    if not rows:
        return (
            EngineRelayFixtureTeamRecord(fixture.id, fixture.team_a_id, fixture.anchor_a_engine_id, 0),
            EngineRelayFixtureTeamRecord(fixture.id, fixture.team_b_id, fixture.anchor_b_engine_id, 1),
        )
    return tuple(
        EngineRelayFixtureTeamRecord(
            fixture_id=int(row["fixture_id"]),
            team_id=int(row["team_id"]),
            anchor_engine_id=int(row["anchor_engine_id"]),
            position=int(row["position"]),
        )
        for row in rows
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
        max(1, int(member.metadata.get("threads", 1))),
        max(1, int(member.metadata.get("hash_mb", 256))),
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
                threads=_member_settings(member)[0],
                hash_mb=_member_settings(member)[1],
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
    fixture_teams = _fixture_teams(connection, fixture)
    teams_by_anchor = {
        item.anchor_engine_id: cast.get(item.team_id)
        for item in fixture_teams
    }
    white_team = teams_by_anchor.get(game.white_engine_id)
    black_team = teams_by_anchor.get(game.black_engine_id)
    if white_team is None or black_team is None:
        raise RuntimeError("relay fixture team is missing")
    return EngineRelayAssignment(
        event_id=fixture.event_id,
        fixture_id=fixture.id,
        teams={
            ColorSlot.WHITE: _team_assignment(connection, fixture.event_id, white_team),
            ColorSlot.BLACK: _team_assignment(connection, fixture.event_id, black_team),
        },
        kibitzer=None if fixture.kibitzer_engine_id is None else EngineRelayKibitzer(
            engine_id=fixture.kibitzer_engine_id,
            threads=fixture.kibitzer_threads or 1,
            hash_mb=fixture.kibitzer_hash_mb or 256,
        ),
    )


def relay_engine_ids_for_game(
    connection: sqlite3.Connection,
    game: GameRecord,
) -> tuple[int, ...]:
    relay = relay_assignment_for_game(connection, game)
    if relay is None:
        return ()
    engine_ids = tuple(
        member.engine_id
        for team in relay.teams.values()
        for member in team.members
    )
    if relay.kibitzer is not None:
        return (*engine_ids, relay.kibitzer.engine_id)
    return engine_ids


def relay_engine_count_for_tournament(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> int:
    fixture = _get_fixture_for_tournament(connection, tournament_id)
    if fixture is None:
        return 2
    return sum(
        len(_team_members(connection, fixture.event_id, team_id))
        for team_id in (item.team_id for item in _fixture_teams(connection, fixture))
    ) + (1 if fixture.kibitzer_engine_id is not None else 0)


def relay_engine_ids_for_tournament(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> tuple[int, ...]:
    fixture = _get_fixture_for_tournament(connection, tournament_id)
    if fixture is None:
        return ()
    engine_ids = tuple(
        int(member.engine_version_id)
        for fixture_team in _fixture_teams(connection, fixture)
        for member in _team_members(connection, fixture.event_id, fixture_team.team_id)
        if member.engine_version_id is not None
    )
    if fixture.kibitzer_engine_id is not None:
        return (*engine_ids, fixture.kibitzer_engine_id)
    return engine_ids


def relay_resources_for_tournament(
    connection: sqlite3.Connection,
    tournament_id: int,
    participant_engine_ids: tuple[int, ...] | None = None,
) -> tuple[tuple[int, int], ...]:
    fixture = _get_fixture_for_tournament(connection, tournament_id)
    if fixture is None:
        return ()
    fixture_teams = _fixture_teams(connection, fixture)
    if participant_engine_ids is not None:
        selected_anchors = set(participant_engine_ids)
        fixture_teams = tuple(
            item for item in fixture_teams
            if item.anchor_engine_id in selected_anchors
        )
    resources = tuple(
        _member_settings(member)[:2]
        for team_id in (item.team_id for item in fixture_teams)
        for member in _team_members(connection, fixture.event_id, team_id)
    )
    if fixture.kibitzer_engine_id is not None:
        team_threads = max((threads for threads, _ in resources), default=0)
        return (*resources, (team_threads + (fixture.kibitzer_threads or 1), fixture.kibitzer_hash_mb or 256))
    return resources


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
    return team.members[moves_played % len(team.members)]


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
    ratings: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    roster = []
    for member in _team_members(connection, event_id, team.id):
        threads, hash_mb, position, label = _member_settings(member)
        engine = get_engine(connection, int(member.engine_version_id or 0))
        roster.append(
            {
                "id": member.id,
                "engine_id": member.engine_version_id,
                "name": engine.name if engine is not None else member.display_name,
                "version": engine.version if engine is not None else "",
                "display_name": member.display_name,
                "label": label,
                "threads": threads,
                "hash_mb": hash_mb,
                "position": position,
                "rating": ratings.get(int(member.engine_version_id or 0)),
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
    connection: sqlite3.Connection,
    fixture: EngineRelayFixtureRecord,
    game: GameRecord,
) -> tuple[int, int]:
    teams_by_anchor = {
        item.anchor_engine_id: item.team_id
        for item in _fixture_teams(connection, fixture)
    }
    try:
        return teams_by_anchor[game.white_engine_id], teams_by_anchor[game.black_engine_id]
    except KeyError as cause:
        raise RuntimeError("relay fixture game anchors do not match its teams") from cause


def _fixture_payload(
    connection: sqlite3.Connection,
    fixture: EngineRelayFixtureRecord,
) -> dict[str, Any]:
    tournament = get_tournament(connection, fixture.tournament_id)
    worker = None
    if tournament is not None and tournament.status in {
        "scheduled",
        "running",
        "paused",
    }:
        worker = connection.execute(
            """
            SELECT workers.id, workers.label, workers.status, claims.claimed_at,
                   EXISTS (
                     SELECT 1
                     FROM games game
                     JOIN game_assignments assignment ON assignment.game_id = game.id
                     WHERE game.tournament_id = claims.tournament_id
                       AND assignment.worker_id = claims.worker_id
                       AND assignment.status IN ('acked', 'live')
                   ) AS prepared
            FROM event_fixture_workers claims
            JOIN workers ON workers.id = claims.worker_id
            WHERE claims.tournament_id = ?
            """,
            (fixture.tournament_id,),
        ).fetchone()
    games = () if tournament is None else list_games(connection, tournament.id)
    cast = _cast_by_id(connection, fixture.event_id)
    fixture_teams = _fixture_teams(connection, fixture)
    team_assignments = {
        item.team_id: _team_assignment(connection, fixture.event_id, cast[item.team_id])
        for item in fixture_teams
        if item.team_id in cast
    }
    game_payloads = []
    for game in games:
        white_team_id, black_team_id = _side_team_ids(connection, fixture, game)
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
    winner_team_id = None
    if tournament is not None and tournament.status == "finished":
        winner = connection.execute(
            """
            SELECT winner_engine_id
            FROM tournament_matches
            WHERE tournament_id = ? AND status IN ('finished', 'bye')
            ORDER BY round DESC, match_index, id
            LIMIT 1
            """,
            (tournament.id,),
        ).fetchone()
        if winner is not None and winner["winner_engine_id"] is not None:
            winner_engine_id = int(winner["winner_engine_id"])
            winner_team_id = next(
                (
                    item.team_id
                    for item in fixture_teams
                    if item.anchor_engine_id == winner_engine_id
                ),
                None,
            )
    kibitzer = None
    if fixture.kibitzer_engine_id is not None:
        engine = get_engine(connection, fixture.kibitzer_engine_id)
        kibitzer = {
            "engine_id": fixture.kibitzer_engine_id,
            "name": "Kibitzer" if engine is None else engine.name,
            "version": "" if engine is None else engine.version,
            "threads": fixture.kibitzer_threads or 1,
            "hash_mb": fixture.kibitzer_hash_mb or 256,
        }
    return {
        **asdict(fixture),
        "tournament": None if tournament is None else asdict(tournament),
        "worker": None if worker is None else dict(worker),
        "games": game_payloads,
        "winner_team_id": winner_team_id,
        "kibitzer": kibitzer,
        "teams": [
            {
                "id": item.team_id,
                "name": cast[item.team_id].display_name if item.team_id in cast else "Unknown team",
                "anchor_engine_id": item.anchor_engine_id,
                "position": item.position,
            }
            for item in fixture_teams
        ],
        "team_a_name": cast.get(fixture.team_a_id).display_name if fixture.team_a_id in cast else "Team A",
        "team_b_name": cast.get(fixture.team_b_id).display_name if fixture.team_b_id in cast else "Team B",
    }


def _payload(connection: sqlite3.Connection, event: EventRecord, *, admin: bool) -> dict[str, Any]:
    ratings: dict[int, dict[str, Any]] = {}
    for rating_list in list_rating_lists(connection):
        for row in list_rating_rows(connection, rating_list.id):
            candidate = {
                "elo": row.elo,
                "error_margin": row.error_margin,
                "list_name": rating_list.name,
            }
            current = ratings.get(row.engine.engine_id)
            candidate_margin = row.error_margin if row.error_margin is not None else float("inf")
            current_margin = current["error_margin"] if current and current["error_margin"] is not None else float("inf")
            if current is None or candidate_margin < current_margin:
                ratings[row.engine.engine_id] = candidate
    teams = [_team_payload(connection, event.id, team, ratings) for team in _teams(connection, event.id)]
    fixtures = [_fixture_payload(connection, fixture) for fixture in _list_fixtures(connection, event.id)]
    payload: dict[str, Any] = {
        "teams": teams,
        "fixtures": fixtures,
        "format": event.handler_key,
    }
    if admin:
        payload["engine_options"] = [
            {
                "id": engine.id,
                "engine_id": engine.engine_id,
                "name": engine.name,
                "version": engine.version,
                "author": engine.author,
                "source_kind": engine.source_kind,
            }
            for engine in list_engine_records(connection)
            if engine.active and engine.engine_active
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
    if event is None or event.handler_key not in {MODULE_KEY, FINALE_MODULE_KEY} or event.handler_version != MODULE_VERSION:
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
        JOIN engine_relay_fixture_teams fixture_team ON fixture_team.fixture_id = fixture.id
        JOIN tournaments tournament ON tournament.id = fixture.tournament_id
        WHERE fixture_team.team_id = ?
          AND tournament.status IN ('running', 'paused', 'finished', 'aborted')
        LIMIT 1
        """,
        (team_id,),
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
            SELECT fixture.*
            FROM engine_relay_fixtures fixture
            JOIN engine_relay_fixture_teams fixture_team ON fixture_team.fixture_id = fixture.id
            WHERE fixture.event_id = ? AND fixture_team.team_id = ?
            """,
            (event_id, team_id),
        )
    )
    for fixture in fixtures:
        engine_ids = {
            engine_id,
            *(
                int(member.engine_version_id)
                for fixture_team in _fixture_teams(connection, fixture)
                for member in _team_members(connection, event_id, fixture_team.team_id)
                if member.engine_version_id is not None
            ),
        }
        if fixture.kibitzer_engine_id is not None:
            engine_ids.add(fixture.kibitzer_engine_id)
        engines = tuple(get_engine(connection, value) for value in engine_ids)
        managed_engines = tuple(
            item
            for item in engines
            if item is not None and item.distribution == "managed"
        )
        if any(item is None for item in engines) or (
            managed_engines
            and get_common_benchmark_reference(connection, managed_engines) is None
        ):
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
    request.app.state.stream_hub.publish_to_internal(
        "runner.wake",
        {"reason": f"engine-relay-event:{event_id}"},
    )
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
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = get_event_by_slug(connection, slug)
        if (
            event is None
            or event.handler_key not in {MODULE_KEY, FINALE_MODULE_KEY}
            or event.handler_version != MODULE_VERSION
        ):
            raise HTTPException(status_code=404, detail="Engine relay event not found.")
        if not web_app._event_is_public(event) and not web_app._admin_request_authenticated(request):
            raise HTTPException(status_code=401, detail="Admin session required.")
        return _json(_public_payload(connection, event))

    @app.post("/api/events/{slug}/engine-relay/cheers")
    def cheer_for_team(
        slug: str,
        payload: RelayCheerPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        hub = request.app.state.stream_hub
        client_host = request.client.host if request.client is not None else "unknown"
        supplied_client = request.headers.get("x-cope-cheer-client", "")
        client_key = supplied_client if _CHEER_CLIENT.fullmatch(supplied_client) else client_host
        if not hub.allow_ephemeral(
            f"cheer-host:{slug}:{client_host}",
            rate=12.0,
            burst=24,
        ):
            raise HTTPException(status_code=429, detail="Cheer rate limit exceeded.")
        if not hub.allow_ephemeral(
            f"cheer-client:{slug}:{client_host}:{client_key}",
            rate=4.0,
            burst=8,
        ):
            raise HTTPException(status_code=429, detail="Cheer rate limit exceeded.")
        event = get_event_by_slug(connection, slug)
        if (
            event is None
            or event.handler_key not in {MODULE_KEY, FINALE_MODULE_KEY}
            or event.handler_version != MODULE_VERSION
        ):
            raise HTTPException(status_code=404, detail="Engine relay event not found.")
        if not web_app._event_is_public(event) and not web_app._admin_request_authenticated(request):
            raise HTTPException(status_code=401, detail="Admin session required.")
        team = connection.execute(
            "SELECT id FROM event_cast_members WHERE event_id = ? AND id = ? AND kind = 'team'",
            (event.id, payload.team_id),
        ).fetchone()
        if team is None:
            raise HTTPException(status_code=404, detail="Relay team not found.")
        if not hub.allow_ephemeral(
            f"cheer-event:{event.id}",
            rate=20.0,
            burst=30,
        ):
            raise HTTPException(status_code=429, detail="The crowd is cheering too quickly.")
        cheer = {
            "id": secrets.token_hex(8),
            "event_id": event.id,
            "team_id": payload.team_id,
            "side": payload.side,
        }
        hub.publish(
            f"event.{event.id}",
            "event.cheer",
            cheer,
            source="web",
            ephemeral=True,
        )
        return _json({"accepted": True, "cheer_id": cheer["id"]}, 202)

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
            "SELECT 1 FROM engine_relay_fixture_teams WHERE team_id = ? LIMIT 1",
            (team_id,),
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
        duplicate = connection.execute(
            """
            SELECT 1
            FROM event_cast_members member
            JOIN event_cast_members team ON team.id = member.parent_id
            WHERE member.event_id = ?
              AND member.kind = 'engine'
              AND member.engine_version_id = ?
              AND team.event_id = member.event_id
              AND team.kind = 'team'
              AND team.parent_id IS NULL
            LIMIT 1
            """,
            (event_id, payload.engine_id),
        ).fetchone()
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
                "threads": payload.threads,
                "hash_mb": payload.hash_mb,
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
                        "threads": payload.threads,
                        "hash_mb": payload.hash_mb,
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
            SELECT 1 FROM engine_relay_fixture_teams
            WHERE team_id = ? AND anchor_engine_id = ?
            LIMIT 1
            """,
            (team_id, member.engine_version_id),
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
        event = _required_event(connection, event_id)
        finale = event.handler_key == FINALE_MODULE_KEY
        if finale and len(payload.team_ids) != 2:
            raise HTTPException(status_code=422, detail="A sudden-death finale requires exactly two teams.")
        teams = tuple(
            _required_team(connection, event_id, team_id)
            for team_id in payload.team_ids
        )
        if payload.opening_suite_id is not None and not any(
            suite.id == payload.opening_suite_id
            for suite in list_opening_suites(connection)
        ):
            raise HTTPException(status_code=422, detail="Select an available opening suite.")
        if any(not _team_members(connection, event_id, team.id) for team in teams):
            raise HTTPException(status_code=422, detail="Every team needs at least one engine before creating a fixture.")
        relay_teams = tuple(
            _team_assignment(connection, event_id, team)
            for team in teams
        )
        engine_ids = [member.engine_id for team in relay_teams for member in team.members]
        if len(set(engine_ids)) != len(engine_ids):
            raise HTTPException(status_code=409, detail="An engine version cannot play for both relay teams.")
        if payload.kibitzer_engine_id is not None:
            if payload.kibitzer_engine_id in engine_ids:
                raise HTTPException(status_code=409, detail="The kibitzer must be independent from both relay benches.")
            engine_ids.append(payload.kibitzer_engine_id)
        engines = tuple(get_engine(connection, engine_id) for engine_id in engine_ids)
        if any(engine is None for engine in engines):
            raise HTTPException(status_code=422, detail="Every relay engine must still be available.")
        managed_engines = tuple(
            engine
            for engine in engines
            if engine is not None and engine.distribution == "managed"
        )
        if managed_engines and get_common_benchmark_reference(connection, managed_engines) is None:
            raise HTTPException(status_code=409, detail="The full relay roster does not share a benchmark hardware reference yet.")
        anchors = [team.members[0].engine_id for team in relay_teams]
        config = TournamentConfig(
            format=TournamentFormat.KNOCKOUT if finale else TournamentFormat.ROUND_ROBIN,
            format_options=KnockoutFormatOptions() if finale else RoundRobinFormatOptions(cycles=payload.cycles),
            participants=anchors,
            time_control=IncrementTimeControl(
                initial_ms=payload.initial_ms,
                increment_ms=payload.increment_ms,
            ),
            concurrency=payload.concurrency,
            opening_suite_id=payload.opening_suite_id,
            adjudication=payload.adjudication,
            rated=False,
            lag_compensation_ms=payload.lag_compensation_ms,
            engine_threads=max(member.threads for team in relay_teams for member in team.members),
            engine_hash_mb=max(member.hash_mb for team in relay_teams for member in team.members),
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
              anchor_a_engine_id, anchor_b_engine_id,
              kibitzer_engine_id, kibitzer_threads, kibitzer_hash_mb,
              title, position, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                tournament_id,
                payload.team_ids[0],
                payload.team_ids[1],
                anchors[0],
                anchors[1],
                payload.kibitzer_engine_id,
                payload.kibitzer_threads if payload.kibitzer_engine_id is not None else None,
                payload.kibitzer_hash_mb if payload.kibitzer_engine_id is not None else None,
                payload.title,
                position,
                utc_now(),
            ),
        )
        fixture_id = int(cursor.lastrowid)
        connection.executemany(
            """
            INSERT INTO engine_relay_fixture_teams (
              fixture_id, team_id, anchor_engine_id, position
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                (fixture_id, team_id, anchor_engine_id, position)
                for position, (team_id, anchor_engine_id) in enumerate(
                    zip(payload.team_ids, anchors, strict=True)
                )
            ),
        )
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
        else:
            reconcile_engine_relay_event(connection, event_id)
        _touch_event(connection, event_id)
        connection.commit()
        _publish_change(request, event_id)
        return _json(
            {
                "id": fixture_id,
                "tournament_id": tournament_id,
                "message": "Sudden-death relay finale created." if finale else "Relay round-robin fixture created as an unrated tournament.",
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
        try:
            lifecycle = reconcile_engine_relay_event(connection, event_id)
            if payload.published and lifecycle.status == "draft":
                set_event_status(connection, event_id, "announced")
            set_event_published(connection, event_id, payload.published)
            lifecycle = reconcile_engine_relay_event(connection, event_id)
            connection.commit()
        except (ValueError, sqlite3.IntegrityError) as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _publish_change(request, event_id)
        return _json(
            {
                "status": lifecycle.status,
                "published": lifecycle.published_at is not None,
                "message": "Event visibility updated.",
            }
        )


def _provision(connection: sqlite3.Connection) -> int:
    event_id = create_event(
        connection,
        slug="openbench-engine-clash",
        handler_key=MODULE_KEY,
        handler_version=MODULE_VERSION,
        title="OpenBench Engine Clash",
        subtitle="One board. Two teams. Every handoff changes the fight.",
        summary="Open-source chess engines relay through a single game, carrying the position forward under a shared clock.",
        description="A relay exhibition built around the engines developed and tested across OpenBench communities. Each team fields a configured running order, and engines hand the position to the next specialist after every team move.",
        rules="Each fixture runs through the normal COPE tournament scheduler and remains unrated. Teams relay after every move under the fixture clock. Every roster member receives its own thread and hash allocation, and all moves retain the identity and telemetry of the engine that played them.",
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
        metadata={"secondary_color": "#fdba74", "motto": ""},
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
        metadata={"secondary_color": "#a5f3fc", "motto": ""},
    )
    return event_id


def _provision_finale(connection: sqlite3.Connection) -> int:
    event_id = create_event(
        connection,
        slug="engine-relay-finale",
        handler_key=FINALE_MODULE_KEY,
        handler_version=MODULE_VERSION,
        title="Engine Relay Finale",
        subtitle="One pair decides everything.",
        summary="Two engine benches trade the position until one team wins a colour-swapped pair and claims the finale.",
        description="A sudden-death relay exhibition where every engine handoff matters and a tied pair immediately sends both benches back out for another.",
        rules="Each match begins with a colour-swapped pair of unrated games. If the pair is tied, another pair starts. The first team to outscore its opponent across a completed pair wins the event.",
        status="draft",
        featured=True,
        theme={
            "primary": "#d5a72d",
            "accent": "#f5cf62",
            "background": "#100c02",
            "surface": "#211804",
            "text": "#fff8d8",
        },
        config={"module": FINALE_MODULE_KEY},
    )
    create_event_cast_member(
        connection,
        event_id,
        member_key="team-one",
        kind="team",
        display_name="Team One",
        short_name="ONE",
        role="finale bench",
        accent_color="#d5a72d",
        position=0,
        metadata={"secondary_color": "#f5cf62", "motto": ""},
    )
    create_event_cast_member(
        connection,
        event_id,
        member_key="team-two",
        kind="team",
        display_name="Team Two",
        short_name="TWO",
        role="finale bench",
        accent_color="#8b6a18",
        position=1,
        metadata={"secondary_color": "#e9c85b", "motto": ""},
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

register_event_module(
    EventModule(
        key=FINALE_MODULE_KEY,
        label="Engine Relay Finale",
        version=MODULE_VERSION,
        provision=_provision_finale,
        public_payload=_public_payload,
        admin_payload=_admin_payload,
    )
)
