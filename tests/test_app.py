from datetime import date
import threading
import unittest

from hygiene_tracker.app import App
from hygiene_tracker.models import Task, TaskStatus
from hygiene_tracker.schedule import DueState


class FakeDashboard:
    def __init__(self) -> None:
        self.flashes: list[tuple[str, float]] = []

    def set_flash(self, message: str, seconds: float = 1.8) -> None:
        self.flashes.append((message, seconds))


class FakeStore:
    def __init__(self) -> None:
        self.done_calls: list[tuple[int, int | None]] = []

    def mark_done(self, task_id: int, calendar_event_id: int | None = None) -> None:
        self.done_calls.append((task_id, calendar_event_id))


class AppTest(unittest.TestCase):
    def test_done_requires_second_press_to_confirm(self) -> None:
        app = App.__new__(App)
        app.store = FakeStore()
        app.dashboard = FakeDashboard()
        app.selected_index = 0
        app.last_selection_at = 0.0
        app._dirty = False
        app._lock = threading.Lock()
        app._pending_done_task_id = None
        app._pending_done_calendar_event_id = None
        app._pending_done_until = 0.0
        app._statuses = [
            TaskStatus(
                task=Task(12, "Air conditioner filters", 1, "months", date(2026, 4, 28), 1),
                last_completed=None,
                next_due=date(2026, 4, 30),
                due_state=DueState.DUE_TODAY,
                calendar_event_id=34,
            )
        ]

        app.mark_selected_done()

        self.assertEqual(app.store.done_calls, [])
        self.assertEqual(app._pending_done_task_id, 12)
        self.assertIn("Press Done again", app.dashboard.flashes[-1][0])

        app.mark_selected_done()

        self.assertEqual(app.store.done_calls, [(12, 34)])
        self.assertIsNone(app.selected_index)
        self.assertTrue(app._dirty)
        self.assertIsNone(app._pending_done_task_id)


if __name__ == "__main__":
    unittest.main()
