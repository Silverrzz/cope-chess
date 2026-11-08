from __future__ import annotations

import logging
import sqlite3

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError, model_validator

from cope.adapters.http.responses import _json, _publish_admin_change
from cope.adapters.http.tournament_config import (
    TournamentPayload,
    _engine_is_tournament_ready,
    _tournament_form_payload,
    _validated_tournament_config,
)
from cope.db import (
    create_tournament,
    get_engine_record,
    get_rating_list,
    invalidate_rating_list_engine_games,
    list_engine_records,
    list_rating_list_engine_ids,
    list_rating_lists,
    list_rating_rows,
)

LOG = logging.getLogger(__name__)


class TournamentCreatorBatchPayload(BaseModel):
    tournaments: list[TournamentPayload] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_shared_settings(self) -> TournamentCreatorBatchPayload:
        shared = self.tournaments[0].config.model_dump(
            mode="json",
            exclude={"participants"},
        )
        if any(
            item.config.model_dump(mode="json", exclude={"participants"}) != shared
            for item in self.tournaments[1:]
        ):
            raise ValueError("batch tournaments must share the same settings")
        return self


class GauntletPreviewPayload(BaseModel):
    rating_list_id: int = Field(gt=0)
    hero_engine_id: int = Field(gt=0)
    elo_estimate: float = Field(ge=-10000, le=10000)
    gauntlet_size: int = Field(ge=2, le=500)


class InvalidateRatingListEnginePayload(BaseModel):
    engine_id: int = Field(gt=0)
    rating_list_id: int = Field(gt=0)


