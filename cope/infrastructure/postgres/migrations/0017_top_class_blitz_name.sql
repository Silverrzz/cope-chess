UPDATE rating_lists
SET name = 'Top-Class Blitz'
WHERE name = 'Monthly Cope Top 15' AND hardware_scaled AND monthly_refresh;

CREATE INDEX games_pending_dispatch_order
ON games (tournament_id, ((game_number - 1) / 2), round, pair_index, game_number, id)
WHERE status = 'pending';
