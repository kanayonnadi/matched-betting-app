from datetime import datetime, timezone
from decimal import Decimal

import pytest

from models import Event, ExchangeOdds, Market, MarketType, NormalizedOdds, Side

START = datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def back_quote(**overrides):
    data = dict(
        provider="example_book",
        event_id="123",
        sport="basketball",
        league="NBA",
        home_team="New York Knicks",
        away_team="Toronto Raptors",
        start_time=START,
        market="moneyline",
        selection="Toronto Raptors",
        side=Side.BACK,
        decimal_odds=Decimal("3.20"),
        timestamp=NOW,
    )
    data.update(overrides)
    return NormalizedOdds(**data)


def test_normalized_back_quote_is_decimal():
    q = back_quote(decimal_odds=3.20)
    assert q.decimal_odds == Decimal("3.2")
    assert q.side is Side.BACK


def test_side_coerced_from_string():
    q = back_quote(side="back")
    assert q.side is Side.BACK


def test_exchange_quote_forces_lay_and_requires_fields():
    q = ExchangeOdds(
        provider="exchange",
        event_id="123",
        sport="basketball",
        league="NBA",
        home_team="New York Knicks",
        away_team="Toronto Raptors",
        start_time=START,
        market="moneyline",
        selection="Toronto Raptors",
        side=Side.BACK,
        decimal_odds=Decimal("3.25"),
        timestamp=NOW,
        available_size=Decimal("450.00"),
        commission=Decimal("0.02"),
    )
    assert q.side is Side.LAY
    assert q.available_size == Decimal("450.00")
    assert q.commission == Decimal("0.02")
    assert isinstance(q, NormalizedOdds)


@pytest.mark.parametrize("odds", [1, 0.5, -2])
def test_invalid_odds_rejected(odds):
    with pytest.raises(ValueError):
        back_quote(decimal_odds=odds)


@pytest.mark.parametrize("field", ["event_id", "provider", "sport", "market", "selection"])
def test_empty_required_fields_rejected(field):
    with pytest.raises(ValueError):
        back_quote(**{field: "  "})


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError):
        back_quote(timestamp=datetime(2026, 11, 1, 12, 0))


def test_naive_start_time_rejected():
    with pytest.raises(ValueError):
        back_quote(start_time=datetime(2026, 11, 1, 19, 30))


def _exchange(**overrides):
    data = dict(
        provider="exchange",
        event_id="123",
        sport="basketball",
        league="NBA",
        home_team="Knicks",
        away_team="Raptors",
        start_time=START,
        market="moneyline",
        selection="Raptors",
        side=Side.LAY,
        decimal_odds=Decimal("3.25"),
        timestamp=NOW,
        available_size=Decimal("100"),
        commission=Decimal("0.02"),
    )
    data.update(overrides)
    return ExchangeOdds(**data)


@pytest.mark.parametrize("size", [0, -5])
def test_invalid_available_size_rejected(size):
    with pytest.raises(ValueError):
        _exchange(available_size=size)


@pytest.mark.parametrize("commission", [-0.01, 1.0, 1.5])
def test_invalid_commission_rejected(commission):
    with pytest.raises(ValueError):
        _exchange(commission=commission)


def test_event_is_tz_aware():
    event = Event(
        provider="book",
        event_id="1",
        sport="basketball",
        league="NBA",
        home_team="Knicks",
        away_team="Raptors",
        start_time=START,
    )
    assert event.start_time == START
    with pytest.raises(ValueError):
        Event(
            provider="book",
            event_id="1",
            sport="basketball",
            league="NBA",
            home_team="Knicks",
            away_team="Raptors",
            start_time=datetime(2026, 11, 1, 19, 30),
        )


def test_market_type_coerced_from_string():
    market = Market(
        provider="book",
        event_id="1",
        name="Moneyline",
        market_type="moneyline",
        selections=["Knicks", "Raptors"],
    )
    assert market.market_type is MarketType.MONEYLINE
    assert market.selections == ("Knicks", "Raptors")
