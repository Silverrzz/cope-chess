DO $$
DECLARE
    ellie_worker BIGINT;
BEGIN
    SELECT id INTO ellie_worker
    FROM workers
    WHERE lower(label) = 'desktop-c0mtgoo-wsl'
    ORDER BY id DESC
    LIMIT 1;

    IF ellie_worker IS NULL THEN
        RETURN;
    END IF;

    INSERT INTO hardware_donor_workers (worker_id, worker_label)
    SELECT id, label FROM workers
    ON CONFLICT (worker_id) DO NOTHING;

    WITH styx_cpus AS (
        SELECT DISTINCT lower(btrim(hw::jsonb ->> 'cpu_model')) AS cpu
        FROM workers
        WHERE lower(label) IN ('styxworker1', 'styxworker2') AND hw IS NOT NULL
    )
    UPDATE hardware_donor_workers profile
    SET donor_name = CASE WHEN EXISTS (
        SELECT 1
        FROM workers worker
        JOIN styx_cpus styx ON styx.cpu = lower(btrim(worker.hw::jsonb ->> 'cpu_model'))
        WHERE worker.id = profile.worker_id
    ) THEN 'Styx' ELSE 'Ellie' END;

    WITH styx_workers AS MATERIALIZED (
        SELECT id, lower(btrim(hw::jsonb ->> 'cpu_model')) AS cpu
        FROM workers
        WHERE lower(label) IN ('styxworker1', 'styxworker2') AND hw IS NOT NULL
    )
    INSERT INTO hardware_donor_games (game_id, worker_id)
    SELECT game.id, COALESCE(
        styx.id,
        CASE WHEN profile.donor_name = 'Ellie' THEN profile.worker_id END,
        ellie_worker
    )
    FROM games game
    LEFT JOIN game_assignments assignment ON assignment.game_id = game.id
    LEFT JOIN hardware_donor_games credit ON credit.game_id = game.id
    LEFT JOIN hardware_donor_workers profile
        ON profile.worker_id = COALESCE(credit.worker_id, assignment.worker_id)
    LEFT JOIN workers worker ON worker.id = profile.worker_id
    LEFT JOIN LATERAL (
        SELECT min(lower(btrim(hardware.hw::jsonb ->> 'cpu_model'))) AS cpu
        FROM game_clock_calibrations calibration
        JOIN benchmark_hardware hardware ON hardware.hardware_key = calibration.worker_hardware_key
        WHERE calibration.game_id = game.id
          AND (assignment.assignment_key IS NULL OR calibration.assignment_key = assignment.assignment_key)
        HAVING COUNT(DISTINCT lower(btrim(hardware.hw::jsonb ->> 'cpu_model'))) = 1
    ) calibrated ON EXISTS (SELECT 1 FROM styx_workers)
    LEFT JOIN LATERAL (
        SELECT candidate.id
        FROM styx_workers candidate
        WHERE candidate.cpu = COALESCE(
            NULLIF(calibrated.cpu, ''),
            NULLIF(lower(btrim(CASE WHEN pg_input_is_valid(game.white_hw, 'jsonb')
                THEN game.white_hw::jsonb ->> 'cpu_model' ELSE game.white_hw END)), ''),
            NULLIF(lower(btrim(CASE WHEN pg_input_is_valid(game.black_hw, 'jsonb')
                THEN game.black_hw::jsonb ->> 'cpu_model' ELSE game.black_hw END)), ''),
            lower(btrim(worker.hw::jsonb ->> 'cpu_model'))
        )
        ORDER BY (candidate.id = profile.worker_id) DESC, candidate.id
        LIMIT 1
    ) styx ON TRUE
    WHERE game.status = 'finished' AND game.result IS NOT NULL
    ON CONFLICT (game_id) DO UPDATE SET worker_id = EXCLUDED.worker_id
    WHERE hardware_donor_games.worker_id IS DISTINCT FROM EXCLUDED.worker_id;
END;
$$;
