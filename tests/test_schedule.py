from datetime import date
import unittest

from hygiene_tracker.schedule import DueState, Interval, add_interval, due_state, due_state_by_window, next_due_date


class ScheduleTest(unittest.TestCase):
    def test_next_due_uses_start_date_when_task_has_never_been_completed(self) -> None:
        self.assertEqual(
            next_due_date(
                interval=Interval(7, "days"),
                start_date=date(2026, 4, 28),
                last_completed=None,
            ),
            date(2026, 4, 28),
        )

    def test_next_due_uses_last_completed_for_day_interval(self) -> None:
        self.assertEqual(
            next_due_date(
                interval=Interval(7, "days"),
                start_date=date(2026, 1, 1),
                last_completed=date(2026, 4, 28),
            ),
            date(2026, 5, 5),
        )

    def test_month_interval_clamps_to_last_day_of_short_month(self) -> None:
        self.assertEqual(add_interval(date(2026, 1, 31), Interval(1, "months")), date(2026, 2, 28))

    def test_two_month_interval_crosses_year_boundary(self) -> None:
        self.assertEqual(add_interval(date(2026, 11, 30), Interval(2, "months")), date(2027, 1, 30))

    def test_due_state_for_weekly_task(self) -> None:
        cases = [
            (date(2026, 4, 20), date(2026, 4, 28), DueState.OK),
            (date(2026, 4, 27), date(2026, 4, 28), DueState.DUE_SOON),
            (date(2026, 4, 28), date(2026, 4, 28), DueState.DUE_TODAY),
            (date(2026, 4, 29), date(2026, 4, 28), DueState.OVERDUE),
        ]
        for today, due, expected in cases:
            with self.subTest(today=today, due=due):
                self.assertEqual(due_state(today=today, due_date=due, interval=Interval(7, "days")), expected)

    def test_interval_rejects_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            Interval(0, "days")
        with self.assertRaises(ValueError):
            Interval(1, "weeks")

    def test_due_state_by_fixed_window(self) -> None:
        self.assertEqual(
            due_state_by_window(today=date(2026, 4, 25), due_date=date(2026, 4, 28), due_soon_days=3),
            DueState.DUE_SOON,
        )
        self.assertEqual(
            due_state_by_window(today=date(2026, 4, 24), due_date=date(2026, 4, 28), due_soon_days=3),
            DueState.OK,
        )


if __name__ == "__main__":
    unittest.main()
