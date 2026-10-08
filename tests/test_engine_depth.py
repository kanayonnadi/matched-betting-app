from datetime import datetime, timezone
from decimal import Decimal

from liquidity import build_order_book
from models import ExchangeOdds, NormalizedOdds, Side
from opportunities import build_opportunity

START = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
BIDS = [
    {"price": "0.6200", "quantity": "461.00"},
    {"price": "0.6100", "quantity": "399.00"},
    {"price": "0.5900", "quantity": "399.00"},
]


def book_quote(odds="1.60"):
    return NormalizedOdds(
        provider="theoddsapi:betano_ca_on",
        event_id="book-1",
        sport="ice_hockey",
        league="NHL",
        home_team="Colorado Avalanche",
        away_team="Winnipeg Jets",
        start_time=START,
        market="moneyline",
        selection="Colorado Avalanche",
        side=Side.BACK,
        decimal_odds=Decimal(odds),
        timestamp=NOW,
    )


def exchange_quote():
    return ExchangeOdds(
        provider="stx",
        event_id="stx-1",
        sport="ice_hockey",
        league="NHL",
        home_team="Colorado Avalanche",
        away_team="Winnipeg Jets",
        start_time=START,
        market="moneyline",
        selection="Colorado Avalanche",
        side=Side.LAY,
        decimal_odds=Decimal("1.6129"),
        timestamp=NOW,
        available_size=Decimal("285.82"),
        commission=Decimal("0"),
        fee_model="per_wager",
        fee_factor=Decimal("0.10"),
        max_price=Decimal("1"),
    )


def test_engine_depth_path():
    order_book = build_order_book(BIDS, "1", NOW, completeness="COMPLETE")
    opp = build_opportunity(book_quote(), exchange_quote(), 300, order_book=order_book, now=NOW)
    assert opp.execution is not None
    assert opp.fully_hedged is True
    assert opp.levels_consumed == 2
    assert opp.liquidity_grade == "HIGH"
    assert opp.qualifying_loss == Decimal("13.91")
    assert opp.effective_lay_odds > Decimal("1.6129")
    assert opp.liquidity_sufficient is True
    assert opp.lay_fee == Decimal("11.32")


def test_engine_depth_insufficient():
    order_book = build_order_book(BIDS, "1", NOW, completeness="COMPLETE")
    opp = build_opportunity(book_quote(), exchange_quote(), 1000, order_book=order_book, now=NOW)
    assert opp.fully_hedged is False
    assert opp.liquidity_grade == "INSUFFICIENT"
    assert opp.liquidity_sufficient is False
    assert opp.execution.shortfall_contracts == Decimal("341")  # 1600 - 1259


def test_engine_without_order_book_preserves_headline_behaviour():
    opp = build_opportunity(book_quote(), exchange_quote(), 300)
    assert opp.execution is None
    assert opp.liquidity_grade is None
    assert opp.fully_hedged is None
    assert opp.lay_odds == Decimal("1.6129")
