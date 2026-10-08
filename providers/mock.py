"""In-memory mock odds providers.

These adapters let the whole pipeline run end-to-end without API credentials.
They are deterministic: the event catalogue is fixed and timestamps come from an
injectable clock (defaulting to UTC now), so tests can make freshness
deterministic.

The catalogue deliberately gives the book and the exchange different event ids
and slightly different team labels (e.g. "New York Knicks" vs "Knicks"). That
keeps the later event-matching engine honest instead of relying on shared ids.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from models import Event, ExchangeOdds, Market, MarketType, NormalizedOdds, Side

from .base import EventNotFoundError, OddsProvider

DEFAULT_COMMISSION = Decimal("0.02")
DEFAULT_LIQUIDITY = Decimal("500.00")


@dataclass(frozen=True)
class MockOutcome:
    book_label: str
    exchange_label: str
    back_odds: Decimal
    lay_odds: Decimal


@dataclass(frozen=True)
class MockEvent:
    book_id: str
    exchange_id: str
    sport: str
    league: str
    book_home: str
    book_away: str
    exchange_home: str
    exchange_away: str
    start_time: datetime
    market: str
    market_type: MarketType
    outcomes: tuple


MOCK_EVENTS = (
    MockEvent(
        book_id="kb-1001",
        exchange_id="kx-1001",
        sport="basketball",
        league="NBA",
        book_home="New York Knicks",
        book_away="Toronto Raptors",
        exchange_home="Knicks",
        exchange_away="Raptors",
        start_time=datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc),
        market="moneyline",
        market_type=MarketType.MONEYLINE,
        outcomes=(
            MockOutcome("New York Knicks", "Knicks", Decimal("1.85"), Decimal("1.90")),
            MockOutcome("Toronto Raptors", "Raptors", Decimal("2.10"), Decimal("2.16")),
        ),
    ),
    MockEvent(
        book_id="kb-1002",
        exchange_id="kx-1002",
        sport="basketball",
        league="NBA",
        book_home="Los Angeles Lakers",
        book_away="Boston Celtics",
        exchange_home="Lakers",
        exchange_away="Celtics",
        start_time=datetime(2026, 11, 1, 22, 0, tzinfo=timezone.utc),
        market="moneyline",
        market_type=MarketType.MONEYLINE,
        outcomes=(
            MockOutcome("Los Angeles Lakers", "Lakers", Decimal("2.40"), Decimal("2.46")),
            MockOutcome("Boston Celtics", "Celtics", Decimal("1.60"), Decimal("1.64")),
        ),
    ),
    MockEvent(
        book_id="kb-1003",
        exchange_id="kx-1003",
        sport="basketball",
        league="NBA",
        book_home="Golden State Warriors",
        book_away="Miami Heat",
        exchange_home="Warriors",
        exchange_away="Heat",
        start_time=datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc),
        market="moneyline",
        market_type=MarketType.MONEYLINE,
        outcomes=(
            MockOutcome("Golden State Warriors", "Warriors", Decimal("1.70"), Decimal("1.74")),
            MockOutcome("Miami Heat", "Heat", Decimal("2.15"), Decimal("2.22")),
        ),
    ),
    MockEvent(
        book_id="kb-2001",
        exchange_id="kx-2001",
        sport="soccer",
        league="Premier League",
        book_home="Arsenal",
        book_away="Chelsea",
        exchange_home="Arsenal",
        exchange_away="Chelsea",
        start_time=datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc),
        market="match_winner",
        market_type=MarketType.THREE_WAY,
        outcomes=(
            MockOutcome("Arsenal", "Arsenal", Decimal("2.20"), Decimal("2.26")),
            MockOutcome("Draw", "The Draw", Decimal("3.40"), Decimal("3.50")),
            MockOutcome("Chelsea", "Chelsea", Decimal("3.10"), Decimal("3.20")),
        ),
    ),
)


_ALT_BACK_ODDS = {
    ("kb-1001", "New York Knicks"): Decimal("2.15"),
    ("kb-1001", "Toronto Raptors"): Decimal("1.80"),
    ("kb-1002", "Los Angeles Lakers"): Decimal("2.30"),
    ("kb-1002", "Boston Celtics"): Decimal("1.70"),
    ("kb-1003", "Golden State Warriors"): Decimal("1.78"),
    ("kb-1003", "Miami Heat"): Decimal("2.05"),
    ("kb-2001", "Arsenal"): Decimal("2.30"),
    ("kb-2001", "Draw"): Decimal("3.30"),
    ("kb-2001", "Chelsea"): Decimal("3.00"),
}


def _build_alt_events():
    events = []
    for event in MOCK_EVENTS:
        outcomes = tuple(
            replace(
                outcome,
                back_odds=_ALT_BACK_ODDS.get(
                    (event.book_id, outcome.book_label), outcome.back_odds
                ),
            )
            for outcome in event.outcomes
        )
        events.append(replace(event, outcomes=outcomes))
    return tuple(events)


MOCK_EVENTS_ALT = _build_alt_events()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _MockProviderBase(OddsProvider):
    def __init__(self, events: Sequence[MockEvent] = MOCK_EVENTS, clock=_utc_now):
        self._clock = clock
        self._events = tuple(events)
        self._by_book_id = {event.book_id: event for event in self._events}
        self._by_exchange_id = {event.exchange_id: event for event in self._events}

    def _lookup(self, index: dict, event_id: str) -> MockEvent:
        event = index.get(event_id)
        if event is None:
            raise EventNotFoundError(f"{self.name}: unknown event id {event_id!r}")
        return event


class MockSportsbookProvider(_MockProviderBase):
    """A mock sportsbook returning BACK quotes."""

    name = "mock_book"

    def _event(self, event_id: str) -> MockEvent:
        return self._lookup(self._by_book_id, event_id)

    def get_events(self, sport: str) -> Sequence[Event]:
        return tuple(
            Event(
                provider=self.name,
                event_id=event.book_id,
                sport=event.sport,
                league=event.league,
                home_team=event.book_home,
                away_team=event.book_away,
                start_time=event.start_time,
            )
            for event in self._events
            if event.sport == sport
        )

    def get_markets(self, event_id: str) -> Sequence[Market]:
        event = self._event(event_id)
        return (
            Market(
                provider=self.name,
                event_id=event.book_id,
                name=event.market,
                market_type=event.market_type,
                selections=tuple(outcome.book_label for outcome in event.outcomes),
            ),
        )

    def get_odds(self, event_id: str, market: str) -> Sequence[NormalizedOdds]:
        event = self._event(event_id)
        if market != event.market:
            return ()
        timestamp = self._clock()
        return tuple(
            NormalizedOdds(
                provider=self.name,
                event_id=event.book_id,
                sport=event.sport,
                league=event.league,
                home_team=event.book_home,
                away_team=event.book_away,
                start_time=event.start_time,
                market=event.market,
                selection=outcome.book_label,
                side=Side.BACK,
                decimal_odds=outcome.back_odds,
                timestamp=timestamp,
            )
            for outcome in event.outcomes
        )


class MockSecondSportsbookProvider(MockSportsbookProvider):
    """A second mock book with different prices, so cross-book arbitrage exists."""

    name = "mock_book_b"

    def __init__(self, clock=_utc_now):
        super().__init__(events=MOCK_EVENTS_ALT, clock=clock)


class MockExchangeProvider(_MockProviderBase):
    """A mock exchange returning LAY quotes with liquidity and commission."""

    name = "mock_exchange"

    def __init__(
        self,
        events: Sequence[MockEvent] = MOCK_EVENTS,
        clock=_utc_now,
        commission: Decimal = DEFAULT_COMMISSION,
        liquidity: Decimal = DEFAULT_LIQUIDITY,
    ):
        super().__init__(events=events, clock=clock)
        self._commission = commission
        self._liquidity = liquidity

    def _event(self, event_id: str) -> MockEvent:
        return self._lookup(self._by_exchange_id, event_id)

    def get_events(self, sport: str) -> Sequence[Event]:
        return tuple(
            Event(
                provider=self.name,
                event_id=event.exchange_id,
                sport=event.sport,
                league=event.league,
                home_team=event.exchange_home,
                away_team=event.exchange_away,
                start_time=event.start_time,
            )
            for event in self._events
            if event.sport == sport
        )

    def get_markets(self, event_id: str) -> Sequence[Market]:
        event = self._event(event_id)
        return (
            Market(
                provider=self.name,
                event_id=event.exchange_id,
                name=event.market,
                market_type=event.market_type,
                selections=tuple(outcome.exchange_label for outcome in event.outcomes),
            ),
        )

    def get_odds(self, event_id: str, market: str) -> Sequence[NormalizedOdds]:
        event = self._event(event_id)
        if market != event.market:
            return ()
        timestamp = self._clock()
        return tuple(
            ExchangeOdds(
                provider=self.name,
                event_id=event.exchange_id,
                sport=event.sport,
                league=event.league,
                home_team=event.exchange_home,
                away_team=event.exchange_away,
                start_time=event.start_time,
                market=event.market,
                selection=outcome.exchange_label,
                side=Side.LAY,
                decimal_odds=outcome.lay_odds,
                timestamp=timestamp,
                available_size=self._liquidity,
                commission=self._commission,
            )
            for outcome in event.outcomes
        )
