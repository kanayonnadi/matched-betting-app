"""Odds freshness helpers.

Every quote carries a timestamp. Opportunities older than
``ODDS_STALE_SECONDS`` are flagged and must never be silently treated as live.
"""

from datetime import datetime, timezone

ODDS_STALE_SECONDS = 30


def _now(now=None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def age_seconds(timestamp: datetime, now=None) -> float:
    return max((_now(now) - timestamp).total_seconds(), 0.0)


def is_stale(timestamp: datetime, now=None, max_age_seconds: float = ODDS_STALE_SECONDS) -> bool:
    return age_seconds(timestamp, now=now) > max_age_seconds


def format_age(timestamp: datetime, now=None) -> str:
    seconds = int(age_seconds(timestamp, now=now))
    if seconds < 1:
        return "just now"
    if seconds == 1:
        return "1 second ago"
    if seconds < 60:
        return f"{seconds} seconds ago"
    minutes = seconds // 60
    if minutes == 1:
        return "1 minute ago"
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours == 1:
        return "1 hour ago"
    return f"{hours} hours ago"
