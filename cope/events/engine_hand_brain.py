from __future__ import annotations

import json
from dataclasses import asdict

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field

from cope.core.models import (
    AdjudicationConfig,
    DrawAdjudicationRule,
    IncrementTimeControl,
    ResignAdjudicationRule,
    RoundRobinFormatOptions,
    TournamentConfig,
    TournamentFormat,
)
from cope.db import (
    create_game,
    create_tournament,
    get_engine,
    get_event,
    get_game,
    get_tournament,
    list_engine_records,
    list_games,
    list_opening_suites,
    list_suite_opening_ids,
    set_tournament_status,
)
from cope.db.events import (
    create_event,
    create_event_cast_member,
    create_event_contest,
    create_event_stage,
    get_event_by_slug,
    list_event_cast,
    list_event_contests,
    list_event_stages,
    set_event_published,
    set_event_status,
    set_event_stage_status,
    update_event_contest_state,
    utc_now,
)
from .registry import EventModule, register_event_module


MODULE_KEY = "engine-hand-brain"
DEFAULT_TEAMS = (
    ("Berserkless", "Berserk", "Reckless", 3764.0083),
    ("Torch & Ethereal", "Torch", "Ethereal", 3745.6237),
    ("Quantinocchio", "Quanticade", "Pawnocchio", 3743.03125),
    ("Stormphrax & Viridithas", "Stormphrax", "Viridithas", 3742.94555),
    ("Halossa", "Halogen", "Caissa", 3730.67925),
    ("HorsyChess", "Horsie", "PlentyChess", 3719.72165),
    ("Hobbarus", "Hobbes", "Icarus", 3677.838),
    ("PZCheran", "PZChessBot", "Tcheran", 3669.6989),
    ("Tarndall", "Tarnished", "Heimdall", 3666.728),
    ("June", "Prune", "JustBot", 3627.3197),
)
COLORS = (
    "#f59c69",
    "#d6abff",
    "#ec83bc",
    "#7fc6e8",
    "#a8d99b",
    "#e6c179",
    "#8cb5ff",
    "#7ed5ba",
    "#d9a195",
    "#b5aceb",
)
BRACKET = (
    ("P1", "Play-in", 7, 10),
    ("P2", "Play-in", 8, 9),
    ("Q1", "Quarterfinal", 1, "P2"),
    ("Q2", "Quarterfinal", 4, 5),
    ("Q3", "Quarterfinal", 2, "P1"),
    ("Q4", "Quarterfinal", 3, 6),
    ("S1", "Semifinal", "Q1", "Q2"),
    ("S2", "Semifinal", "Q3", "Q4"),
    ("F", "Final", "S1", "S2"),
)


