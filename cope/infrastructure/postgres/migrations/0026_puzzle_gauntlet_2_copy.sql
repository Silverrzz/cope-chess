UPDATE events
SET subtitle = CASE WHEN subtitle = 'Engine survival, one puzzle at a time' THEN '' ELSE subtitle END,
    summary = CASE WHEN summary = 'Engines face the August filtered suite in order of estimated puzzle difficulty.' THEN '' ELSE summary END,
    revision = revision + 1,
    updated_at = to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"')
WHERE slug = 'puzzle-gauntlet-2'
  AND handler_key = 'puzzle-gauntlet-2'
  AND handler_version = 1
  AND (subtitle = 'Engine survival, one puzzle at a time'
       OR summary = 'Engines face the August filtered suite in order of estimated puzzle difficulty.');
