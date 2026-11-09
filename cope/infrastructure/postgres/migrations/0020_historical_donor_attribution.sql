ALTER TABLE hardware_donor_workers
    ADD COLUMN historical_anchor TEXT CHECK (historical_anchor IN ('ellie', 'styx')),
    ADD COLUMN historical_styx_cpu TEXT;

WITH ellie AS (
    SELECT id FROM workers WHERE lower(label) = 'desktop-c0mtgoo-wsl' ORDER BY id DESC LIMIT 1
), styx AS (
    SELECT id, lower(btrim(hw::jsonb ->> 'cpu_model')) AS cpu
    FROM workers
    WHERE lower(label) IN ('styxworker1', 'styxworker2') AND hw IS NOT NULL
), profiles AS (
    SELECT profile.worker_id,
           CASE WHEN profile.worker_id = ellie.id THEN 'ellie'
                WHEN profile.worker_id IN (SELECT id FROM styx) THEN 'styx' END AS anchor,
           (SELECT min(cpu) FROM styx WHERE cpu = lower(btrim(worker.hw::jsonb ->> 'cpu_model'))) AS styx_cpu
    FROM hardware_donor_workers profile
    CROSS JOIN ellie
    LEFT JOIN workers worker ON worker.id = profile.worker_id
)
UPDATE hardware_donor_workers profile
SET donor_name = CASE WHEN profiles.styx_cpu IS NOT NULL THEN 'Styx' ELSE 'Ellie' END,
    historical_anchor = profiles.anchor,
    historical_styx_cpu = profiles.styx_cpu
FROM profiles
WHERE profile.worker_id = profiles.worker_id;
