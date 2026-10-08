from datetime import datetime, timezone
from decimal import Decimal

import pytest

from models import ExchangeOdds, MarketType, NormalizedOdds, Side
from providers import (
    EventNotFoundError,
    MockExchangeProvider,
    MockSportsbookProvider,
    OddsProvider,
)

FIXED = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def book():
    return MockSportsbookProvider(clock=lambda: FIXED)


@pytest.fixture
def exchange():
    return MockExchangeProvider(clock=lambda: FIXED)


def test_both_are_odds_providers(book, exchange):
    assert isinstance(book, OddsProvider)
    assert isinstance(exchange, OddsProvider)
    assert book.name == "mock_book"
    assert exchange.name == "mock_exchange"


def test_get_events_filters_by_sport(book, exchange):
    nba = book.get_events("basketball")
    assert len(nba) == 3
    assert all(e.provider == "mock_book" for e in nba)
    assert {e.event_id for e in nba} == {"kb-1001", "kb-1002", "kb-1003"}

    soccer = book.get_events("soccer")
    assert len(soccer) == 1
    assert soccer[0].event_id == "kb-2001"


def test_exchange_ids_differ_from_book_ids(exchange):
    ids = {e.event_id for e in exchange.get_events("basketball")}
    assert ids == {"kx-1001", "kx-1002", "kx-1003"}


def test_unknown_sport_returns_empty(book):
    assert book.get_events("cricket") == ()


def test_book_odds_are_back_decimals(book):
    odds = book.get_odds("kb-1001", "moneyline")
    assert len(odds) == 2
    assert all(q.side is Side.BACK for q in odds)
    assert all(not isinstance(q, ExchangeOdds) for q in odds)
    knicks = next(q for q in odds if q.selection == "New York Knicks")
    assert knicks.decimal_odds == Decimal("1.85")
    assert knicks.timestamp == FIXED


def test_exchange_odds_are_lay_with_liquidity(book, exchange):
    odds = exchange.get_odds("kx-1001", "moneyline")
    assert len(odds) == 2
    assert all(isinstance(q, ExchangeOdds) for q in odds)
    assert all(q.side is Side.LAY for q in odds)
    assert all(q.commission == Decimal("0.02") for q in odds)
    assert all(q.available_size == Decimal("500.00") for q in odds)
    raptors = next(q for q in odds if q.selection == "Raptors")
    assert raptors.decimal_odds == Decimal("2.16")


def test_three_way_market_has_three_selections(book):
    markets = book.get_markets("kb-2001")
    assert len(markets) == 1
    assert markets[0].market_type is MarketType.THREE_WAY
    assert markets[0].selections == ("Arsenal", "Draw", "Chelsea")

    odds = book.get_odds("kb-2001", "match_winner")
    assert len(odds) == 3


def test_two_way_market_type(book):
    assert book.get_markets("kb-1001")[0].market_type is MarketType.MONEYLINE


def test_unknown_event_raises(book, exchange):
    with pytest.raises(EventNotFoundError):
        book.get_markets("nope")
    with pytest.raises(EventNotFoundError):
        book.get_odds("nope", "moneyline")
    with pytest.raises(EventNotFoundError):
        exchange.get_odds("nope", "moneyline")


def test_unknown_market_returns_empty(book, exchange):
    assert book.get_odds("kb-1001", "totals") == ()
    assert exchange.get_odds("kx-1001", "totals") == ()


def test_clock_is_injectable(book, exchange):
    assert book.get_odds("kb-1001", "moneyline")[0].timestamp == FIXED
    assert exchange.get_odds("kx-1001", "moneyline")[0].timestamp == FIXED


def test_exchange_defaults_match_documented_values():
    provider = MockExchangeProvider()
    odds = provider.get_odds("kx-1001", "moneyline")
    assert odds[0].commission == Decimal("0.02")
    assert odds[0].available_size == Decimal("500.00")
    assert isinstance(odds[0], NormalizedOdds)
