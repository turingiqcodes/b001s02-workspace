"""
Car Pooling Problem
-------------------
A company must transport m employees to the office. Each car has at most
5 seats. Every employee has their own pickup location, but all go to the
same destination. The program decides how many cars are needed, using as
few cars as possible.

Approach:
    1. Read m (number of employees) from the user and validate it.
    2. Cars needed = ceil(m / 5).
    3. Fill each car with up to 5 employees.
"""

import math

SEATS_PER_CAR = 5


def parse_employee_count(text):
    """Convert the user's input to a valid employee count, or raise ValueError."""
    text = text.strip()
    if not text.isdigit():
        raise ValueError("please enter a whole number (0 or more)")
    return int(text)


def cars_needed(m, seats=SEATS_PER_CAR):
    """Return the minimum number of cars needed for m employees."""
    if not isinstance(m, int) or isinstance(m, bool):
        raise TypeError("m must be an integer")
    if m < 0:
        raise ValueError("m cannot be negative")
    return math.ceil(m / seats)


def assign_cars(m, seats=SEATS_PER_CAR):
    """Return a list of cars, each car is a list of employee numbers (1..m)."""
    employees = list(range(1, m + 1))
    return [employees[i:i + seats] for i in range(0, cars_needed(m, seats) * seats, seats)]


def main():
    try:
        m = parse_employee_count(input("Enter the number of employees: "))
    except ValueError as error:
        print("Invalid input:", error)
        return

    cars = assign_cars(m)
    print("Cars needed:", len(cars))
    for number, car in enumerate(cars, start=1):
        print(f"Car {number}: {len(car)} employees -> {car}")


if __name__ == "__main__":
    main()
