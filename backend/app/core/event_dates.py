"""Shared validation for event date ranges entering the Server."""

from __future__ import annotations

from datetime import date


EVENT_DATE_RANGE_ERROR = "End date must be on or after start date."


def require_valid_event_date_range(
    start_date: date | None,
    end_date: date | None,
) -> None:
    """Reject an impossible event range while permitting partial or same-day dates."""

    if start_date is not None and end_date is not None and end_date < start_date:
        raise ValueError(EVENT_DATE_RANGE_ERROR)
