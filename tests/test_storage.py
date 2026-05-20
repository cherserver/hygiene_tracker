from datetime import date
from pathlib import Path
import unittest
from uuid import uuid4

from hygiene_tracker.schedule import DueState
from hygiene_tracker.storage import Store


class StorageTest(unittest.TestCase):
    def test_seed_creates_default_tasks(self) -> None:
        store = Store(self.database_path())
        store.seed_defaults(today=date(2026, 4, 28))

        statuses = store.statuses(today=date(2026, 4, 28))

        self.assertEqual(
            [status.task.name for status in statuses],
            [
                "Cat fountain",
                "Air conditioner",
                "Full floor cleaning with steam",
            ],
        )
        self.assertEqual({status.due_state for status in statuses}, {DueState.DUE_TODAY})

    def test_mark_done_inserts_completion_and_moves_next_due(self) -> None:
        store = Store(self.database_path())
        store.seed_defaults(today=date(2026, 4, 28))
        cat = store.tasks()[0]

        store.mark_done(cat.id, completed_at=date(2026, 4, 28))
        [cat_status, *_] = store.statuses(today=date(2026, 4, 28))

        self.assertEqual(cat_status.last_completed, date(2026, 4, 28))
        self.assertEqual(cat_status.next_due, date(2026, 5, 5))
        self.assertEqual(cat_status.due_state, DueState.OK)
        self.assertEqual(store.history()[0].task_name, "Cat fountain")

    def test_local_statuses_are_ordered_by_shortest_period_first(self) -> None:
        store = Store(self.database_path())
        store.initialize()
        with store.connect() as conn:
            conn.executemany(
                """
                INSERT INTO tasks (name, interval_value, interval_unit, start_date, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    ("Monthly", 1, "months", "2026-04-28", 1),
                    ("Weekly", 7, "days", "2026-04-28", 2),
                    ("Daily", 1, "days", "2026-04-28", 3),
                ],
            )

        statuses = store.statuses(today=date(2026, 4, 28))

        self.assertEqual([status.task.name for status in statuses], ["Daily", "Weekly", "Monthly"])

    def test_calendar_event_drives_next_due_and_done_advances_to_next_instance(self) -> None:
        store = Store(self.database_path())
        store.upsert_calendar_event(
            calendar_id="hygiene@example.com",
            google_event_id="event-1",
            google_recurring_event_id="series-1",
            summary="Cat fountain",
            due_date=date(2026, 4, 28),
            start_at="2026-04-28",
            end_at="2026-04-29",
            status="confirmed",
            updated_at="2026-04-01T00:00:00Z",
            html_link="",
        )
        store.upsert_calendar_event(
            calendar_id="hygiene@example.com",
            google_event_id="event-2",
            google_recurring_event_id="series-1",
            summary="Cat fountain",
            due_date=date(2026, 5, 5),
            start_at="2026-05-05",
            end_at="2026-05-06",
            status="confirmed",
            updated_at="2026-04-01T00:00:00Z",
            html_link="",
        )

        [status] = store.statuses(today=date(2026, 4, 29))
        self.assertEqual(status.next_due, date(2026, 4, 28))
        self.assertEqual(status.due_state, DueState.OVERDUE)
        self.assertEqual(status.source, "google_calendar")

        store.mark_done(status.task.id, completed_at=date(2026, 4, 29), calendar_event_id=status.calendar_event_id)
        [next_status] = store.statuses(today=date(2026, 4, 29))

        self.assertEqual(next_status.next_due, date(2026, 5, 5))
        self.assertEqual(next_status.due_state, DueState.OK)

    def test_calendar_done_skips_all_missed_instances_up_to_completion_date(self) -> None:
        store = Store(self.database_path())
        for day in (9, 16, 23):
            store.upsert_calendar_event(
                calendar_id="hygiene@example.com",
                google_event_id=f"event-{day}",
                google_recurring_event_id="series-1",
                summary="Cat fountain",
                due_date=date(2026, 5, day),
                start_at=f"2026-05-{day:02d}",
                end_at=f"2026-05-{day + 1:02d}",
                status="confirmed",
                updated_at="2026-05-01T00:00:00Z",
                html_link="",
            )

        [status] = store.statuses(today=date(2026, 5, 18))
        self.assertEqual(status.next_due, date(2026, 5, 9))
        self.assertEqual(status.due_state, DueState.OVERDUE)

        store.mark_done(status.task.id, completed_at=date(2026, 5, 18), calendar_event_id=status.calendar_event_id)
        [next_status] = store.statuses(today=date(2026, 5, 18))

        self.assertEqual(next_status.next_due, date(2026, 5, 23))
        self.assertEqual(next_status.due_state, DueState.OK)

    def test_calendar_statuses_are_ordered_by_inferred_period(self) -> None:
        store = Store(self.database_path())
        for due_day in (1, 8):
            store.upsert_calendar_event(
                calendar_id="hygiene@example.com",
                google_event_id=f"weekly-{due_day}",
                google_recurring_event_id="weekly-series",
                summary="Weekly task",
                due_date=date(2026, 5, due_day),
                start_at=f"2026-05-{due_day:02d}",
                end_at=f"2026-05-{due_day + 1:02d}",
                status="confirmed",
                updated_at="2026-05-01T00:00:00Z",
                html_link="",
            )
        for due_day in (1, 31):
            store.upsert_calendar_event(
                calendar_id="hygiene@example.com",
                google_event_id=f"monthly-{due_day}",
                google_recurring_event_id="monthly-series",
                summary="Monthly task",
                due_date=date(2026, 5, due_day),
                start_at=f"2026-05-{due_day:02d}",
                end_at=f"2026-06-01" if due_day == 31 else f"2026-05-{due_day + 1:02d}",
                status="confirmed",
                updated_at="2026-05-01T00:00:00Z",
                html_link="",
            )

        statuses = store.statuses(today=date(2026, 5, 2))

        self.assertEqual([status.task.name for status in statuses], ["Weekly task", "Monthly task"])

    def test_missing_calendar_event_can_be_cancelled_after_sync(self) -> None:
        store = Store(self.database_path())
        store.upsert_calendar_event(
            calendar_id="hygiene@example.com",
            google_event_id="event-1",
            google_recurring_event_id="series-1",
            summary="Cat fountain",
            due_date=date(2026, 4, 28),
            start_at="2026-04-28",
            end_at="2026-04-29",
            status="confirmed",
            updated_at="2026-04-01T00:00:00Z",
            html_link="",
        )

        store.mark_missing_calendar_events_cancelled(
            calendar_id="hygiene@example.com",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 5, 1),
            seen_google_event_ids=set(),
        )

        self.assertEqual(store.statuses(today=date(2026, 4, 28)), [])

    def test_stale_calendar_task_is_deactivated_but_history_remains(self) -> None:
        store = Store(self.database_path())
        event_id = store.upsert_calendar_event(
            calendar_id="hygiene@example.com",
            google_event_id="event-1",
            google_recurring_event_id="series-1",
            summary="Cat fountain",
            due_date=date(2026, 4, 28),
            start_at="2026-04-28",
            end_at="2026-04-29",
            status="confirmed",
            updated_at="2026-04-01T00:00:00Z",
            html_link="",
        )
        [status] = store.statuses(today=date(2026, 4, 28))
        store.mark_done(status.task.id, completed_at=date(2026, 4, 28), calendar_event_id=event_id)
        store.mark_missing_calendar_events_cancelled(
            calendar_id="hygiene@example.com",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 5, 1),
            seen_google_event_ids=set(),
        )

        deactivated = store.deactivate_stale_calendar_tasks(calendar_id="hygiene@example.com")

        self.assertEqual(deactivated, 1)
        self.assertEqual(store.statuses(today=date(2026, 4, 29)), [])
        self.assertEqual(store.history()[0].task_name, "Cat fountain")

    def database_path(self) -> Path:
        directory = Path.cwd() / ".test-data"
        directory.mkdir(exist_ok=True)
        path = directory / f"{uuid4().hex}.sqlite3"
        self.addCleanup(lambda: path.exists() and path.unlink())
        return path


if __name__ == "__main__":
    unittest.main()
