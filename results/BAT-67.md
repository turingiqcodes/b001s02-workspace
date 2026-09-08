# Assessment Result — BAT-67

**[CODING] Student Performance Summary Week1 Tuesday — B001S02**

| Field | Value |
|---|---|
| Candidate | b001s02 (b001s02@training.turingiq.ai) |
| Type | CODING |
| Score | 0/100 |
| Result | **FAIL** |
| Tests passed | 0/2 |
| Execution status | success |
| Submitted at | 2026-09-08T09:14:13.402Z |
| Graded at | 2026-09-08T09:14:17.960840+00:00 |

## Feedback

Hidden tests: 0/2 passed. AI code review: The submitted code is currently just a placeholder containing the `pass` statement, meaning no logic has been implemented yet to process the student data. To complete the assignment, you will need to iterate through the dictionary, calculate the required summary statistics (such as averages or categories), and return the results.

## Test Case Results

| Call | Expected | Actual | Passed |
|---|---|---|---|
| `performance_summary({'A': 50})` | `{'passed': 1, 'failed': 0, 'highest': 50, 'lowest': 50}` | `None` | ❌ |
| `performance_summary({'A': 10, 'B': 20})` | `{'passed': 0, 'failed': 2, 'highest': 20, 'lowest': 10}` | `None` | ❌ |

## Your Submitted Code

```python
def performance_summary(students):
    # Write your code here
    pass

# Test your code with the following test cases:
# performance_summary({'Ravi': 85, 'Priya': 92, 'Kiran': 45, 'Anil': 55, 'Meena': 40, 'John': 70, 'Asha': 88, 'Vijay': 35, 'Arun': 61, 'Neha': 95})

```

## Reference Solution

```python
def performance_summary(students):
    passed = 0
    failed = 0

    marks = list(students.values())
    highest = marks[0]
    lowest = marks[0]

    for mark in marks:
        if mark >= 50:
            passed += 1
        else:
            failed += 1

        if mark > highest:
            highest = mark
        if mark < lowest:
            lowest = mark

    return {"passed": passed, "failed": failed, "highest": highest, "lowest": lowest}

```

## AI Code Review

The submitted code is currently just a placeholder containing the `pass` statement, meaning no logic has been implemented yet to process the student data. To complete the assignment, you will need to iterate through the dictionary, calculate the required summary statistics (such as averages or categories), and return the results.
