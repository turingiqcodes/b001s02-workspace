import unittest

from car_pooling import assign_cars, cars_needed, parse_employee_count


class TestCarsNeeded(unittest.TestCase):
    # --- normal cases ---
    def test_less_than_one_car(self):
        self.assertEqual(cars_needed(3), 1)

    def test_exactly_one_full_car(self):
        self.assertEqual(cars_needed(5), 1)

    def test_one_extra_needs_new_car(self):
        self.assertEqual(cars_needed(6), 2)

    def test_exact_multiple_of_five(self):
        self.assertEqual(cars_needed(20), 4)

    def test_not_a_multiple_of_five(self):
        self.assertEqual(cars_needed(23), 5)

    def test_large_input(self):
        self.assertEqual(cars_needed(1_000_001), 200_001)

    # --- edge cases ---
    def test_zero_employees(self):
        self.assertEqual(cars_needed(0), 0)

    def test_single_employee(self):
        self.assertEqual(cars_needed(1), 1)

    # --- invalid input ---
    def test_negative_employees(self):
        with self.assertRaises(ValueError):
            cars_needed(-1)

    def test_non_integer_input(self):
        for bad in ("10", 10.5, True, None):
            with self.assertRaises(TypeError):
                cars_needed(bad)


class TestParseEmployeeCount(unittest.TestCase):
    def test_valid_number(self):
        self.assertEqual(parse_employee_count("12"), 12)

    def test_spaces_around_number(self):
        self.assertEqual(parse_employee_count("  7 \n"), 7)

    def test_invalid_text(self):
        for bad in ("", "abc", "-5", "4.5", "1 2"):
            with self.assertRaises(ValueError):
                parse_employee_count(bad)


class TestAssignCars(unittest.TestCase):
    def test_every_employee_assigned_once(self):
        cars = assign_cars(12)
        self.assertEqual([emp for car in cars for emp in car], list(range(1, 13)))

    def test_no_car_over_capacity(self):
        cars = assign_cars(23)
        self.assertEqual([len(car) for car in cars], [5, 5, 5, 5, 3])

    def test_no_employees(self):
        self.assertEqual(assign_cars(0), [])


if __name__ == "__main__":
    unittest.main()
