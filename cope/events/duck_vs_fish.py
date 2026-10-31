from __future__ import annotations

import json
import re
import secrets
import sqlite3
from typing import Any

import chess
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from cope.core.models import (
    AdjudicationConfig,
    IncrementTimeControl,
    OpeningLine,
    RoundRobinFormatOptions,
    TournamentConfig,
    TournamentFormat,
)
from cope.db import (
    create_event,
    create_event_cast_member,
    create_game,
    create_opening_suite,
    create_tournament,
    get_event,
    get_event_by_slug,
    get_tournament,
    list_engine_records,
    list_games,
    list_opening_suites,
    list_suite_openings,
    lock_tournament,
    replace_suite_openings,
    set_event_published,
    set_event_status,
    update_tournament,
)
from cope.db.events import EventRecord, utc_now
from cope.runner.scheduler import materialize_tournament_schedule

from .registry import EventModule, register_event_module


MODULE_KEY = "duck-vs-fish"
ODDS_SUITE = "Duck vs Fish start-position odds v1"
ODDS_STEPS = (
    ("Even material", ()),
    ("Pawn odds", ("f2",)),
    ("Two-pawn odds", ("f2", "c2")),
    ("Knight odds", ("b1",)),
    ("Rook odds", ("a1",)),
    ("Two-bishop odds", ("c1", "f1")),
    ("Queen odds", ("d1",)),
    ("Queen and two-pawn odds", ("d1", "f2", "c2")),
    ("Queen and rook odds", ("d1", "a1")),
    ("Queen and two-rook odds", ("d1", "a1", "h1")),
    ("Queen, two-rook and knight odds", ("d1", "a1", "h1", "b1")),
    ("Queen, two-rook and two-knight odds", ("d1", "a1", "h1", "b1", "g1")),
    ("King, bishop and eight pawns", ("d1", "a1", "h1", "b1", "g1", "c1")),
    ("King and eight pawns", ("d1", "a1", "h1", "b1", "g1", "c1", "f1")),
    *((f"King and {8 - count} {'pawn' if count == 7 else 'pawns'}" if count < 8 else "Lone king",
       ("d1", "a1", "h1", "b1", "g1", "c1", "f1", *tuple(f"{file}2" for file in "fcbgdeah"[:count])))
      for count in range(1, 9)),
)


def odds_openings() -> list[OpeningLine]:
    openings = []
    for index, (label, squares) in enumerate(ODDS_STEPS):
        board = chess.Board()
        for square in squares:
            target = chess.parse_square(square)
            board.remove_piece_at(chess.square_mirror(target) if index % 2 else target)
        board.castling_rights = board.clean_castling_rights()
        fen = board.fen()
        openings.append(OpeningLine(name=label, start_fen=fen, fen=fen))
    return openings


def _odds_suite(connection: sqlite3.Connection) -> int:
    suite = next((item for item in list_opening_suites(connection) if item.name == ODDS_SUITE and item.variant == "duck"), None)
    expected = odds_openings()
    if suite is not None:
        actual = list_suite_openings(connection, suite.id)
        if [(item.name, item.start_fen, item.moves, item.fen) for item in actual] != [(item.name, item.start_fen, item.moves, item.fen) for item in expected]:
            raise ValueError("The Duck vs Fish odds suite has been modified.")
        return suite.id
    suite_id = create_opening_suite(connection, name=ODDS_SUITE, variant="duck")
    replace_suite_openings(connection, suite_id, expected)
    return suite_id


def odds_event(connection: sqlite3.Connection, tournament_id: int) -> EventRecord | None:
    row = connection.execute(
        "SELECT id FROM events WHERE handler_key = 'duck-vs-fish' AND config::jsonb->>'tournament_id' = ? AND config::jsonb->>'odds_version' = '1'",
        (str(tournament_id),),
    ).fetchone()
    return get_event(connection, int(row["id"])) if row is not None else None


