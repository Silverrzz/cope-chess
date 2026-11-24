ALTER TABLE moves ADD COLUMN position_fen TEXT;

CREATE TABLE secret_queen_pairs (
  tournament_id BIGINT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
  round INTEGER NOT NULL,
  pair_index INTEGER NOT NULL,
  white_file INTEGER NOT NULL CHECK (white_file BETWEEN 0 AND 7),
  black_file INTEGER NOT NULL CHECK (black_file BETWEEN 0 AND 7),
  PRIMARY KEY (tournament_id, round, pair_index)
);

CREATE TABLE secret_queen_event_tournaments (
  event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  contest_id BIGINT NOT NULL REFERENCES event_contests(id) ON DELETE CASCADE,
  tournament_id BIGINT PRIMARY KEY REFERENCES tournaments(id) ON DELETE CASCADE
);

CREATE INDEX secret_queen_event_tournaments_event ON secret_queen_event_tournaments(event_id, contest_id);
