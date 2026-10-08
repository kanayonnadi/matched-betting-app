from decimal import Decimal

import pytest

from calculators import (
    calculate_free_bet_snr_per_wager,
    calculate_qualifying_per_wager,
    fee_rate,
)

TOL = Decimal("1e-12")


def test_qualifying_per_wager_known_case():
    result = calculate_qualifying_per_wager(100, 2.0, 2.0, Decimal("0.10"))
    assert result.lay_stake == Decimal("100")  # no commission in denominator
    assert result.liability == Decimal("100")
    assert result.lay_fee == Decimal("5")  # 0.10 * 100 * (2-1)/2
    assert result.profit_if_back == Decimal("-5")
    assert result.profit_if_lay == Decimal("-5")
    assert result.qualifying_loss == Decimal("5")


def test_matches_stx_published_example():
    # 50 contracts at $0.60, max $1, taker 0.10 -> fee $1.20
    price = Decimal("0.6")
    max_price = Decimal("1")
    lay_odds = max_price / price
    contracts = Decimal("50")
    lay_stake = contracts * price  # 30
    result = calculate_qualifying_per_wager(lay_stake, lay_odds, lay_odds, Decimal("0.10"))
    expected_fee = Decimal("0.10") * contracts * price * (max_price - price) / max_price
    assert expected_fee == Decimal("1.20")
    assert result.lay_fee == expected_fee


def test_free_bet_per_wager():
    result = calculate_free_bet_snr_per_wager(10, 2.0, 2.0, Decimal("0.10"))
    assert result.lay_stake == Decimal("5")
    assert result.lay_fee == Decimal("0.25")
    assert result.profit_if_back == result.profit_if_lay
    assert result.conversion_rate == Decimal("47.5")


def test_fee_rate_formula():
    assert fee_rate(Decimal("2.0"), Decimal("0.10")) == Decimal("0.05")


def test_per_wager_validates_factor():
    with pytest.raises(ValueError):
        calculate_qualifying_per_wager(100, 2.0, 2.0, Decimal("1.0"))
    with pytest.raises(ValueError):
        calculate_qualifying_per_wager(100, 2.0, 2.0, Decimal("-0.1"))


def test_zero_factor_matches_no_fee():
    result = calculate_qualifying_per_wager(100, 2.0, 2.0, Decimal("0"))
    assert result.lay_fee == Decimal("0")
    assert result.profit_if_back == Decimal("0")
    assert result.profit_if_lay == Decimal("0")
