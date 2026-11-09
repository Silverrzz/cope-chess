WITH summaries AS (
    SELECT id, regexp_match(
        output,
        '(?im)^Nodes searched[ \t]*:[ \t]*([0-9]+)[ \t]*\r?\nNodes/second[ \t]*:[ \t]*([0-9]+)[ \t]*\r?$'
    ) AS counts
    FROM engine_benchmarks
    WHERE output LIKE '%Nodes searched%'
)
UPDATE engine_benchmarks benchmark
SET nps = summaries.counts[2]::bigint
FROM summaries
WHERE benchmark.id = summaries.id
  AND benchmark.nps = summaries.counts[1]::bigint
  AND summaries.counts[2]::bigint > 0
  AND benchmark.nps <> summaries.counts[2]::bigint;
