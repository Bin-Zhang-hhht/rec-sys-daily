"""State-derived query windows shared by cold-start and daily runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .schemas import State


@dataclass(frozen=True)
class QueryWindow:
    """The inclusive lower and exclusive upper bounds for both source kinds."""

    papers_since: datetime
    blogs_since: datetime
    until: datetime


def _valid_state(value: State | dict[str, Any] | None) -> State | None:
    if value is None:
        return None
    state = value if isinstance(value, State) else State.model_validate(value)
    if state.last_success_at is None:
        raise ValueError("existing state must include last_success_at")
    return state


def query_window(state: State | dict[str, Any] | None, *, now: datetime | None = None) -> QueryWindow:
    """Return the documented cold-start or state-derived source windows."""
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() != timedelta(0):
        raise ValueError("now must be timezone-aware UTC")
    state_value = _valid_state(state)
    if state_value is None:
        try:
            papers_since = current.replace(year=current.year - 5)
        except ValueError:
            papers_since = current.replace(year=current.year - 5, day=28)
        try:
            blogs_since = current.replace(year=current.year - 3)
        except ValueError:
            blogs_since = current.replace(year=current.year - 3, day=28)
    else:
        last_success = state_value.last_success_at
        assert last_success is not None
        papers_since = last_success - timedelta(hours=48)
        blogs_since = last_success - timedelta(days=7)
    return QueryWindow(papers_since=papers_since, blogs_since=blogs_since, until=current)