def advance_odds(connection: sqlite3.Connection, tournament_id: int) -> int | None:
    tournament = lock_tournament(connection, tournament_id)
    if tournament is None or odds_event(connection, tournament_id) is None:
        return None
    if tournament.status not in {"draft", "scheduled", "running"}:
        return 0
    games = list_games(connection, tournament_id)
    if any(game.status != "finished" for game in games):
        return 0
    wakwak, fish = tournament.config.participants
    if any(game.result != ("1-0" if game.white_engine_id == wakwak else "0-1") for game in games):
        return 0
    if len(games) >= len(ODDS_STEPS):
        return 0
    openings = list_suite_openings(connection, tournament.config.opening_suite_id)
    if len(openings) != len(ODDS_STEPS):
        raise ValueError("Duck vs Fish odds positions are missing.")
    number = len(games) + 1
    white, black = (wakwak, fish) if number % 2 else (fish, wakwak)
    create_game(connection, tournament_id=tournament_id, round=number, pair_index=1,
                white_engine_id=white, black_engine_id=black, game_number=number,
                opening_id=openings[number - 1].id)
    return 1


def _copy(label: str, threads: int) -> tuple[str, str]:
    return (
        f"wakwak vs Fairy-Stockfish Duck NNUE. Start-position odds ladder, {threads} threads per engine, {label}.",
        f"Unrated Duck Chess at {label}. Colours alternate every game, with wakwak White first. Start with normal material, then increase Fairy-Stockfish's material odds until wakwak has only its king. Every game starts from the initial position with the stated wakwak pieces removed; no book moves. One game at a time. The first wakwak draw or loss ends the event. No evaluation adjudication or tiebreak games.",
    )


def configure_odds(connection: sqlite3.Connection, event_id: int) -> None:
    connection.execute("SELECT id FROM events WHERE id = ? FOR UPDATE", (event_id,)).fetchone()
    event = get_event(connection, event_id)
    if event is None or event.handler_key != MODULE_KEY:
        raise ValueError("Duck vs Fish event not found.")
    tournament = lock_tournament(connection, event.config["tournament_id"])
    if tournament is None or tournament.status not in {"draft", "scheduled"} or tournament.started_at is not None:
        raise ValueError("Only an unstarted Duck vs Fish match can change to odds.")
    games = connection.execute("SELECT status FROM games WHERE tournament_id = ? FOR UPDATE", (tournament.id,)).fetchall()
    if any(game["status"] != "pending" for game in games):
        raise ValueError("Existing played or assigned games must be preserved.")
    if event.config.get("odds_version") == 1:
        return
    suite_id = _odds_suite(connection)
    config = tournament.config.model_copy(update={"opening_suite_id": suite_id, "format_options": RoundRobinFormatOptions(cycles=1), "concurrency": 1, "rated": False})
    connection.execute("DELETE FROM games WHERE tournament_id = ?", (tournament.id,))
    update_tournament(connection, tournament.id, name=tournament.name, config=config)
    control = config.time_control
    summary, rules = _copy(f"{control.initial_ms / 60_000:g}m + {control.increment_ms / 1000:g}s", config.engine_threads)
    connection.execute(
        "UPDATE events SET config = ?, summary = ?, rules = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
        (json.dumps({**event.config, "match": config.model_dump(mode="json"), "opening_book": "Start position", "odds_version": 1}), summary, rules, utc_now(), event_id),
    )
    advance_odds(connection, tournament.id)


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
    suite_id = _odds_suite(connection)
    match = TournamentConfig(
        variant="duck",
        format=TournamentFormat.ROUND_ROBIN,
        format_options=RoundRobinFormatOptions(cycles=1),
        participants=[engine.id for engine in participants],
        time_control=IncrementTimeControl(initial_ms=180_000, increment_ms=1_800),
        concurrency=1,
        opening_suite_id=suite_id,
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
        summary=_copy("3m + 1.8s", 64)[0],
        description="",
        rules=_copy("3m + 1.8s", 64)[1],
        status="announced",
        featured=True,
        theme={"primary": "#edc653", "accent": "#b8d8a5", "background": "#080f13", "surface": "#142b24", "text": "#eff3e9"},
        config={"module": MODULE_KEY, "match": match.model_dump(mode="json"), "opening_book": "Start position", "odds_version": 1},
    )
    for position, (engine, label, color) in enumerate(zip(participants, ("wakwak", "Fairy-Stockfish"), ("#edc653", "#a9d8d4"), strict=True)):
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
        "odds_steps": [label for label, _ in ODDS_STEPS] if event.config.get("odds_version") == 1 else [],
    }


class VisibilityPayload(BaseModel):
    published: bool


class CheerPayload(BaseModel):
    member_id: int = Field(gt=0)