def register_tournament_tools_routes(app: FastAPI) -> None:
    from cope.web import app as web_app

    @app.get("/api/admin/tools/tournament-creator")
    def admin_tournament_creator(
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        engine_records = {engine.id: engine for engine in list_engine_records(connection)}
        eligible_engine_ids = {
            engine.id
            for engine in engine_records.values()
            if _engine_is_tournament_ready(connection, engine)
        }
        rating_lists = []
        for rating_list in list_rating_lists(connection):
            engines = []
            unavailable_engines = 0
            for row in list_rating_rows(connection, rating_list.id):
                engine_id = row.engine.engine_id
                if engine_id not in eligible_engine_ids:
                    unavailable_engines += 1
                    continue
                engines.append(
                    {
                        "id": engine_id,
                        "name": row.engine.name,
                        "version": row.engine.version,
                        "elo": row.elo,
                        "games_played": row.games_played,
                    }
                )
            rating_lists.append(
                {
                    "id": rating_list.id,
                    "name": rating_list.name,
                    "variant": rating_list.variant,
                    "rules_revision": rating_list.rules_revision,
                    "engines": engines,
                    "unavailable_engines": unavailable_engines,
                }
            )
        form = _tournament_form_payload(web_app, request, connection)
        form["engine_options"] = [
            engine for engine in form["engine_options"]
            if engine.id in eligible_engine_ids
        ]
        return _json(
            {
                "form": form,
                "rating_lists": rating_lists,
            }
        )

    @app.post("/api/admin/tools/tournament-creator/gauntlet-preview")
    def admin_tournament_creator_gauntlet_preview(
        payload: GauntletPreviewPayload,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        rating_list = get_rating_list(connection, payload.rating_list_id)
        if rating_list is None:
            raise HTTPException(status_code=404, detail="Rating list not found.")
        hero = get_engine_record(connection, payload.hero_engine_id)
        if (
            hero is None
            or not _engine_is_tournament_ready(connection, hero)
        ):
            raise HTTPException(status_code=422, detail="Choose an available hero engine.")
        engine_records = {engine.id: engine for engine in list_engine_records(connection)}
        candidates = []
        for row in list_rating_rows(connection, payload.rating_list_id):
            engine_id = row.engine.engine_id
            record = engine_records.get(engine_id)
            if engine_id == payload.hero_engine_id or record is None:
                continue
            if not _engine_is_tournament_ready(connection, record):
                continue
            candidates.append(row)
        opponent_count = payload.gauntlet_size - 1
        if len(candidates) < opponent_count:
            raise HTTPException(
                status_code=422,
                detail=f"This list has only {len(candidates)} available opponent{'s' if len(candidates) != 1 else ''} for the selected hero.",
            )
        rank_denominator = max(1, len(candidates) - 1)
        closeness_ranks = {
            row.engine.engine_id: rank / rank_denominator
            for rank, row in enumerate(
                sorted(
                    candidates,
                    key=lambda item: (
                        abs(item.elo - payload.elo_estimate),
                        item.games_played,
                        item.engine.engine_id,
                    ),
                )
            )
        }
        coverage_ranks = {
            row.engine.engine_id: rank / rank_denominator
            for rank, row in enumerate(
                sorted(
                    candidates,
                    key=lambda item: (
                        item.games_played,
                        abs(item.elo - payload.elo_estimate),
                        item.engine.engine_id,
                    ),
                )
            )
        }

        def selection_score(row) -> tuple[float, int, float, int]:
            rating_distance = abs(row.elo - payload.elo_estimate)
            return (
                0.7 * closeness_ranks[row.engine.engine_id]
                + 0.3 * coverage_ranks[row.engine.engine_id],
                row.games_played,
                rating_distance,
                row.engine.engine_id,
            )

        selected = sorted(candidates, key=selection_score)[:opponent_count]
        return _json(
            {
                "hero": {
                    "id": hero.id,
                    "name": hero.name,
                    "version": hero.version,
                },
                "opponents": [
                    {
                        "id": row.engine.engine_id,
                        "name": row.engine.name,
                        "version": row.engine.version,
                        "elo": row.elo,
                        "games_played": row.games_played,
                        "rating_distance": abs(row.elo - payload.elo_estimate),
                        "selection_score": selection_score(row)[0],
                    }
                    for row in selected
                ],
            }
        )

    @app.post("/api/admin/tools/tournament-creator/batch")
    def admin_tournament_creator_batch(
        payload: TournamentCreatorBatchPayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        try:
            tournaments = [
                (item.name, _validated_tournament_config(connection, item.config))
                for item in payload.tournaments
            ]
            created = [
                {
                    "id": create_tournament(connection, name, config, status="draft"),
                    "name": name,
                    "participants": len(config.participants),
                }
                for name, config in tournaments
            ]
            connection.commit()
        except HTTPException:
            connection.rollback()
            raise
        except ValidationError as exc:
            connection.rollback()
            raise HTTPException(
                status_code=422,
                detail=[error["msg"] for error in exc.errors()],
            ) from exc
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise HTTPException(
                status_code=409,
                detail="Tournament data changed while the drafts were being created. Reload the tool and try again.",
            ) from exc
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            LOG.exception("tournament creator batch failed")
            raise HTTPException(
                status_code=503,
                detail="The database could not save the tournament drafts. Try again.",
            ) from exc
        _publish_admin_change(web_app, request)
        return _json(
            {
                "tournaments": created,
                "message": f"Created {len(created)} tournament draft{'s' if len(created) != 1 else ''}.",
            },
            status_code=201,
        )

    @app.get("/api/admin/tools/invalidate-engine-games")
    def admin_invalidate_engine_games_context(
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        engine_ids_by_list = list_rating_list_engine_ids(connection)
        relevant_engine_ids = {
            engine_id
            for engine_ids in engine_ids_by_list.values()
            for engine_id in engine_ids
        }
        return _json(
            {
                "rating_lists": [
                    {
                        "id": rating_list.id,
                        "name": rating_list.name,
                        "engine_ids": engine_ids_by_list.get(rating_list.id, ()),
                    }
                    for rating_list in list_rating_lists(connection)
                ],
                "engines": [
                    {
                        "id": engine.id,
                        "name": engine.name,
                        "version": engine.version,
                        "author": engine.author,
                    }
                    for engine in list_engine_records(connection)
                    if engine.id in relevant_engine_ids
                ],
            }
        )

    @app.post("/api/admin/tools/invalidate-engine-games")
    def admin_invalidate_engine_games(
        payload: InvalidateRatingListEnginePayload,
        request: Request,
        connection: sqlite3.Connection = Depends(web_app._database),
    ):
        engine = get_engine_record(connection, payload.engine_id)
        if engine is None:
            raise HTTPException(status_code=404, detail="Engine version not found.")
        rating_list = get_rating_list(connection, payload.rating_list_id)
        if rating_list is None:
            raise HTTPException(status_code=404, detail="Rating list not found.")
        try:
            result = invalidate_rating_list_engine_games(
                connection,
                payload.rating_list_id,
                payload.engine_id,
            )
            connection.commit()
        except ValueError as exc:
            connection.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            LOG.exception(
                "rating-list engine invalidation failed rating_list_id=%s engine_id=%s",
                payload.rating_list_id,
                payload.engine_id,
            )
            raise HTTPException(
                status_code=503,
                detail="The database could not invalidate the engine games. Try again.",
            ) from exc
        _publish_admin_change(web_app, request)
        game_count = len(result.game_ids)
        return _json(
            {
                "message": f"Invalidated {game_count} game{'s' if game_count != 1 else ''} for {engine.name} {engine.version}.",
                "games_invalidated": game_count,
                "tournaments_affected": len(result.tournament_ids),
                "rating_lists_affected": len(result.rating_list_ids),
                "list_memberships_removed": result.list_memberships_removed,
            }
        )
