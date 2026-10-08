"""TTL cache for odds providers.

Wrapping a provider prevents a Streamlit rerun (or a scanner poll) from hitting
the source API on every interaction. Entries expire after ``ttl_seconds`` so a
quote is never served longer than the freshness window.
"""

from datetime import datetime, timezone

from .base import OddsProvider


class CachedProvider(OddsProvider):
    def __init__(self, provider: OddsProvider, ttl_seconds: float = 30, clock=None):
        self._provider = provider
        self._ttl = ttl_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._cache = {}
        self.name = getattr(provider, "name", "cached")

    def _cached(self, key, producer):
        now = self._clock()
        entry = self._cache.get(key)
        if entry is not None and (now - entry[0]).total_seconds() < self._ttl:
            return entry[1]
        value = producer()
        self._cache[key] = (now, value)
        return value

    def get_events(self, sport: str):
        return self._cached(("events", sport), lambda: self._provider.get_events(sport))

    def get_markets(self, event_id: str):
        return self._cached(("markets", event_id), lambda: self._provider.get_markets(event_id))

    def get_odds(self, event_id: str, market: str):
        return self._cached(
            ("odds", event_id, market), lambda: self._provider.get_odds(event_id, market)
        )

    @property
    def source_type(self) -> str:
        return getattr(self._provider, "source_type", "MOCK")

    def invalidate(self) -> None:
        self._cache.clear()

    def __getattr__(self, name):
        # Delegate extra provider capabilities (e.g. get_order_book, market_ids)
        # to the wrapped provider.
        return getattr(self.__dict__["_provider"], name)
