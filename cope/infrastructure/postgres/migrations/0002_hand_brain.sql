CREATE TABLE IF NOT EXISTS hand_brain_searches (
    game_id BIGINT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    ply INTEGER NOT NULL CHECK (ply >= 0),
    engine_id BIGINT NOT NULL REFERENCES engine_versions(id),
    role TEXT NOT NULL CHECK (role IN ('brain', 'hand')),
    side TEXT NOT NULL CHECK (side IN ('white', 'black')),
    root_fen TEXT NOT NULL,
    bestmove TEXT NOT NULL,
    analysis TEXT NOT NULL,
    clocks TEXT NOT NULL,
    PRIMARY KEY (game_id, ply, engine_id)
);
