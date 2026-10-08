from datetime import datetime, timezone
from decimal import Decimal

import pytest

from models import Event, Market, MarketType, NormalizedOdds, Side
from providers import EventNotFoundError
from providers.aggregate import MultiSportsbookProvider

START = datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


class FakeBook:
    def __init__(self, name, event_id):
        self.name = name
        self._event_id = event_id

    def get_events(self, sport):
        return (
            Event(
                provider=self.name,
                event_id=self._event_id,
                sport=sport,
                league="NBA",
                home_team="New York Knicks",
                away_team="Toronto Raptors",
                start_time=START,
            ),
        )

    def get_markets(self, event_id):
        return (
            Market(
                provider=self.name,
                event_id=event_id,
                name="moneyline",
                market_type=MarketType.MONEYLINE,
                selections=("New York Knicks", "Toronto Raptors"),
            ),
        )

    def get_odds(self, event_id, market):
        return (
            NormalizedOdds(
                provider=self.name,
                event_id=event_id,
                sport="basketball",
                league="NBA",
                home_team="New York Knicks",
                away_team="Toronto Raptors",
                start_time=START,
                market="moneyline",
                selection="New York Knicks",
                side=Side.BACK,
                decimal_odds=Decimal("1.85"),
                timestamp=NOW,
            ),
        )


def test_requires_at_least_one_provider():
    with pytest.raises(ValueError):
        MultiSportsbookProvider([])


def test_get_events_combines_and_namespaces():
    provider = MultiSportsbookProvider([FakeBook("book_a", "same-id"), FakeBook("book_b", "same-id")])
    events = provider.get_events("basketball")
    assert len(events) == 2
    assert {e.event_id for e in events} == {"0:same-id", "1:same-id"}
    assert {e.provider for e in events} == {"book_a", "book_b"}


def test_routes_markets_and_odds_to_the_right_book():
    provider = MultiSportsbookProvider([FakeBook("book_a", "same-id"), FakeBook("book_b", "same-id")])
    events = provider.get_events("basketball")
    first, second = events[0], events[1]

    markets = provider.get_markets(second.event_id)
    assert markets[0].provider == "book_b"

    odds = provider.get_odds(first.event_id, "moneyline")
    assert odds[0].provider == "book_a"
    assert odds[0].decimal_odds == Decimal("1.85")


def test_unknown_namespaced_event_raises():
    provider = MultiSportsbookProvider([FakeBook("book_a", "evt")])
    with pytest.raises(EventNotFoundError):
        provider.get_odds("9:missing", "moneyline")
