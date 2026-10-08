from decimal import Decimal

import pytest

from calculators import calculate_free_bet_snr

TOLERANCE = Decimal("1e-12")


def assert_close(a: Decimal, b: Decimal):
    assert abs(a - b) <= TOLERANCE, f"{a} != {b}"


def test_clean_snr_example():
    r = calculate_free_bet_snr(10, 5.0, 5.0, 0)
    assert r.lay_stake == Decimal("8")
    assert r.liability == Decimal("32")
    assert r.profit_if_back == Decimal("8")
    assert r.profit_if_lay == Decimal("8")
    assert r.expected_profit == Decimal("8")
    assert r.conversion_rate == Decimal("80")
    assert r.required_capital == Decimal("32")


def test_commission_makes_outcomes_equal():
    r = calculate_free_bet_snr(50, 3.0, 3.1, Decimal("0.02"))
    assert_close(r.profit_if_back, r.profit_if_lay)
    assert r.expected_profit > 0


def test_free_stake_is_not_returned_on_back_win():
    r = calculate_free_bet_snr(25, 4.0, 4.2, 0)
    assert_close(r.profit_if_back, r.profit_if_lay)
    # lay liability must be staked even though the back stake is free
    assert r.required_capital == r.liability


def test_return_types_are_decimal():
    r = calculate_free_bet_snr("10", "5", "5", "0")
    for value in (
        r.lay_stake,
        r.liability,
        r.profit_if_back,
        r.profit_if_lay,
        r.expected_profit,
        r.conversion_rate,
        r.required_capital,
    ):
        assert isinstance(value, Decimal)


def test_no_silent_rounding_of_lay_stake():
    r = calculate_free_bet_snr(10, 5, 5, 0)
    expected = Decimal("10") * (Decimal("5") - Decimal("1")) / Decimal("5")
    assert r.lay_stake == expected


@pytest.mark.parametrize(
    "args",
    [
        (0, 5.0, 5.0, 0.0),
        (-1, 5.0, 5.0, 0.0),
        (10, 1.0, 5.0, 0.0),
        (10, 5.0, 1.0, 0.0),
        (10, 5.0, 5.0, -0.01),
        (10, 5.0, 5.0, 1.0),
    ],
)
def test_invalid_inputs_rejected(args):
    with pytest.raises(ValueError):
        calculate_free_bet_snr(*args)
