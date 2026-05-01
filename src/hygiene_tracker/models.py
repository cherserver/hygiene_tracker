from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .schedule import DueState


@dataclass(frozen=True)
class Task:
    id: int
    name: str
    interval_value: int
    interval_unit: str
    start_date: date
    sort_order: int
    active: bool = True
    source: str = "local"
    external_id: str | None = None


@dataclass(frozen=True)
class TaskStatus:
    task: Task
    last_completed: date | None
    next_due: date
    due_state: DueState
    calendar_event_id: int | None = None
    external_event_id: str | None = None
    source: str = "local"
    previous_due: date | None = None
    following_due: date | None = None


@dataclass(frozen=True)
class Completion:
    id: int
    task_id: int
    task_name: str
    completed_at: date
    note: str
    calendar_event_id: int | None = None


@dataclass(frozen=True)
class CalendarEvent:
    id: int
    task_id: int
    calendar_id: str
    google_event_id: str
    google_recurring_event_id: str
    summary: str
    due_date: date
    status: str
    updated_at: str
    html_link: str
