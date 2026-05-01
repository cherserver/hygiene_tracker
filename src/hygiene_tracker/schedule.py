from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from math import ceil
import calendar


class DueState(str, Enum):
    OK = "OK"
    DUE_SOON = "Due soon"
    DUE_TODAY = "Due today"
    OVERDUE = "Overdue"


@dataclass(frozen=True)
class Interval:
    value: int
    unit: str

    def __post_init__(self) -> None:
        if self.value <= 0:
            raise ValueError("interval value must be positive")
        if self.unit not in {"days", "months"}:
            raise ValueError("interval unit must be 'days' or 'months'")


def add_months(source: date, months: int) -> date:
    """Add calendar months, clamping to the target month's last day."""
    if months <= 0:
        raise ValueError("months must be positive")

    month_index = source.month - 1 + months
    year = source.year + month_index // 12
    month = month_index % 12 + 1
    day = min(source.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def add_interval(source: date, interval: Interval) -> date:
    if interval.unit == "days":
        return source + timedelta(days=interval.value)
    return add_months(source, interval.value)


def next_due_date(
    *,
    interval: Interval,
    start_date: date,
    last_completed: date | None,
) -> date:
    if last_completed is None:
        return start_date
    return add_interval(last_completed, interval)


def due_state(
    *,
    today: date,
    due_date: date,
    interval: Interval,
    due_soon_fraction: float = 0.2,
) -> DueState:
    if today > due_date:
        return DueState.OVERDUE
    if today == due_date:
        return DueState.DUE_TODAY

    window_days = due_soon_window_days(due_date=due_date, interval=interval, fraction=due_soon_fraction)
    if (due_date - today).days <= window_days:
        return DueState.DUE_SOON
    return DueState.OK


def due_state_by_window(*, today: date, due_date: date, due_soon_days: int) -> DueState:
    if today > due_date:
        return DueState.OVERDUE
    if today == due_date:
        return DueState.DUE_TODAY
    if (due_date - today).days <= max(0, due_soon_days):
        return DueState.DUE_SOON
    return DueState.OK


def due_soon_window_days(*, due_date: date, interval: Interval, fraction: float) -> int:
    if fraction <= 0:
        return 0
    if interval.unit == "days":
        interval_days = interval.value
    else:
        interval_days = _approx_month_interval_days(due_date=due_date, months=interval.value)
    return max(1, ceil(interval_days * fraction))


def _approx_month_interval_days(*, due_date: date, months: int) -> int:
    month_index = due_date.month - 1 - months
    year = due_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(due_date.day, calendar.monthrange(year, month)[1])
    previous = date(year, month, day)
    return max(1, (due_date - previous).days)
