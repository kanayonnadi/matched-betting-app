from datetime import datetime, timezone
from decimal import Decimal

import pytest

from calculators import (
    calculate_free_bet_snr_with_execution,
    calculate_qualifying_with_execution,
)
from liquidity import build_order_book, simulate_execution

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
AVALANCHE_BIDS = [
    {"price": "0.6200", "quantity": "461.00"},
    {"price": "0.6100", "quantity": "399.00"},
    {"price": "0.5900", "quantity": "399.00"},
]


def execution(required, fee_factor="0.10"):
    book = build_order_book(AVALANCHE_BIDS, "1", NOW)
    return simulate_execution(book, required, Decimal(fee_factor), now=NOW)


def test_qualifying_through_depth():
    ex = execution(480)
    result = calculate_qualifying_with_execution(300, "1.60", ex)
    assert result.lay_stake == Decimal("297.41")
    assert result.liability == Decimal("182.59")
    assert result.lay_fee == Decimal("11.32")
    # both outcomes equalise through the fill vector
    assert result.profit_if_back == result.profit_if_lay
    assert result.qualifying_loss == Decimal("13.91")
    assert result.required_capital == Decimal("482.59")


def test_free_bet_through_depth():
    ex = execution(150)
    result = calculate_free_bet_snr_with_execution(50, "4.0", ex)
    assert result.lay_stake == Decimal("93.00")   # 150 * 0.62
    assert result.lay_fee == Decimal("3.54")
    assert result.profit_if_back == result.profit_if_lay
    assert result.required_capital == result.liability


def test_multilevel_effective_odds_used():
    ex = execution(480)
    result = calculate_qualifying_with_execution(300, "1.60", ex)
    assert result.lay_odds == ex.effective_lay_odds
    assert result.lay_odds > Decimal("1.6129")  # worse than best level


def test_depth_invalid_stake():
    ex = execution(480)
    with pytest.raises(ValueError):
        calculate_qualifying_with_execution(0, "1.60", ex)
    with pytest.raises(ValueError):
        calculate_free_bet_snr_with_execution(50, "1.0", ex)
