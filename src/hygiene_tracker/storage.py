from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from pathlib import Path
import sqlite3
from typing import Iterator

from .models import CalendarEvent, Completion, Task, TaskStatus
from .schedule import Interval, due_state, due_state_by_window, next_due_date


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    interval_value INTEGER NOT NULL CHECK (interval_value > 0),
    interval_unit TEXT NOT NULL CHECK (interval_unit IN ('days', 'months')),
    start_date TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'local',
    external_id TEXT
);

CREATE TABLE IF NOT EXISTS calendar_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    calendar_id TEXT NOT NULL,
    google_event_id TEXT NOT NULL,
    google_recurring_event_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    due_date TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirmed',
    updated_at TEXT NOT NULL DEFAULT '',
    html_link TEXT NOT NULL DEFAULT '',
    synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(calendar_id, google_event_id)
);

CREATE INDEX IF NOT EXISTS idx_calendar_events_task_due
ON calendar_events(task_id, due_date, id);

CREATE TABLE IF NOT EXISTS completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    calendar_event_id INTEGER REFERENCES calendar_events(id) ON DELETE SET NULL,
    completed_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_completions_task_date
ON completions(task_id, completed_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS calendar_sync_state (
    calendar_id TEXT PRIMARY KEY,
    sync_token TEXT,
    last_sync_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


SEED_TASKS = [
    ("Cat fountain", 7, "days", 1),
    ("Air conditioner", 1, "months", 2),
    ("Full floor cleaning with steam", 2, "months", 3),
]


class Store:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            _ensure_column(conn, "tasks", "source", "TEXT NOT NULL DEFAULT 'local'")
            _ensure_column(conn, "tasks", "external_id", "TEXT")
            _ensure_column(conn, "completions", "calendar_event_id", "INTEGER REFERENCES calendar_events(id) ON DELETE SET NULL")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_source_external
                ON tasks(source, external_id)
                WHERE external_id IS NOT NULL
                """
            )

    def seed_defaults(self, today: date | None = None) -> None:
        self.initialize()
        today = today or date.today()
        with self.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            if count:
                return
            conn.executemany(
                """
                INSERT INTO tasks (name, interval_value, interval_unit, start_date, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                [(name, value, unit, today.isoformat(), order) for name, value, unit, order in SEED_TASKS],
            )

    def tasks(self) -> list[Task]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, interval_value, interval_unit, start_date,
                       sort_order, active, source, external_id
                FROM tasks
                WHERE active = 1
                ORDER BY sort_order, id
                """
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def statuses(
        self,
        today: date | None = None,
        due_soon_fraction: float = 0.2,
        calendar_due_soon_days: int = 3,
    ) -> list[TaskStatus]:
        today = today or date.today()
        calendar_statuses = self.calendar_statuses(today=today, due_soon_days=calendar_due_soon_days)
        if calendar_statuses:
            return calendar_statuses

        statuses: list[TaskStatus] = []
        for task in self.tasks():
            if task.source != "local":
                continue
            last_completed = self.last_completed_date(task.id)
            interval = Interval(task.interval_value, task.interval_unit)
            next_due = next_due_date(
                interval=interval,
                start_date=task.start_date,
                last_completed=last_completed,
            )
            statuses.append(
                TaskStatus(
                    task=task,
                    last_completed=last_completed,
                    next_due=next_due,
                    due_state=due_state(
                        today=today,
                        due_date=next_due,
                        interval=interval,
                        due_soon_fraction=due_soon_fraction,
                    ),
                )
            )
        return sorted(statuses, key=_status_sort_key)

    def calendar_statuses(self, today: date | None = None, due_soon_days: int = 3) -> list[TaskStatus]:
        self.initialize()
        today = today or date.today()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT tasks.id AS task_id, tasks.name, tasks.interval_value, tasks.interval_unit,
                       tasks.start_date, tasks.sort_order, tasks.active, tasks.source, tasks.external_id,
                       calendar_events.id AS calendar_event_id,
                       calendar_events.google_event_id,
                       calendar_events.due_date,
                       (
                         SELECT MAX(previous.due_date)
                         FROM calendar_events previous
                         WHERE previous.task_id = tasks.id
                           AND previous.status != 'cancelled'
                           AND previous.due_date < calendar_events.due_date
                       ) AS previous_due,
                       (
                         SELECT MIN(following.due_date)
                         FROM calendar_events following
                         WHERE following.task_id = tasks.id
                           AND following.status != 'cancelled'
                           AND following.due_date > calendar_events.due_date
                       ) AS following_due
                FROM calendar_events
                JOIN tasks ON tasks.id = calendar_events.task_id
                WHERE tasks.active = 1
                  AND tasks.source = 'google_calendar'
                  AND calendar_events.status != 'cancelled'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM completions
                    WHERE completions.calendar_event_id = calendar_events.id
                  )
                  AND calendar_events.due_date > COALESCE((
                    SELECT MAX(task_completions.completed_at)
                    FROM completions task_completions
                    WHERE task_completions.task_id = tasks.id
                  ), '0001-01-01')
                  AND calendar_events.id = (
                    SELECT ce2.id
                    FROM calendar_events ce2
                    WHERE ce2.task_id = tasks.id
                      AND ce2.status != 'cancelled'
                      AND NOT EXISTS (
                        SELECT 1
                        FROM completions c2
                        WHERE c2.calendar_event_id = ce2.id
                      )
                      AND ce2.due_date > COALESCE((
                        SELECT MAX(task_completions2.completed_at)
                        FROM completions task_completions2
                        WHERE task_completions2.task_id = tasks.id
                      ), '0001-01-01')
                    ORDER BY ce2.due_date ASC, ce2.id ASC
                    LIMIT 1
                  )
                ORDER BY calendar_events.due_date ASC, tasks.sort_order, tasks.id
                """
            ).fetchall()

        statuses: list[TaskStatus] = []
        for row in rows:
            task = _task_from_calendar_row(row)
            next_due = date.fromisoformat(row["due_date"])
            statuses.append(
                TaskStatus(
                    task=task,
                    last_completed=self.last_completed_date(task.id),
                    next_due=next_due,
                    due_state=due_state_by_window(today=today, due_date=next_due, due_soon_days=due_soon_days),
                    calendar_event_id=row["calendar_event_id"],
                    external_event_id=row["google_event_id"],
                    source="google_calendar",
                    previous_due=date.fromisoformat(row["previous_due"]) if row["previous_due"] else None,
                    following_due=date.fromisoformat(row["following_due"]) if row["following_due"] else None,
                )
            )
        return sorted(statuses, key=_status_sort_key)

    def last_completed_date(self, task_id: int) -> date | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT completed_at
                FROM completions
                WHERE task_id = ?
                ORDER BY completed_at DESC, id DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return date.fromisoformat(row["completed_at"])

    def mark_done(
        self,
        task_id: int,
        completed_at: date | None = None,
        note: str = "",
        calendar_event_id: int | None = None,
    ) -> None:
        self.initialize()
        completed_at = completed_at or date.today()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO completions (task_id, calendar_event_id, completed_at, note) VALUES (?, ?, ?, ?)",
                (task_id, calendar_event_id, completed_at.isoformat(), note),
            )

    def update_task(self, task_id: int, name: str, interval_value: int, interval_unit: str) -> None:
        Interval(interval_value, interval_unit)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET name = ?, interval_value = ?, interval_unit = ?
                WHERE id = ?
                """,
                (name.strip(), interval_value, interval_unit, task_id),
            )

    def upsert_calendar_event(
        self,
        *,
        calendar_id: str,
        google_event_id: str,
        google_recurring_event_id: str,
        summary: str,
        due_date: date,
        start_at: str,
        end_at: str,
        status: str,
        updated_at: str,
        html_link: str,
    ) -> int:
        self.initialize()
        with self.connect() as conn:
            task_id = self._upsert_calendar_task(
                conn,
                name=summary,
                external_id=google_recurring_event_id,
                start_date=due_date,
            )
            conn.execute(
                """
                INSERT INTO calendar_events (
                    task_id, calendar_id, google_event_id, google_recurring_event_id,
                    summary, due_date, start_at, end_at, status, updated_at, html_link
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(calendar_id, google_event_id) DO UPDATE SET
                    task_id = excluded.task_id,
                    google_recurring_event_id = excluded.google_recurring_event_id,
                    summary = excluded.summary,
                    due_date = excluded.due_date,
                    start_at = excluded.start_at,
                    end_at = excluded.end_at,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    html_link = excluded.html_link,
                    synced_at = CURRENT_TIMESTAMP
                """,
                (
                    task_id,
                    calendar_id,
                    google_event_id,
                    google_recurring_event_id,
                    summary,
                    due_date.isoformat(),
                    start_at,
                    end_at,
                    status,
                    updated_at,
                    html_link,
                ),
            )
            row = conn.execute(
                "SELECT id FROM calendar_events WHERE calendar_id = ? AND google_event_id = ?",
                (calendar_id, google_event_id),
            ).fetchone()
        return int(row["id"])

    def calendar_events(self, limit: int = 100) -> list[CalendarEvent]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, task_id, calendar_id, google_event_id, google_recurring_event_id,
                       summary, due_date, status, updated_at, html_link
                FROM calendar_events
                ORDER BY due_date, id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            CalendarEvent(
                id=row["id"],
                task_id=row["task_id"],
                calendar_id=row["calendar_id"],
                google_event_id=row["google_event_id"],
                google_recurring_event_id=row["google_recurring_event_id"],
                summary=row["summary"],
                due_date=date.fromisoformat(row["due_date"]),
                status=row["status"],
                updated_at=row["updated_at"],
                html_link=row["html_link"],
            )
            for row in rows
        ]

    def mark_missing_calendar_events_cancelled(
        self,
        *,
        calendar_id: str,
        start_date: date,
        end_date: date,
        seen_google_event_ids: set[str],
    ) -> None:
        self.initialize()
        with self.connect() as conn:
            if seen_google_event_ids:
                placeholders = ",".join("?" for _ in seen_google_event_ids)
                conn.execute(
                    f"""
                    UPDATE calendar_events
                    SET status = 'cancelled', synced_at = CURRENT_TIMESTAMP
                    WHERE calendar_id = ?
                      AND due_date BETWEEN ? AND ?
                      AND google_event_id NOT IN ({placeholders})
                    """,
                    (calendar_id, start_date.isoformat(), end_date.isoformat(), *seen_google_event_ids),
                )
            else:
                conn.execute(
                    """
                    UPDATE calendar_events
                    SET status = 'cancelled', synced_at = CURRENT_TIMESTAMP
                    WHERE calendar_id = ?
                      AND due_date BETWEEN ? AND ?
                    """,
                    (calendar_id, start_date.isoformat(), end_date.isoformat()),
                )

    def deactivate_stale_calendar_tasks(self, *, calendar_id: str) -> int:
        self.initialize()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET active = 0
                WHERE source = 'google_calendar'
                  AND active = 1
                  AND NOT EXISTS (
                    SELECT 1
                    FROM calendar_events
                    WHERE calendar_events.task_id = tasks.id
                      AND calendar_events.calendar_id = ?
                      AND calendar_events.status != 'cancelled'
                  )
                """,
                (calendar_id,),
            )
        return cursor.rowcount

    def get_sync_token(self, calendar_id: str) -> str | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT sync_token FROM calendar_sync_state WHERE calendar_id = ?",
                (calendar_id,),
            ).fetchone()
        return None if row is None else row["sync_token"]

    def set_sync_token(self, calendar_id: str, sync_token: str | None) -> None:
        self.initialize()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO calendar_sync_state (calendar_id, sync_token, last_sync_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(calendar_id) DO UPDATE SET
                    sync_token = excluded.sync_token,
                    last_sync_at = CURRENT_TIMESTAMP
                """,
                (calendar_id, sync_token),
            )

    def history(self, limit: int = 50) -> list[Completion]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT completions.id, completions.task_id, tasks.name AS task_name,
                       completions.completed_at, completions.note, completions.calendar_event_id
                FROM completions
                JOIN tasks ON tasks.id = completions.task_id
                ORDER BY completions.completed_at DESC, completions.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            Completion(
                id=row["id"],
                task_id=row["task_id"],
                task_name=row["task_name"],
                completed_at=date.fromisoformat(row["completed_at"]),
                note=row["note"],
                calendar_event_id=row["calendar_event_id"],
            )
            for row in rows
        ]

    def _upsert_calendar_task(
        self,
        conn: sqlite3.Connection,
        *,
        name: str,
        external_id: str,
        start_date: date,
    ) -> int:
        row = conn.execute(
            "SELECT id FROM tasks WHERE source = 'google_calendar' AND external_id = ?",
            (external_id,),
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE tasks SET name = ?, active = 1 WHERE id = ?",
                (name, row["id"]),
            )
            return int(row["id"])

        sort_order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM tasks").fetchone()[0]
        cursor = conn.execute(
            """
            INSERT INTO tasks (
                name, interval_value, interval_unit, start_date, sort_order,
                active, source, external_id
            )
            VALUES (?, 1, 'days', ?, ?, 1, 'google_calendar', ?)
            """,
            (name, start_date.isoformat(), sort_order, external_id),
        )
        return int(cursor.lastrowid)


def _task_from_row(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        name=row["name"],
        interval_value=row["interval_value"],
        interval_unit=row["interval_unit"],
        start_date=date.fromisoformat(row["start_date"]),
        sort_order=row["sort_order"],
        active=bool(row["active"]),
        source=row["source"],
        external_id=row["external_id"],
    )


def _task_from_calendar_row(row: sqlite3.Row) -> Task:
    return Task(
        id=row["task_id"],
        name=row["name"],
        interval_value=row["interval_value"],
        interval_unit=row["interval_unit"],
        start_date=date.fromisoformat(row["start_date"]),
        sort_order=row["sort_order"],
        active=bool(row["active"]),
        source=row["source"],
        external_id=row["external_id"],
    )


def _status_sort_key(status: TaskStatus) -> tuple[int, int, int, str]:
    return (_status_period_days(status), status.task.sort_order, status.task.id, status.task.name.lower())


def _status_period_days(status: TaskStatus) -> int:
    if status.previous_due is not None:
        return max(1, (status.next_due - status.previous_due).days)
    if status.following_due is not None:
        return max(1, (status.following_due - status.next_due).days)
    if status.task.interval_unit == "days":
        return max(1, status.task.interval_value)
    return max(1, status.task.interval_value * 30)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
