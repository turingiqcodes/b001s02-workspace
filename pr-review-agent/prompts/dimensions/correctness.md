# Dimension: Correctness & Bugs

Focus: does the changed code do what it intends, on every path a real caller can reach?

## Checklist

- **Logic errors:** inverted conditions, wrong operator (`and`/`or`, `<`/`<=`),
  off-by-one in ranges/slices/pagination, wrong variable used (copy-paste bugs).
- **Null / empty / missing:** `None`/`null`/`undefined` dereference, empty
  collections, missing dict keys, optional fields from APIs or DB rows.
- **Error handling:** exceptions swallowed (`except: pass`, empty `catch`), errors
  logged but execution continues with bad state, wrong exception type caught,
  missing cleanup on failure (transactions not rolled back, files/handles left open).
- **State & concurrency:** shared mutable state, race conditions, check-then-act,
  async functions not awaited, missing locks, non-idempotent retries.
- **Contracts:** changed function signatures/return types whose callers were not
  updated (grep for every caller!), changed defaults, changed API response shapes,
  DB schema changes without matching code/migration.
- **Data handling:** integer overflow/truncation, float money math, timezone-naive
  datetimes, encoding issues, wrong units.
- **Resource handling:** connections/cursors/files not closed, unbounded growth.
- **Config / deploy:** hard-coded local paths, env vars read but never set, new
  dependencies missing from requirements/package files.

## How to work

1. For each changed function, list its callers (`grep -rn`) and confirm they still
   satisfy the new contract.
2. Walk the unhappy paths: empty input, missing record, network failure, duplicate call.
3. Only report a bug when you can describe the concrete input/state that triggers it.