def quad_time_control(index, config=None):
    config = config or {}
    divisor = 1 << min(max(0, index), 16)
    return IncrementTimeControl(
        initial_ms=max(1000, int(config.get("initial_ms", 60000)) // divisor),
        increment_ms=max(1, int(config.get("increment_ms", 600)) // divisor),
    )


def quad_score(games, anchor_a):
    score = [0, 0]
    for game in games:
        if game.status != "finished" or game.result is None:
            continue
        white = {"1-0": 2, "0-1": 0, "1/2-1/2": 1}.get(game.result)
        if white is None:
            raise ValueError("unsupported hand and brain result")
        a = white if game.white_engine_id == anchor_a else 2 - white
        score[0] += a
        score[1] += 2 - a
    return score


def initialize_bracket(connection, event_id):
    event = get_event(connection, event_id)
    if event is None or event.handler_key != MODULE_KEY:
        raise ValueError("Hand and brain event not found")
    if list_event_contests(connection, event_id):
        return
    stages = {
        stage.title: stage.id for stage in list_event_stages(connection, event_id)
    }
    for position, title in enumerate(("Play-in", "Quarterfinal", "Semifinal", "Final")):
        if title not in stages:
            stages[title] = create_event_stage(
                connection,
                event_id,
                stage_key=title.lower(),
                title=title,
                position=position,
            )
    cast = list_event_cast(connection, event_id)
    teams = [
        member for member in cast if member.kind == "team" and member.parent_id is None
    ]
    if teams and len(teams) != 10:
        raise ValueError("The bracket requires exactly ten teams")
    if not teams:
        engines = list_engine_records(connection)
        supported = {
            "Berserk",
            "Caissa",
            "Stormphrax",
            "Ethereal",
            "Icarus",
            "Horsie",
            "Heimdall",
            "Torch",
        }
        expected = {"Caissa": "v2.0", "Tcheran": "v14.0", "JustBot": "v0.5.0"}
        for seed, (name, first, second, elo) in enumerate(DEFAULT_TEAMS, 1):
            team_id = create_event_cast_member(
                connection,
                event_id,
                member_key=f"team-{seed}",
                kind="team",
                display_name=name,
                position=(seed - 1) * 3,
                accent_color=COLORS[seed - 1],
                metadata={
                    "seed": seed,
                    "rapid_elo": elo,
                    "secondary_color": COLORS[seed - 1],
                },
            )
            for order, engine_name in enumerate((first, second)):
                matches = [engine for engine in engines if engine.name == engine_name]
                engine = (
                    next(
                        (
                            engine
                            for engine in matches
                            if engine.version == expected.get(engine_name)
                        ),
                        None,
                    )
                    if engine_name in expected
                    else (matches[0] if matches else None)
                )
                create_event_cast_member(
                    connection,
                    event_id,
                    member_key=f"engine-{seed}-{order}",
                    kind="engine",
                    display_name=engine_name,
                    position=(seed - 1) * 3 + order + 1,
                    parent_id=team_id,
                    engine_version_id=None if engine is None else engine.id,
                    metadata={
                        "threads": 1,
                        "hash_mb": 256,
                        "relay_order": order,
                        "searchmoves_confirmed": engine_name in supported
                        and engine is not None,
                    },
                )
    for position, (key, stage, a, b) in enumerate(BRACKET):
        create_event_contest(
            connection,
            event_id,
            contest_key=key.lower(),
            title=key,
            position=position,
            summary=stage,
            stage_id=stages[stage],
            state={"sources": [a, b], "quads": [], "winner_team_id": None},
        )


def _resolve_teams(connection, event_id, contest):
    cast = list_event_cast(connection, event_id)
    seeds = {
        int(team.metadata.get("seed", team.position + 1)): team.id
        for team in cast
        if team.kind == "team"
    }
    winners = {
        item.title: item.state.get("winner_team_id")
        for item in list_event_contests(connection, event_id)
    }
    return [
        seeds.get(source) if isinstance(source, int) else winners.get(source)
        for source in contest.state["sources"]
    ]


def _opening_suite(connection):
    suites = [
        suite
        for suite in list_opening_suites(connection)
        if suite.variant == "standard"
        and "lichess" in suite.name.lower()
        and "uho" in suite.name.lower()
    ]
    if not suites:
        raise ValueError(
            "The Lichess UHO opening book must be installed before starting"
        )
    return suites[0]


def _create_quad(connection, event, contest):
    from .engine_relay import _team_members

    team_ids = _resolve_teams(connection, event.id, contest)
    if any(team_id is None for team_id in team_ids):
        raise ValueError("This match is waiting for its previous round")
    rosters = [_team_members(connection, event.id, team_id) for team_id in team_ids]
    if any(len(roster) != 2 for roster in rosters):
        raise ValueError("Each team needs exactly two engines")
    if any(
        member.engine_version_id is None
        or not member.metadata.get("searchmoves_confirmed")
        for roster in rosters
        for member in roster
    ):
        raise ValueError(
            "Every engine must have a selected build with confirmed searchmoves support"
        )
    anchors = [int(roster[0].engine_version_id) for roster in rosters]
    all_ids = [member.engine_version_id for roster in rosters for member in roster]
    if len(set(all_ids)) != 4:
        raise ValueError("Each matchup needs four different engines")
    suite = _opening_suite(connection)
    opening_ids = list_suite_opening_ids(
        connection,
        suite.id,
        limit=1,
        start_position=contest.position * 997 + len(contest.state["quads"]),
    )
    if not opening_ids:
        raise ValueError("The Lichess UHO opening book is empty")
    index = len(contest.state["quads"])
    config = TournamentConfig(
        format=TournamentFormat.ROUND_ROBIN,
        format_options=RoundRobinFormatOptions(),
        participants=anchors,
        time_control=quad_time_control(index, event.config.get("time_control")),
        concurrency=1,
        opening_suite_id=suite.id,
        adjudication=AdjudicationConfig(
            draw=DrawAdjudicationRule(
                max_abs_cp=10, consecutive_plies=8, min_fullmove=40
            ),
            resign=ResignAdjudicationRule(min_abs_cp=800, consecutive_plies=6),
        ),
        rated=False,
        lag_compensation_ms=0,
        engine_threads=1,
        engine_hash_mb=256,
    )
    title = f"Hand & Brain · {contest.title} · Quad {index + 1}"
    tournament_id = create_tournament(connection, title, config)
    cursor = connection.execute(
        "INSERT INTO engine_relay_fixtures (event_id, tournament_id, team_a_id, team_b_id, anchor_a_engine_id, anchor_b_engine_id, title, position, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event.id,
            tournament_id,
            *team_ids,
            *anchors,
            title,
            int(
                connection.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 AS position FROM engine_relay_fixtures WHERE event_id = ?",
                    (event.id,),
                ).fetchone()["position"]
            ),
            utc_now(),
        ),
    )
    fixture_id = int(cursor.lastrowid)
    for position, (team_id, anchor) in enumerate(zip(team_ids, anchors, strict=True)):
        connection.execute(
            "INSERT INTO engine_relay_fixture_teams (fixture_id, team_id, anchor_engine_id, position) VALUES (?, ?, ?, ?)",
            (fixture_id, team_id, anchor, position),
        )
    opening_id = opening_ids[(contest.position * 997 + index) % len(opening_ids)]
    create_game(
        connection,
        tournament_id=tournament_id,
        round=1,
        pair_index=1,
        white_engine_id=anchors[0],
        black_engine_id=anchors[1],
        opening_id=opening_id,
        game_number=1,
    )
    state = {
        **contest.state,
        "team_ids": team_ids,
        "quads": [*contest.state["quads"], tournament_id],
    }
    update_event_contest_state(connection, contest.id, status="live", state=state)
    if contest.stage_id is not None:
        set_event_stage_status(connection, contest.stage_id, "active")
    set_tournament_status(connection, tournament_id, "running")
    return tournament_id


