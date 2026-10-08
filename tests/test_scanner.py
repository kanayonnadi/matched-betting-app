from datetime import datetime, timedelta, timezone

from monitoring import OpportunityScanner
from providers import MockExchangeProvider, MockSportsbookProvider

NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)


class TickingClock:
    def __init__(self, start):
        self._now = start

    def __call__(self):
        return self._now

    def advance(self, seconds):
        self._now = self._now + timedelta(seconds=seconds)


def make_scanner(clock):
    return OpportunityScanner(
        MockSportsbookProvider(),
        MockExchangeProvider(),
        ["basketball", "soccer"],
        100,
        scan_interval_seconds=30,
        clock=clock,
    )


def test_scan_reuses_result_within_interval():
    clock = TickingClock(NOW)
    scanner = make_scanner(clock)

    first = scanner.scan()
    clock.advance(10)
    second = scanner.scan()

    assert first is second
    assert len(first.opportunities) == 9
    assert first.scanned_at == NOW


def test_scan_refreshes_after_interval():
    clock = TickingClock(NOW)
    scanner = make_scanner(clock)

    first = scanner.scan()
    clock.advance(31)
    second = scanner.scan()

    assert first is not second
    assert second.scanned_at == NOW + timedelta(seconds=31)


def test_force_rescan():
    clock = TickingClock(NOW)
    scanner = make_scanner(clock)

    first = scanner.scan()
    second = scanner.scan(force=True)
    assert first is not second


def test_source_is_mock_for_mock_providers():
    clock = TickingClock(NOW)
    assert make_scanner(clock).scan().source == "mock"


def test_scan_result_staleness():
    clock = TickingClock(NOW)
    scanner = make_scanner(clock)
    result = scanner.scan()

    assert result.is_stale(now=NOW) is False
    assert result.is_stale(now=NOW + timedelta(seconds=31)) is True
