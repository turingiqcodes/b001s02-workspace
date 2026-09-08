# Assessment Result — BAT-65

**[CODING] Find Largest Number Week1 Tuesday — B001S02**

| Field | Value |
|---|---|
| Candidate | b001s02 (b001s02@training.turingiq.ai) |
| Type | CODING |
| Score | 0/100 |
| Result | **FAIL** |
| Tests passed | 0/3 |
| Execution status | success |
| Submitted at | 2026-09-08T09:58:35.608Z |
| Graded at | 2026-09-08T09:58:40.253523+00:00 |

## Feedback

Hidden tests: 0/3 passed. AI code review: Your submission currently only contains a `pass` statement, meaning no actual logic has been implemented yet to solve the problem. To get started, you will need to replace `pass` with code that iterates through the `numbers` list to determine and return the maximum value. Once you write the implementation, make sure to test it against the provided test case to verify that it works correctly.

## Test Case Results

| Call | Expected | Actual | Passed |
|---|---|---|---|
| `find_largest([5])` | `5` | `None` | ❌ |
| `find_largest([-1, -5, -2])` | `-1` | `None` | ❌ |
| `find_largest([0, 0, 0])` | `0` | `None` | ❌ |

## Your Submitted Code

```python
def find_largest(numbers):
    # Write your code here
    pass

# Test your code with the following test cases:
# find_largest([45, 12, 98, 23, 67, 34, 89, 10, 55, 76])

```

## Reference Solution

```python
def find_largest(numbers):
    largest = numbers[0]
    for num in numbers:
        if num > largest:
            largest = num
    return largest

```

## AI Code Review

Your submission currently only contains a `pass` statement, meaning no actual logic has been implemented yet to solve the problem. To get started, you will need to replace `pass` with code that iterates through the `numbers` list to determine and return the maximum value. Once you write the implementation, make sure to test it against the provided test case to verify that it works correctly.
