from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from cope.core.models import (
    AdjudicationConfig,
    IncrementTimeControl,
    RoundRobinFormatOptions,
    TournamentConfig,
    TournamentFormat,
)
from cope.db import (
    create_event,
    create_event_cast_member,
    create_tournament,
    get_event,
    get_tournament,
    list_engine_records,
    list_games,
    list_opening_suites,
    set_event_published,
    set_event_status,
    suite_opening_count,
)
from cope.db.events import EventRecord, utc_now
from cope.runner.scheduler import materialize_tournament_schedule

from .registry import EventModule, register_event_module


MODULE_KEY = "duck-vs-fish"


def reset_match(connection: sqlite3.Connection, event_id: int) -> int:
    event = get_event(connection, event_id)
    if event is None:
        raise ValueError("Duck vs Fish event not found.")
    config = TournamentConfig.model_validate(event.config["match"])
    tournament_id = create_tournament(connection, "Duck vs Fish", config)
    connection.execute(
        "UPDATE events SET config = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
        (json.dumps({**event.config, "tournament_id": tournament_id}), utc_now(), event_id),
    )
    tournament = get_tournament(connection, tournament_id)
    if tournament is None:
        raise RuntimeError("Duck vs Fish match could not be created.")
    materialize_tournament_schedule(connection, tournament)
    return tournament_id


def _provision(connection: sqlite3.Connection) -> int:
    engines = list_engine_records(connection)
    participants = []
    for name in ("wakwak", "Fairy-Stockfish"):
        candidates = [
            engine for engine in engines
            if engine.name.casefold() == name.casefold() and engine.active and engine.engine_active
        ]
        if not candidates:
            raise ValueError(f"Register an active {name} Duck Chess engine before creating this event.")
        participants.append(max(candidates, key=lambda engine: (engine.created_at, engine.id)))
    suite = next((suite for suite in list_opening_suites(connection) if suite.name == "Lichess UHO (Duck)" and suite.variant == "duck"), None)
    if suite is None:
        raise ValueError("Import the Lichess UHO (Duck) opening suite before creating this event.")
    if suite_opening_count(connection, suite.id) < 10:
        raise ValueError("Duck vs Fish requires at least ten Lichess UHO (Duck) openings.")
    match = TournamentConfig(
        variant="duck",
        format=TournamentFormat.ROUND_ROBIN,
        format_options=RoundRobinFormatOptions(cycles=10),
        participants=[engine.id for engine in participants],
        time_control=IncrementTimeControl(initial_ms=1_200_000, increment_ms=12_000),
        concurrency=1,
        opening_suite_id=suite.id,
        adjudication=AdjudicationConfig(),
        rated=False,
        engine_threads=64,
        engine_hash_mb=4096,
        lag_compensation_ms=50,
    )
    event_id = create_event(
        connection,
        slug=MODULE_KEY,
        handler_key=MODULE_KEY,
        title="Duck vs Fish",
        subtitle="",
        summary="Wakwak vs Fairy-Stockfish. Duck Chess, 20 games, 64 threads per engine, 20m + 12s.",
        description="",
        rules="20 unrated Duck Chess games in 10 colour-swapped Lichess UHO opening pairs. One game at a time. No evaluation adjudication or tiebreak games.",
        status="announced",
        featured=True,
        theme={"primary": "#edc653", "accent": "#b8d8a5", "background": "#080f13", "surface": "#142b24", "text": "#eff3e9"},
        config={"module": MODULE_KEY, "match": match.model_dump(mode="json"), "opening_book": suite.name},
    )
    for position, (engine, label, color) in enumerate(zip(participants, ("Wakwak", "Stockfish"), ("#edc653", "#a9d8d4"), strict=True)):
        create_event_cast_member(
            connection, event_id, member_key=("duck", "fish")[position], kind="engine",
            display_name=label, short_name=label, role="Challenger" if position == 0 else "Opponent",
            engine_version_id=engine.id, position=position, accent_color=color,
            profile=f"{engine.name} {engine.version}", metadata={"threads": 64, "hash_mb": 4096},
        )
    reset_match(connection, event_id)
    set_event_published(connection, event_id, True)
    return event_id


def _payload(connection: sqlite3.Connection, event: EventRecord) -> dict[str, Any]:
    tournament_id = event.config.get("tournament_id")
    tournament = get_tournament(connection, tournament_id) if tournament_id else None
    return {
        "tournament": tournament,
        "games": list_games(connection, tournament.id) if tournament is not None else (),
        "opening_book": event.config.get("opening_book", "Lichess UHO (Duck)"),
    }


class VisibilityPayload(BaseModel):
    published: bool


def _register_api(app: FastAPI) -> None:
    from cope.web import app as web_app

    @app.put("/api/admin/events/{event_id}/duck-vs-fish/visibility")
    def visibility(
        event_id: int,
        payload: VisibilityPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = get_event(connection, event_id)
        if event is None or event.handler_key != MODULE_KEY:
            raise HTTPException(status_code=404, detail="Duck vs Fish event not found.")
        if payload.published and event.status == "draft":
            set_event_status(connection, event_id, "announced")
        set_event_published(connection, event_id, payload.published)
        connection.commit()
        request.app.state.stream_hub.publish(f"event.{event_id}", "event.changed", {"event_id": event_id}, source="web")
        request.app.state.stream_hub.publish("admin", "admin.changed", {"event_id": event_id}, source="web")
        return JSONResponse(jsonable_encoder({"message": "Event visibility updated."}))


register_event_module(
    EventModule(
        key=MODULE_KEY,
        label="Duck vs Fish",
        version=1,
        provision=_provision,
        public_payload=_payload,
        admin_payload=_payload,
        register_api=_register_api,
    )
)
