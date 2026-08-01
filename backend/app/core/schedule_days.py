"""Shared working-day range helpers for published server schedules."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from typing import Any


DEFAULT_SCHEDULE_DAY_RANGE = {"start_hour": 6, "end_hour": 24}


def normalise_schedule_day_range(value: Any) -> dict[str, int]:
    """Return a validated schedule range or the standard-day default."""
    if not isinstance(value, dict):
        return dict(DEFAULT_SCHEDULE_DAY_RANGE)
    try:
        start_hour = int(value.get("start_hour", value.get("startHour")))
        end_hour = int(value.get("end_hour", value.get("endHour")))
    except (TypeError, ValueError):
        return dict(DEFAULT_SCHEDULE_DAY_RANGE)
    if not (0 <= start_hour <= 23 and start_hour < end_hour <= 36):
        return dict(DEFAULT_SCHEDULE_DAY_RANGE)
    return {"start_hour": start_hour, "end_hour": end_hour}


def event_schedule_day_range(metadata_json: str | None) -> dict[str, int]:
    """Read the persisted schedule range from event metadata."""
    try:
        metadata = json.loads(metadata_json) if metadata_json else {}
    except (TypeError, ValueError):
        metadata = {}
    return normalise_schedule_day_range(metadata.get("schedule_day_range"))


def merge_schedule_day_range(
    metadata_json: str | None,
    schedule_day_range: dict[str, int],
) -> str:
    """Merge a validated schedule range into existing event metadata JSON."""
    try:
        metadata = json.loads(metadata_json) if metadata_json else {}
    except (TypeError, ValueError):
        metadata = {}
    metadata["schedule_day_range"] = normalise_schedule_day_range(schedule_day_range)
    return json.dumps(metadata)


def schedule_day_offset_hour(schedule_day_range: dict[str, int]) -> int:
    """Return the next-day tail length represented by a schedule range."""
    normalised = normalise_schedule_day_range(schedule_day_range)
    return max(0, normalised["end_hour"] - 24)


def working_date_for_clock(
    actual_date: str | date,
    clock_time: str,
    schedule_day_range: dict[str, int],
) -> str:
    """Return the working date owning one local actual date and clock time."""
    day = actual_date if isinstance(actual_date, date) else date.fromisoformat(actual_date)
    parsed_time = datetime.strptime(clock_time[:5], "%H:%M")
    if parsed_time.hour < schedule_day_offset_hour(schedule_day_range):
        day -= timedelta(days=1)
    return day.isoformat()


def working_date_for_datetime(
    value: datetime,
    schedule_day_range: dict[str, int],
) -> str:
    """Return the working date owning one local published datetime."""
    return working_date_for_clock(
        value.date(),
        f"{value.hour:02d}:{value.minute:02d}",
        schedule_day_range,
    )
