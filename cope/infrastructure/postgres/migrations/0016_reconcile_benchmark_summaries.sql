WITH summaries AS (
    SELECT id, regexp_match(
        output,
        '(?im)^Nodes searched[ \t]*:[ \t]*[0-9]+[ \t]*\r?\nNodes/second[ \t]*:[ \t]*([0-9]+)[ \t]*\r?$'
    ) AS measured
    FROM engine_benchmarks
    WHERE output LIKE '%Nodes searched%' AND output LIKE '%Nodes/second%'
)
UPDATE engine_benchmarks benchmark
SET nps = summaries.measured[1]::bigint
FROM summaries
WHERE benchmark.id = summaries.id
  AND summaries.measured[1]::bigint > 0
  AND benchmark.nps <> summaries.measured[1]::bigint;