def _register_api(app: FastAPI) -> None:
    from cope.web import app as web_app

    @app.put("/api/admin/events/{event_id}/duck-vs-fish/time-control")
    def time_control(
        event_id: int,
        payload: IncrementTimeControl,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        event = get_event(connection, event_id)
        if event is None or event.handler_key != MODULE_KEY:
            raise HTTPException(status_code=404, detail="Duck vs Fish event not found.")
        row = connection.execute("SELECT id FROM events WHERE id = ? FOR UPDATE", (event_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Duck vs Fish event not found.")
        event = get_event(connection, event_id)
        tournament_id = event.config.get("tournament_id")
        tournament_row = connection.execute("SELECT status FROM tournaments WHERE id = ? FOR UPDATE", (tournament_id,)).fetchone()
        if tournament_row is None or tournament_row["status"] != "draft":
            raise HTTPException(status_code=409, detail="Return the match to draft before changing its time control.")
        tournament = get_tournament(connection, tournament_id)
        config = tournament.config.model_copy(update={"time_control": payload})
        update_tournament(connection, tournament.id, name=tournament.name, config=config)
        label = f"{payload.initial_ms / 60_000:g}m + {payload.increment_ms / 1000:g}s"
        connection.execute(
            "UPDATE events SET config = ?, summary = ?, rules = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
            (
                json.dumps({**event.config, "match": config.model_dump(mode="json")}),
                _copy(label, config.engine_threads)[0] if event.config.get("odds_version") == 1 else f"wakwak vs Fairy-Stockfish Duck NNUE. Duck Blitz, 20 games, {config.engine_threads} threads per engine, {label}.",
                _copy(label, config.engine_threads)[1] if event.config.get("odds_version") == 1 else f"20 unrated Duck Chess games at {label} in 10 colour-swapped Lichess UHO opening pairs. One game at a time. No evaluation adjudication or tiebreak games.",
                utc_now(), event_id,
            ),
        )
        connection.commit()
        request.app.state.stream_hub.publish(f"event.{event_id}", "event.changed", {"event_id": event_id}, source="web")
        request.app.state.stream_hub.publish("admin", "admin.changed", {"event_id": event_id}, source="web")
        return JSONResponse({"message": "Event time control updated."})

    @app.post("/api/events/{slug}/duck-vs-fish/cheers")
    def cheer(
        slug: str,
        payload: CheerPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        hub = request.app.state.stream_hub
        host = request.client.host if request.client is not None else "unknown"
        supplied_client = request.headers.get("x-cope-cheer-client", "")
        client = supplied_client if re.fullmatch(r"[A-Za-z0-9-]{16,64}", supplied_client) else host
        if not hub.allow_ephemeral(f"cheer-host:{slug}:{host}", rate=12.0, burst=24):
            raise HTTPException(status_code=429, detail="Cheer rate limit exceeded.")
        if not hub.allow_ephemeral(f"cheer-client:{slug}:{host}:{client}", rate=4.0, burst=8):
            raise HTTPException(status_code=429, detail="Cheer rate limit exceeded.")
        event = get_event_by_slug(connection, slug)
        if event is None or event.handler_key != MODULE_KEY or event.handler_version != 1:
            raise HTTPException(status_code=404, detail="Duck vs Fish event not found.")
        if not web_app._event_is_public(event) and not web_app._admin_request_authenticated(request):
            raise HTTPException(status_code=401, detail="Admin session required.")
        member = connection.execute(
            "SELECT id, position FROM event_cast_members WHERE event_id = ? AND id = ? AND kind = 'engine' AND parent_id IS NULL",
            (event.id, payload.member_id),
        ).fetchone()
        if member is None:
            raise HTTPException(status_code=404, detail="Event engine not found.")
        if not hub.allow_ephemeral(f"cheer-event:{event.id}", rate=20.0, burst=30):
            raise HTTPException(status_code=429, detail="The crowd is cheering too quickly.")
        cheer_id = secrets.token_hex(8)
        hub.publish(
            f"event.{event.id}", "event.cheer",
            {"id": cheer_id, "event_id": event.id, "member_id": member["id"], "side": "left" if member["position"] == 0 else "right"},
            source="web", ephemeral=True,
        )
        return JSONResponse({"accepted": True, "cheer_id": cheer_id}, status_code=202)

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
