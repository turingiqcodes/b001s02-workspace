# Assessment Result — BAT-64

**[CODING] Count Positive and Negative Numbers Week1 Tuesday — B001S02**

| Field | Value |
|---|---|
| Candidate | b001s02 (b001s02@training.turingiq.ai) |
| Type | CODING |
| Score | 0/100 |
| Result | **FAIL** |
| Tests passed | 0/3 |
| Execution status | success |
| Submitted at | 2026-09-08T10:23:13.278Z |
| Graded at | 2026-09-08T10:23:18.139483+00:00 |

## Feedback

Hidden tests: 0/3 passed. AI code review: It appears you have submitted the boilerplate function with the `pass` statement still in place, meaning no logic has been implemented yet to process the list of numbers. Because of this, the function currently returns `None` instead of counting the positive and negative values. Please write the logic to iterate through the list and track the counts so your code can be properly evaluated.

## Test Case Results

| Call | Expected | Actual | Passed |
|---|---|---|---|
| `count_positive_negative([])` | `[0, 0]` | `None` | ❌ |
| `count_positive_negative([1, 2, 3])` | `[3, 0]` | `None` | ❌ |
| `count_positive_negative([-1, -2, 0])` | `[0, 3]` | `None` | ❌ |

## Your Submitted Code

```python
def count_positive_negative(numbers):
    # Write your code here
    pass

# Test your code with the following test cases:
# count_positive_negative([10, -5, 20, -15, 30, 25, -8, 12, -2, 40])

```

## Reference Solution

```python
def count_positive_negative(numbers):
    index = 0
    positive = 0
    negative = 0

    while index < len(numbers):
        if numbers[index] > 0:
            print(numbers[index])
            positive = positive + 1
        else:
            negative = negative + 1
        index = index + 1

    return [positive, negative]

```

## AI Code Review

It appears you have submitted the boilerplate function with the `pass` statement still in place, meaning no logic has been implemented yet to process the list of numbers. Because of this, the function currently returns `None` instead of counting the positive and negative values. Please write the logic to iterate through the list and track the counts so your code can be properly evaluated.
