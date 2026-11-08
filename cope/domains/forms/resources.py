from __future__ import annotations

import re

from cope.application.errors import ApplicationError
from cope.db import get_game, get_opening_position, get_rating_list, get_tournament
from cope.db.events import list_event_tournament_links

RESOURCE_ID = re.compile(r"[1-9][0-9]{0,14}")


def resource_id(value: str) -> int:
    if not RESOURCE_ID.fullmatch(value):
        raise ApplicationError("invalid_resource", "Choose a valid Cope resource.", 422)
    return int(value)


def public_tournament(connection, tournament_id: int):
    tournament = get_tournament(connection, tournament_id)
    if tournament is None or tournament.status == "draft" or list_event_tournament_links(connection, tournament_id=tournament_id):
        raise ApplicationError("resource_unavailable", "This tournament is unavailable.", 404)
    return tournament


def public_game(connection, tournament_id: int, game_id: int):
    tournament = public_tournament(connection, tournament_id)
    game = get_game(connection, game_id)
    if game is None or game.tournament_id != tournament_id or not game.record_eligible or game.status != "finished":
        raise ApplicationError("resource_unavailable", "This game is unavailable.", 404)
    return tournament, game


def validate_public_resource(connection, kind: str, value: str) -> str:
    if kind == "chess_game":
        tournament, separator, game = value.partition("/")
        if not separator:
            raise ApplicationError("invalid_resource", "Choose a tournament and a game.", 422)
        tournament_id, game_id = resource_id(tournament), resource_id(game)
        public_game(connection, tournament_id, game_id)
        return f"{tournament_id}/{game_id}"
    selected = resource_id(value)
    if get_rating_list(connection, selected) is None:
        raise ApplicationError("resource_unavailable", "This rating list is unavailable.", 404)
    return str(selected)


def resource_choices(connection, kind: str, query: str, tournament_id: int | None = None) -> list[dict]:
    text = query.casefold().strip()
    if kind == "game":
        if tournament_id is None:
            return []
        public_tournament(connection, tournament_id)
        rows = connection.execute(
            """SELECT game.id, w.name AS white, b.name AS black
               FROM games game
               JOIN engine_versions wv ON wv.id = game.white_engine_id JOIN engines w ON w.id = wv.engine_id
               JOIN engine_versions bv ON bv.id = game.black_engine_id JOIN engines b ON b.id = bv.engine_id
               WHERE game.tournament_id = ? AND game.record_eligible = 1 AND game.status = 'finished'
                 AND (strpos(lower(w.name || ' ' || b.name), ?) > 0 OR game.id::text = ?)
               ORDER BY game.id DESC LIMIT 25""",
            (tournament_id, text, text),
        )
        return [{"id": f"{tournament_id}/{row['id']}", "label": f"#{row['id']} · {row['white']} vs {row['black']}"} for row in rows]
    if kind == "rating_list":
        rows = connection.execute(
            "SELECT id, name FROM rating_lists WHERE strpos(lower(name), ?) > 0 OR id::text = ? ORDER BY name, id LIMIT 25",
            (text, text),
        )
    else:
        event_ids = sorted({int(row["tournament_id"]) for row in list_event_tournament_links(connection)})
        rows = connection.execute(
            """SELECT id, name FROM tournaments WHERE status <> 'draft' AND NOT (id = ANY(?::bigint[]))
               AND (strpos(lower(name), ?) > 0 OR id::text = ?) ORDER BY id DESC LIMIT 25""",
            (event_ids, text, text),
        )
    return [{"id": str(row["id"]), "label": row["name"]} for row in rows]


def game_embed(connection, tournament_id: int, game_id: int) -> dict:
    tournament, game = public_game(connection, tournament_id, game_id)
    players = connection.execute(
        """SELECT version.id, engine.name, version.version FROM engine_versions version
           JOIN engines engine ON engine.id = version.engine_id WHERE version.id = ANY(?::bigint[])""",
        ([game.white_engine_id, game.black_engine_id],),
    )
    names = {row["id"]: f"{row['name']} {row['version']}".strip() for row in players}
    opening = get_opening_position(connection, game.opening_id)
    moves = list(connection.execute("SELECT uci, san FROM moves WHERE game_id = ? ORDER BY ply LIMIT 4096", (game.id,)))
    return {
        "title": f"{names.get(game.white_engine_id, 'White')} vs {names.get(game.black_engine_id, 'Black')}",
        "tournament": tournament.name,
        "url": f"/tournaments/{tournament_id}?game_id={game_id}",
        "variant": tournament.config.variant,
        "fen": opening.start_fen if opening else "startpos",
        "result": game.result,
        "moves": [move["uci"] for move in moves],
        "san": [move["san"] for move in moves],
    }


def rating_embed(connection, rating_list_id: int) -> dict:
    rating_list = get_rating_list(connection, rating_list_id)
    if rating_list is None:
        raise ApplicationError("resource_unavailable", "This rating list is unavailable.", 404)
    rows = connection.execute(
        """SELECT engine.name, version.version, rating.elo, rating.error_margin
           FROM rating_list_ratings rating JOIN engine_versions version ON version.id = rating.engine_id
           JOIN engines engine ON engine.id = version.engine_id
           WHERE rating.rating_list_id = ? ORDER BY rating.elo DESC, engine.name, version.version, version.id LIMIT 10""",
        (rating_list_id,),
    )
    return {"title": rating_list.name, "url": f"/ratings?rating_list_id={rating_list_id}", "rows": list(rows)}
