CREATE TABLE hardware_donor_workers (
    worker_id BIGINT PRIMARY KEY,
    worker_label TEXT NOT NULL,
    donor_name TEXT CHECK (donor_name IS NULL OR char_length(donor_name) BETWEEN 1 AND 80)
);

CREATE TABLE hardware_donor_games (
    game_id BIGINT PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
    worker_id BIGINT NOT NULL REFERENCES hardware_donor_workers(worker_id),
    thread_count INTEGER CHECK (thread_count > 0)
);

CREATE INDEX hardware_donor_games_worker_idx ON hardware_donor_games (worker_id);

INSERT INTO hardware_donor_workers (worker_id, worker_label)
SELECT id, label FROM workers;

INSERT INTO hardware_donor_games (game_id, worker_id)
SELECT game.id, assignment.worker_id
FROM games game
JOIN game_assignments assignment ON assignment.game_id = game.id
WHERE game.status = 'finished' AND game.result IS NOT NULL AND assignment.worker_id IS NOT NULL;
