"""Opportunity scanning with polling, caching and freshness.

A scanner runs discovery on a cadence (``scan_interval_seconds``) and reuses the
last result in between, so a UI rerun does not re-hit the odds sources. The
result records when the scan happened so callers can show how fresh it is.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

from opportunities import BetKind, discover_opportunities
from providers import MockExchangeProvider, MockSportsbookProvider

from .freshness import ODDS_STALE_SECONDS, is_stale


@dataclass(frozen=True)
class ScanResult:
    opportunities: tuple
    scanned_at: datetime
    source: str

    def is_stale(self, now=None, max_age_seconds: float = ODDS_STALE_SECONDS) -> bool:
        return is_stale(self.scanned_at, now=now, max_age_seconds=max_age_seconds)


class OpportunityScanner:
    def __init__(
        self,
        book_provider,
        exchange_provider,
        sports: Sequence[str],
        stake,
        kind: BetKind = BetKind.QUALIFYING,
        scan_interval_seconds: float = ODDS_STALE_SECONDS,
        clock=None,
    ):
        self._book_provider = book_provider
        self._exchange_provider = exchange_provider
        self._sports = tuple(sports)
        self._stake = stake
        self._kind = kind
        self._interval = scan_interval_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._last: Optional[ScanResult] = None
        self._last_at: Optional[datetime] = None

    def _source(self) -> str:
        live = not isinstance(self._book_provider, MockSportsbookProvider) or not isinstance(
            self._exchange_provider, MockExchangeProvider
        )
        return "live" if live else "mock"

    def scan(self, force: bool = False) -> ScanResult:
        now = self._clock()
        if (
            not force
            and self._last is not None
            and self._last_at is not None
            and (now - self._last_at).total_seconds() < self._interval
        ):
            return self._last

        opportunities = discover_opportunities(
            self._book_provider,
            self._exchange_provider,
            self._sports,
            self._stake,
            kind=self._kind,
        )
        self._last = ScanResult(tuple(opportunities), now, self._source())
        self._last_at = now
        return self._last