def start_next_match(connection, event_id):
    connection.execute(
        "SELECT id FROM events WHERE id = ? FOR UPDATE", (event_id,)
    ).fetchone()
    event = get_event(connection, event_id)
    contests = list_event_contests(connection, event_id)
    for stage in list_event_stages(connection, event_id):
        stage_matches = [match for match in contests if match.stage_id == stage.id]
        if (
            stage_matches
            and all(match.status == "completed" for match in stage_matches)
            and stage.status != "completed"
        ):
            set_event_stage_status(connection, stage.id, "completed")
    if any(contest.status == "live" for contest in contests):
        return None
    contest = next((item for item in contests if item.status == "pending"), None)
    if contest is None:
        set_event_status(connection, event_id, "completed")
        return None
    tournament_id = _create_quad(connection, event, contest)
    set_event_status(connection, event_id, "live")
    return tournament_id


def start_scheduled_hand_brain_events(connection, now):
    started = []
    for row in connection.execute(
        "SELECT id FROM events WHERE handler_key = ? AND status = 'scheduled' AND scheduled_start_at IS NOT NULL AND scheduled_start_at <= ?",
        (MODULE_KEY, now),
    ).fetchall():
        event = get_event(connection, row["id"])
        if not event.config.get("auto_start"):
            continue
        roster = [
            member
            for member in list_event_cast(connection, event.id)
            if member.kind == "engine"
        ]
        if len(roster) != 20 or any(
            member.engine_version_id is None
            or not member.metadata.get("searchmoves_confirmed")
            for member in roster
        ):
            continue
        if len({member.engine_version_id for member in roster}) != 20:
            continue
        _opening_suite(connection)
        tournament_id = start_next_match(connection, event.id)
        if tournament_id is not None:
            started.append(tournament_id)
    return started


