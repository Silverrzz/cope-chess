from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cope.db import (
    DEFAULT_DB_PATH,
    GameRecord,
    TournamentRecord,
    connect_database,
    get_game,
    get_tournament,
    list_engines,
    list_games,
    list_moves,
    list_tournaments,
)


PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def create_app(db_path: str | Path = DEFAULT_DB_PATH) -> FastAPI:
    app = FastAPI(title="COPE Chess")
    app.state.db_path = Path(db_path)
    app.mount(
        "/static",
        StaticFiles(directory=str(PACKAGE_DIR / "static")),
        name="static",
    )

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/tournaments", status_code=303)

    @app.get("/tournaments")
    def tournaments(
        request: Request,
        connection: sqlite3.Connection = Depends(_database),
    ):
        engines = _engine_names(connection)
        summaries = [
            _tournament_summary(connection, tournament, engines)
            for tournament in list_tournaments(connection)
        ]
        return templates.TemplateResponse(
            request,
            "tournaments.html",
            {
                "active_nav": "tournaments",
                "tournaments": summaries,
            },
        )

    @app.get("/tournaments/{tournament_id}")
    def tournament_detail(
        tournament_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(_database),
    ):
        tournament = get_tournament(connection, tournament_id)
        if tournament is None:
            raise HTTPException(status_code=404, detail="tournament not found")

        engines = _engine_names(connection)
        games = list_games(connection, tournament.id)
        return templates.TemplateResponse(
            request,
            "tournament_detail.html",
            {
                "active_nav": "tournaments",
                "tournament": tournament,
                "games": games,
                "engines": engines,
                "summary": _summarize_games(games),
                "config": _config_view(tournament),
            },
        )

    @app.get("/games/{game_id}")
    def game_detail(
        game_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(_database),
    ):
        game = get_game(connection, game_id)
        if game is None:
            raise HTTPException(status_code=404, detail="game not found")

        tournament = get_tournament(connection, game.tournament_id)
        moves = list_moves(connection, game.id)
        return templates.TemplateResponse(
            request,
            "game_detail.html",
            {
                "active_nav": "archive",
                "game": game,
                "tournament": tournament,
                "moves": moves,
                "engines": _engine_names(connection),
            },
        )

    return app


def _database(request: Request) -> Iterator[sqlite3.Connection]:
    connection = connect_database(request.app.state.db_path)
    try:
        yield connection
    finally:
        connection.close()


def _engine_names(connection: sqlite3.Connection) -> dict[int, str]:
    return {engine.engine_id: engine.name for engine in list_engines(connection)}


def _tournament_summary(
    connection: sqlite3.Connection,
    tournament: TournamentRecord,
    engines: dict[int, str],
) -> dict[str, Any]:
    games = list_games(connection, tournament.id)
    summary = _summarize_games(games)
    participant_names = [
        engines.get(engine_id, f"Engine {engine_id}")
        for engine_id in tournament.config.participants
    ]
    return {
        "record": tournament,
        "summary": summary,
        "participant_names": participant_names,
        "time_control": _time_control_label(tournament.config.time_control),
        "format": tournament.config.format.value.replace("_", " ").title(),
    }


def _summarize_games(games: tuple[GameRecord, ...]) -> dict[str, int]:
    summary = {
        "total": len(games),
        "pending": 0,
        "assigned": 0,
        "live": 0,
        "finished": 0,
        "abandoned": 0,
    }
    for game in games:
        summary[game.status] = summary.get(game.status, 0) + 1
    return summary


def _config_view(tournament: TournamentRecord) -> dict[str, Any]:
    return tournament.config.model_dump(mode="json")


def _time_control_label(time_control: Any) -> str:
    category = time_control.category
    if category == "increment":
        return (
            f"{_milliseconds(time_control.initial_ms)}"
            f" + {_milliseconds(time_control.increment_ms)}"
        )
    if category == "movetime":
        return f"{_milliseconds(time_control.move_time_ms)} per move"
    if category == "movestogo":
        return f"{_milliseconds(time_control.initial_ms)} / {time_control.moves_to_go}"
    if category == "movenodes":
        return f"{time_control.nodes:,} nodes"
    return str(category)


def _milliseconds(value: int) -> str:
    if value >= 60_000 and value % 60_000 == 0:
        return f"{value // 60_000}m"
    if value >= 1_000 and value % 1_000 == 0:
        return f"{value // 1_000}s"
    return f"{value}ms"

