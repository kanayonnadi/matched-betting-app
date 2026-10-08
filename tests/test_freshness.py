from datetime import datetime, timedelta, timezone

from monitoring import ODDS_STALE_SECONDS, age_seconds, format_age, is_stale

NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_default_stale_seconds():
    assert ODDS_STALE_SECONDS == 30


def test_age_seconds():
    assert age_seconds(NOW - timedelta(seconds=8), now=NOW) == 8


def test_age_never_negative():
    assert age_seconds(NOW + timedelta(seconds=5), now=NOW) == 0


def test_is_stale_boundary():
    assert is_stale(NOW - timedelta(seconds=30), now=NOW) is False
    assert is_stale(NOW - timedelta(seconds=31), now=NOW) is True


def test_is_stale_custom_threshold():
    assert is_stale(NOW - timedelta(seconds=10), now=NOW, max_age_seconds=5) is True


def test_format_age():
    assert format_age(NOW, now=NOW) == "just now"
    assert format_age(NOW - timedelta(seconds=1), now=NOW) == "1 second ago"
    assert format_age(NOW - timedelta(seconds=8), now=NOW) == "8 seconds ago"
    assert format_age(NOW - timedelta(minutes=2), now=NOW) == "2 minutes ago"
    assert format_age(NOW - timedelta(hours=3), now=NOW) == "3 hours ago"
