from datetime import datetime, timedelta, timezone

from providers import CachedProvider

NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)


class TickingClock:
    def __init__(self, start):
        self._now = start

    def __call__(self):
        return self._now

    def advance(self, seconds):
        self._now = self._now + timedelta(seconds=seconds)


class CountingProvider:
    name = "counting"

    def __init__(self):
        self.calls = 0

    def get_events(self, sport):
        self.calls += 1
        return (sport,)

    def get_markets(self, event_id):
        self.calls += 1
        return (event_id,)

    def get_odds(self, event_id, market):
        self.calls += 1
        return (event_id, market)


def test_cache_hit_within_ttl():
    clock = TickingClock(NOW)
    inner = CountingProvider()
    cached = CachedProvider(inner, ttl_seconds=30, clock=clock)

    assert cached.get_events("nba") == ("nba",)
    assert cached.get_events("nba") == ("nba",)
    assert inner.calls == 1


def test_cache_expires_after_ttl():
    clock = TickingClock(NOW)
    inner = CountingProvider()
    cached = CachedProvider(inner, ttl_seconds=30, clock=clock)

    cached.get_events("nba")
    clock.advance(31)
    cached.get_events("nba")
    assert inner.calls == 2


def test_distinct_keys_are_cached_separately():
    clock = TickingClock(NOW)
    inner = CountingProvider()
    cached = CachedProvider(inner, ttl_seconds=30, clock=clock)

    cached.get_odds("evt", "moneyline")
    cached.get_odds("evt", "totals")
    assert inner.calls == 2


def test_invalidate_clears_cache():
    clock = TickingClock(NOW)
    inner = CountingProvider()
    cached = CachedProvider(inner, ttl_seconds=30, clock=clock)

    cached.get_events("nba")
    cached.invalidate()
    cached.get_events("nba")
    assert inner.calls == 2
