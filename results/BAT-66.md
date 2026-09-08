# Assessment Result — BAT-66

**[CODING] Student Marks Report Week1 Tuesday — B001S02**

| Field | Value |
|---|---|
| Candidate | b001s02 (b001s02@training.turingiq.ai) |
| Type | CODING |
| Score | 0/100 |
| Result | **FAIL** |
| Tests passed | 0/2 |
| Execution status | success |
| Submitted at | 2026-09-08T10:32:03.162Z |
| Graded at | 2026-09-08T10:32:07.502398+00:00 |

## Feedback

Hidden tests: 0/2 passed. AI code review: It looks like you only submitted the function signature with a `pass` statement, so the core logic has not been implemented yet. To solve the problem, you will need to iterate through the given dictionary of student names and marks to filter out those who meet the passing criteria. Once implemented, make sure your code uses dynamic iteration rather than hardcoding specific student names or values so it can handle any input dictionary.

## Test Case Results

| Call | Expected | Actual | Passed |
|---|---|---|---|
| `passed_students({})` | `[]` | `None` | ❌ |
| `passed_students({'A': 49, 'B': 50})` | `['B']` | `None` | ❌ |

## Your Submitted Code

```python
def passed_students(students):
    # Write your code here
    pass

# Test your code with the following test cases:
# passed_students({'Ravi': 85, 'Priya': 92, 'Kiran': 45, 'Anil': 55, 'Meena': 40})

```

## Reference Solution

```python
def passed_students(students):
    for name, mark in students.items():
        print(name, ":", mark)

    passed = []
    for name, mark in students.items():
        if mark >= 50:
            passed.append(name)
    return passed

```

## AI Code Review

It looks like you only submitted the function signature with a `pass` statement, so the core logic has not been implemented yet. To solve the problem, you will need to iterate through the given dictionary of student names and marks to filter out those who meet the passing criteria. Once implemented, make sure your code uses dynamic iteration rather than hardcoding specific student names or values so it can handle any input dictionary.
