# Dimension: Code Quality & Maintainability

Focus: will the next engineer be able to understand, change, and trust this code?

## Checklist

- **Duplication:** logic copy-pasted from elsewhere in the repo instead of reusing
  an existing helper (grep for it), or duplicated within the PR.
- **Complexity:** functions doing several unrelated things, deep nesting that early
  returns would flatten, boolean flag parameters that switch behavior.
- **Naming & clarity:** names that mislead about what the value is or does, magic
  numbers/strings that need a named constant, dead code or commented-out blocks.
- **Consistency:** deviates from the conventions of the surrounding code (error
  handling style, logging, module layout, typing) without reason.
- **Abstractions:** premature abstraction for a single use, or the opposite — a
  third copy of something that clearly wants a helper.
- **API design:** leaky abstractions, mutable default arguments, public surface
  that exposes internals, unclear ownership of resources.
- **Docs & comments:** public functions with non-obvious behavior and no docstring;
  comments that contradict the code; outdated README/config docs touched by the change.
- **Hygiene:** debug prints, stray files (checkpoints, zips, local paths), TODOs
  without context, unrelated changes bundled into the PR.

## How to work

1. Compare against neighbouring code — match its idioms, don't impose your own.
2. Prefer one consolidated finding over many small ones about the same pattern.
3. Most findings here are `low`; use `medium` only when the issue will very likely
   cause a future bug (e.g. two divergent copies of business logic).
