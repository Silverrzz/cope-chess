from datetime import UTC

from fastapi import Depends, HTTPException, Request
from pydantic import AwareDatetime, BaseModel

from cope.adapters.http.responses import _json, _publish_admin_change
from cope.db import get_game, list_engine_records, list_games, list_moves
from cope.db.events import (
    create_event,
    get_event,
    set_event_published,
    set_event_status,
    update_event_identity,
)

from .registry import EventModule, register_event_module
from .secret_queen_competition import CompetitionSettings, public_competition, save_settings, settings, start_competition


MODULE_KEY = "secret-queen"
MODULE_VERSION = 1
ANNOUNCEMENT_STATUSES = {"draft", "announced", "scheduled", "postponed"}


class AnnouncementSettings(BaseModel):
    scheduled_start_at: AwareDatetime | None
    published: bool


def _admin_payload(connection, event):
    return {
        **public_competition(connection, event),
        "settings": settings(event).model_dump(mode="json"),
        "engines": [
            {"id": engine.id, "engine_id": engine.engine_id, "name": engine.name, "author": engine.author,
             "version": engine.version, "active": engine.active and engine.engine_active}
            for engine in list_engine_records(connection)
        ],
    }


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
    from cope.adapters.http.events import _require_viewable_event
    from cope.web import app as web_app

    def require_event(connection, event_id):
        event = get_event(connection, event_id, lock=True)
        if event is None or (event.handler_key, event.handler_version) != (MODULE_KEY, MODULE_VERSION):
            raise HTTPException(404, "Secret Queen event not found.")
        return event

    def publish_change(request, event_id):
        request.app.state.stream_hub.publish(
            f"event.{event_id}", "event.changed", {"event_id": event_id}, source="web"
        )
        _publish_admin_change(web_app, request)

    @app.put("/api/admin/events/{event_id}/secret-queen/competition")
    def save_competition(event_id: int, payload: CompetitionSettings, request: Request, connection=Depends(web_app._database)):
        event = require_event(connection, event_id)
        try:
            save_settings(connection, event, payload)
            connection.commit()
        except ValueError as error:
            connection.rollback()
            raise HTTPException(422, str(error)) from error
        publish_change(request, event_id)
        return _json({"message": "Competition settings saved."})

    @app.post("/api/admin/events/{event_id}/secret-queen/start")
    def start(event_id: int, request: Request, connection=Depends(web_app._database)):
        require_event(connection, event_id)
        try:
            tournaments = start_competition(connection, event_id)
            connection.commit()
        except ValueError as error:
            connection.rollback()
            raise HTTPException(409, str(error)) from error
        publish_change(request, event_id)
        return _json({"message": "Swiss group stages started.", "tournament_ids": tournaments})

    @app.get("/api/events/{slug}/secret-queen/games/{game_id}")
    def game_snapshot(slug: str, game_id: int, request: Request, connection=Depends(web_app._database)):
        event = _require_viewable_event(connection, slug, request)
        game = get_game(connection, game_id)
        if event.handler_key != MODULE_KEY or game is None or not web_app._event_has_tournament(connection, event.id, game.tournament_id):
            raise HTTPException(404, "Secret Queen game not found.")
        if not web_app._event_arena_is_public(event) and not web_app._admin_request_authenticated(request):
            raise HTTPException(404, "Secret Queen arena is closed.")
        moves = list_moves(connection, game.id)
        pair = [item for item in list_games(connection, game.tournament_id) if (item.round, item.pair_index) == (game.round, game.pair_index)]
        files = None
        if len(pair) == 2 and all(item.status == "finished" for item in pair):
            row = connection.execute(
                "SELECT white_file, black_file FROM secret_queen_pairs WHERE tournament_id = ? AND round = ? AND pair_index = ?",
                (game.tournament_id, game.round, game.pair_index),
            ).fetchone()
            if row is not None:
                files = {side: "abcdefgh"[row[f"{side}_file"]] for side in ("white", "black")}
        return _json({
            "moves": [{"ply": move.ply, "uci": move.uci, "san": move.san, "position_fen": move.position_fen,
                       "clock_after_ms": move.clock_after_ms} for move in moves],
            "status": game.status, "result": game.result, "termination": game.termination, "secrets": files,
        })

    @app.put("/api/admin/events/{event_id}/secret-queen/announcement")
    def save_announcement(
        event_id: int,
        payload: AnnouncementSettings,
        request: Request,
        connection=Depends(web_app._database),
    ):
        event = require_event(connection, event_id)
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

        publish_change(request, event_id)
        return _json({"message": "Announcement saved."})


register_event_module(
    EventModule(
        key=MODULE_KEY,
        label="Secret Queen",
        version=MODULE_VERSION,
        provision=_provision,
        public_payload=public_competition,
        admin_payload=_admin_payload,
        register_api=_register_api,
        public_arena_statuses=frozenset({"live", "intermission", "completed"}),
    )
)
