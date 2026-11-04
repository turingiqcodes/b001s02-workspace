# Dimension: Tests & Verification

Focus: does the PR prove that it works, and will the tests catch it if it breaks later?

## Checklist

- **Coverage of the change:** new or changed logic with no test at all — especially
  branching logic, parsing, money/date math, auth checks, and bug fixes (a bug fix
  without a regression test is a finding).
- **Test quality:** tests that assert nothing meaningful (only "no exception"),
  assert on mocks instead of behavior, or would still pass if the code were wrong.
- **Edge cases:** missing tests for empty input, boundaries, error paths, and the
  specific failure scenarios found by other dimensions.
- **Brittleness:** tests depending on wall-clock time, randomness without a seed,
  network, test ordering, or real external services without isolation.
- **Fixtures & data:** fixtures that hide the data relevant to the assertion,
  hard-coded absolute paths, committed large data files.
- **Broken tests:** tests modified to pass by weakening assertions, `skip`/`xfail`
  added without explanation, deleted tests.
- **CI:** test not wired into the suite that CI runs; new dependency missing from
  test requirements.

## How to work

1. Map each changed source file to its test file(s). Note which have none.
2. For risky changes without tests, the `suggestion` should name the concrete test
   cases to add (inputs and expected outputs), not just "add tests".
3. If the repo has no test suite at all, report a single summary finding rather
   than one per file.
