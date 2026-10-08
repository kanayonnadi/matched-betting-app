"""API quota accounting for The Odds API.

The Odds API returns ``x-requests-used`` / ``x-requests-remaining`` headers. We
record them so the UI can show usage and avoid burning the monthly allowance.
Process-global and thread-safe.
"""

from dataclasses import dataclass
from threading import Lock
from typing import Optional


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class UsageSnapshot:
    requests_used: int = 0
    requests_remaining: Optional[int] = None
    last_status: Optional[int] = None
    exhausted: bool = False
    errors: int = 0


class UsageTracker:
    def __init__(self):
        self._lock = Lock()
        self._snapshot = UsageSnapshot()

    @property
    def snapshot(self) -> UsageSnapshot:
        return self._snapshot

    def record(self, headers, status_code) -> None:
        with self._lock:
            used = _as_int(headers.get("x-requests-used"))
            remaining = _as_int(headers.get("x-requests-remaining"))
            if used is not None:
                self._snapshot.requests_used = used
            if remaining is not None:
                self._snapshot.requests_remaining = remaining
            self._snapshot.last_status = status_code
            if status_code == 429:
                self._snapshot.exhausted = True

    def record_error(self, status_code=None) -> None:
        with self._lock:
            self._snapshot.errors += 1
            if status_code == 429:
                self._snapshot.exhausted = True


_tracker = UsageTracker()


def get_usage_tracker() -> UsageTracker:
    return _tracker


def reset_usage_tracker() -> None:
    global _tracker
    _tracker = UsageTracker()