def advance_hand_brain(connection, tournament):
    from cope.runner.scheduler import TournamentAdvance

    row = connection.execute(
        "SELECT fixture.*, event.handler_key FROM engine_relay_fixtures fixture JOIN events event ON event.id = fixture.event_id WHERE fixture.tournament_id = ?",
        (tournament.id,),
    ).fetchone()
    if row is None or row["handler_key"] != MODULE_KEY:
        return None
    event_id = int(row["event_id"])
    connection.execute(
        "SELECT id FROM events WHERE id = ? FOR UPDATE", (event_id,)
    ).fetchone()
    contest = next(
        (
            item
            for item in list_event_contests(connection, event_id)
            if tournament.id in item.state.get("quads", [])
        ),
        None,
    )
    if contest is None:
        raise RuntimeError("Hand and brain tournament has no bracket match")
    if contest.status == "completed" or contest.state["quads"][-1] != tournament.id:
        return TournamentAdvance(complete=True)
    games = list_games(connection, tournament.id)
    if not games or any(game.status != "finished" for game in games):
        return TournamentAdvance()
    score = quad_score(games, int(row["anchor_a_engine_id"]))
    if max(score) > 4:
        winner = contest.state["team_ids"][0 if score[0] > score[1] else 1]
        update_event_contest_state(
            connection,
            contest.id,
            status="completed",
            result=f"{score[0] / 2:g}–{score[1] / 2:g}",
            state={**contest.state, "winner_team_id": winner},
        )
        set_tournament_status(connection, tournament.id, "finished")
        start_next_match(connection, event_id)
        return TournamentAdvance(complete=True)
    if len(games) == 4:
        if score != [4, 4]:
            raise RuntimeError("A completed quad must have a winner or be tied 2–2")
        set_tournament_status(connection, tournament.id, "finished")
        _create_quad(connection, get_event(connection, event_id), contest)
        return TournamentAdvance(complete=True)
    next_number = len(games) + 1
    a, b = int(row["anchor_a_engine_id"]), int(row["anchor_b_engine_id"])
    create_game(
        connection,
        tournament_id=tournament.id,
        round=1,
        pair_index=(next_number - 1) // 2 + 1,
        white_engine_id=a if next_number % 2 else b,
        black_engine_id=b if next_number % 2 else a,
        opening_id=games[0].opening_id,
        game_number=next_number,
    )
    return TournamentAdvance(created_games=1)


def _payload(connection, event):
    cast = list_event_cast(connection, event.id)
    teams = [
        {
            "id": team.id,
            "name": team.display_name,
            "seed": team.metadata.get("seed", team.position + 1),
            "color": team.accent_color,
            "elo": team.metadata.get("rapid_elo"),
            "members": [
                {
                    "id": member.id,
                    "name": member.display_name,
                    "engine_id": member.engine_version_id,
                    "ready": bool(member.metadata.get("searchmoves_confirmed")),
                }
                for member in cast
                if member.parent_id == team.id
            ],
        }
        for team in cast
        if team.kind == "team"
    ]
    matches = []
    for contest in list_event_contests(connection, event.id):
        quads = []
        for tournament_id in contest.state.get("quads", []):
            tournament = get_tournament(connection, tournament_id)
            if tournament is None:
                continue
            games = list_games(connection, tournament_id)
            anchor = tournament.config.participants[0]
            quads.append(
                {
                    "tournament_id": tournament_id,
                    "time_control": tournament.config.time_control.model_dump(),
                    "score": [value / 2 for value in quad_score(games, anchor)],
                    "games": [asdict(game) for game in games],
                }
            )
        matches.append(
            {
                "id": contest.id,
                "key": contest.title,
                "stage": contest.summary,
                "sources": contest.state.get("sources", []),
                "team_ids": _resolve_teams(connection, event.id, contest),
                "status": contest.status,
                "winner_team_id": contest.state.get("winner_team_id"),
                "result": contest.result,
                "quads": quads,
            }
        )
    return {
        "teams": teams,
        "matches": matches,
        "rules": {
            "initial_ms": quad_time_control(
                0, event.config.get("time_control")
            ).initial_ms,
            "increment_ms": quad_time_control(
                0, event.config.get("time_control")
            ).increment_ms,
            "minimum_initial_ms": 1000,
            "minimum_increment_ms": 1,
            "threads": 1,
            "concurrency": 1,
            "book": "Lichess UHO",
        },
    }


class MemberBuildPayload(BaseModel):
    engine_id: int = Field(gt=0)
    searchmoves_confirmed: bool


def _admin_payload(connection, event):
    payload = _payload(connection, event)
    payload["engine_options"] = [
        {"id": engine.id, "name": engine.name, "version": engine.version}
        for engine in list_engine_records(connection)
    ]
    return payload


