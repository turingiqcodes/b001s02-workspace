"""
Failure scenarios for the car pooling problem.

EVERY test in this file is EXPECTED TO FAIL. Each one shows a wrong
assumption or a feature the current code does not support, and its
message explains why it fails. The real tests are in test_car_pooling.py.
"""

import unittest

from car_pooling import SEATS_PER_CAR, assign_cars, cars_needed, parse_employee_count


class WrongAssumptions(unittest.TestCase):
    """Tests written with the wrong logic, the way a beginner might."""

    def test_using_floor_division_instead_of_ceil(self):
        # 7 // 5 = 1, but 7 employees cannot fit in 1 car of 5 seats
        self.assertEqual(
            cars_needed(7), 7 // 5,
            "FAILED: 7 employees need 2 cars, not 1. Using // (floor) drops the "
            "2 extra employees; the code correctly uses ceil(7 / 5) = 2.",
        )

    def test_assuming_car_has_6_seats(self):
        self.assertEqual(
            cars_needed(12), 2,
            f"FAILED: expected 2 cars assuming 6 seats per car, but each car "
            f"has only {SEATS_PER_CAR} seats, so 12 employees need 3 cars.",
        )

    def test_assuming_one_employee_per_car(self):
        self.assertEqual(
            len(assign_cars(10)), 10,
            "FAILED: expected 10 cars (one per employee), but the goal is to use "
            "as FEW cars as possible: 10 employees fit in 2 cars.",
        )

    def test_assuming_cars_are_split_evenly(self):
        self.assertEqual(
            [len(car) for car in assign_cars(8)], [4, 4],
            "FAILED: expected an even split of 4 + 4, but the code fills each "
            "car completely before using the next one, giving 5 + 3.",
        )


class InvalidInputNotRaised(unittest.TestCase):
    """Tests expecting bad input to be accepted, but the code rejects it."""

    def test_negative_employees_return_zero(self):
        try:
            cars_needed(-3)
        except ValueError as error:
            self.fail(
                f"FAILED: expected 0 cars for -3 employees, but a negative count "
                f"is invalid and the code raises ValueError('{error}')."
            )

    def test_plus_sign_input_accepted(self):
        try:
            parse_employee_count("+5")
        except ValueError as error:
            self.fail(
                f"FAILED: '+5' is rejected because the input check only allows "
                f"the digits 0-9, not a leading '+'. Code says: '{error}'."
            )

    def test_decimal_employee_count_accepted(self):
        try:
            parse_employee_count("4.5")
        except ValueError as error:
            self.fail(
                f"FAILED: expected '4.5' to be rounded to 4, but half an employee "
                f"is not valid. Code says: '{error}'."
            )


class MissingFeatures(unittest.TestCase):
    """Features from the first version that were removed from the current code."""

    def test_limit_on_available_cars(self):
        self.assertEqual(
            assign_cars(11, 2), -1,
            "FAILED: expected -1 because only 2 cars are available, but the "
            "second argument of assign_cars is SEATS PER CAR, not number of "
            "cars. The current code has no limit on cars.",
        )


if __name__ == "__main__":
    unittest.main()
