CREATE TABLE secret_queen_game_secrets (
  game_id BIGINT PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
  white_file INTEGER NOT NULL CHECK (white_file BETWEEN 0 AND 7),
  black_file INTEGER NOT NULL CHECK (black_file BETWEEN 0 AND 7)
);

INSERT INTO secret_queen_game_secrets (game_id, white_file, black_file)
SELECT game.id, pair.white_file, pair.black_file
FROM secret_queen_pairs pair
JOIN games game ON (game.tournament_id, game.round, game.pair_index) = (pair.tournament_id, pair.round, pair.pair_index)
WHERE game.started_at IS NOT NULL OR game.status IN ('live', 'finished')
   OR EXISTS (SELECT 1 FROM game_pause_checkpoints checkpoint WHERE checkpoint.game_id = game.id);
