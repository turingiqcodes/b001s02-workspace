# Dimension: Performance & Efficiency

Focus: will this PR make something measurably slower, costlier, or fall over at scale?

## Checklist

- **Database:** N+1 queries (query inside a loop), missing `WHERE`/`LIMIT` on large
  tables, `SELECT *` on wide tables in hot paths, missing index for a new filter or
  join column, loading whole tables into memory, row-by-row inserts where a bulk
  insert/COPY fits, transactions held open across network calls.
- **Algorithms:** O(n²) or worse on inputs that can be large (nested loops over
  collections, `list` membership checks inside loops — use a `set`/`dict`),
  repeated sorting, recomputing the same value in a loop.
- **I/O:** synchronous/blocking calls in async code, sequential network calls that
  could be batched or parallel, reading huge files fully into memory, no streaming.
- **Caching:** cache added without invalidation or bound, cache missing on an
  obviously repeated expensive call.
- **Memory:** unbounded lists/queues/dicts that grow with traffic, large objects
  captured in closures, leaks from listeners/handles not released.
- **Frontend:** re-rendering in loops, unthrottled event handlers, huge bundles
  from new imports, DOM queries inside loops.
- **Data pipelines (Airflow/Spark/ETL):** collecting distributed data to the
  driver, non-incremental full reloads, heavy work at DAG import/parse time.

## How to work

1. Estimate realistic input sizes from context (table names, API limits, comments).
2. Report only when the cost is meaningful at those sizes. Micro-optimizations in
   cold code are `nit` at most — usually don't report them.
3. In `failure_scenario`, quantify where you can ("10k rows → 10k queries").