def _register_api(app):
    from cope.web import app as web_app
    from .engine_relay import _json, _publish_change

    def require_event(connection, event_id):
        event = get_event(connection, event_id)
        if event is None or event.handler_key != MODULE_KEY:
            raise HTTPException(404, "Hand and brain event not found")
        return event

    @app.post("/api/admin/events/{event_id}/hand-brain/prepare")
    def prepare(event_id: int, request: Request, connection=Depends(web_app._database)):
        require_event(connection, event_id)
        connection.execute(
            "SELECT id FROM events WHERE id = ? FOR UPDATE", (event_id,)
        ).fetchone()
        initialize_bracket(connection, event_id)
        connection.commit()
        _publish_change(request, event_id)
        return _json({"message": "Ten-team bracket prepared"})

    @app.put("/api/admin/events/{event_id}/hand-brain/members/{member_id}")
    def select_build(
        event_id: int,
        member_id: int,
        payload: MemberBuildPayload,
        request: Request,
        connection=Depends(web_app._database),
    ):
        event = require_event(connection, event_id)
        connection.execute(
            "SELECT id FROM events WHERE id = ? FOR UPDATE", (event_id,)
        ).fetchone()
        if any(
            contest.state.get("quads")
            for contest in list_event_contests(connection, event_id)
        ):
            raise HTTPException(409, "Builds are locked after the event starts")
        member = next(
            (
                item
                for item in list_event_cast(connection, event_id)
                if item.id == member_id and item.kind == "engine"
            ),
            None,
        )
        engine = get_engine(connection, payload.engine_id)
        if member is None or engine is None or engine.name != member.display_name:
            raise HTTPException(422, "Select a build of this engine")
        metadata = {
            **member.metadata,
            "searchmoves_confirmed": payload.searchmoves_confirmed,
        }
        connection.execute(
            "UPDATE event_cast_members SET engine_version_id = ?, metadata = ? WHERE id = ? AND event_id = ?",
            (payload.engine_id, json.dumps(metadata), member_id, event.id),
        )
        connection.commit()
        _publish_change(request, event_id)
        return _json({"message": "Engine build updated"})

    @app.post("/api/admin/events/{event_id}/hand-brain/start")
    def start(event_id: int, request: Request, connection=Depends(web_app._database)):
        require_event(connection, event_id)
        initialize_bracket(connection, event_id)
        try:
            _opening_suite(connection)
            roster = [
                member
                for member in list_event_cast(connection, event_id)
                if member.kind == "engine"
            ]
            if len(roster) != 20 or any(
                member.engine_version_id is None
                or not member.metadata.get("searchmoves_confirmed")
                for member in roster
            ):
                raise ValueError(
                    "All twenty engines need builds with confirmed searchmoves support before the event starts"
                )
            if len({member.engine_version_id for member in roster}) != 20:
                raise ValueError("Each engine may appear on only one team")
            tournament_id = start_next_match(connection, event_id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        connection.commit()
        _publish_change(request, event_id)
        return _json(
            {
                "message": "Hand and brain event ready to play",
                "tournament_id": tournament_id,
            }
        )

    @app.get("/api/events/{slug}/hand-brain/games/{game_id}")
    def game_searches(
        slug: str, game_id: int, request: Request, connection=Depends(web_app._database)
    ):
        from cope.web.api import _require_viewable_event

        event = _require_viewable_event(connection, slug, request)
        game = get_game(connection, game_id)
        if (
            event.handler_key != MODULE_KEY
            or game is None
            or not web_app._event_has_tournament(
                connection, event.id, game.tournament_id
            )
        ):
            raise HTTPException(404, "Hand and brain game not found")
        searches = [
            {
                **dict(row),
                "analysis": json.loads(row["analysis"]),
                "clocks": json.loads(row["clocks"]),
            }
            for row in connection.execute(
                "SELECT * FROM hand_brain_searches WHERE game_id = ? ORDER BY ply, role",
                (game_id,),
            )
        ]
        return _json({"searches": searches})


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


register_event_module(
    EventModule(
        key=MODULE_KEY,
        label="Engine Hand&Brain",
        version=1,
        provision=_provision,
        public_payload=_payload,
        admin_payload=_admin_payload,
        register_api=_register_api,
    )
)
