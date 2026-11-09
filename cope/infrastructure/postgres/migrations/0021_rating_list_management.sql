ALTER TABLE rating_lists
    ADD COLUMN description TEXT NOT NULL DEFAULT '',
    ADD COLUMN display_order INTEGER CHECK (display_order > 0);

ALTER TABLE tournaments ADD COLUMN historical BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE historical_tournament_games (
    tournament_id BIGINT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    game_id BIGINT NOT NULL REFERENCES games(id) ON DELETE RESTRICT,
    PRIMARY KEY (tournament_id, game_id)
);

CREATE INDEX idx_historical_tournament_games_source ON historical_tournament_games(game_id);

CREATE VIEW tournament_games AS
SELECT id, tournament_id, round, pair_index, white_engine_id, black_engine_id,
       match_id, game_number, tiebreak_kind, opening_id, status, record_eligible,
       result, termination, pgn, white_hw, black_hw, started_at, finished_at,
       NULL::bigint AS source_tournament_id
FROM games
UNION ALL
SELECT game.id, archive.tournament_id, game.round, game.pair_index,
       game.white_engine_id, game.black_engine_id, game.match_id, game.game_number,
       game.tiebreak_kind, game.opening_id, game.status, game.record_eligible,
       game.result, game.termination, game.pgn, game.white_hw, game.black_hw,
       game.started_at, game.finished_at, game.tournament_id
FROM historical_tournament_games archive
JOIN games game ON game.id = archive.game_id;
