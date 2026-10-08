from decimal import Decimal

import pytest

from calculators import calculate_qualifying

TOLERANCE = Decimal("1e-12")


def assert_close(a: Decimal, b: Decimal):
    assert abs(a - b) <= TOLERANCE, f"{a} != {b}"


def test_clean_odds_no_commission():
    r = calculate_qualifying(100, 2.0, 2.0, 0)
    assert r.lay_stake == Decimal("100")
    assert r.liability == Decimal("100")
    assert r.profit_if_back == Decimal("0")
    assert r.profit_if_lay == Decimal("0")
    assert r.qualifying_loss == Decimal("0")
    assert r.required_capital == Decimal("200")


def test_commission_makes_both_outcomes_equal():
    r = calculate_qualifying(100, 2.0, 2.0, Decimal("0.02"))
    assert_close(r.profit_if_back, r.profit_if_lay)
    assert r.qualifying_loss > 0
    assert r.profit_if_back < 0


def test_return_types_are_decimal():
    r = calculate_qualifying("100", "3.2", "3.25", "0.02")
    for value in (
        r.lay_stake,
        r.liability,
        r.profit_if_back,
        r.profit_if_lay,
        r.expected_profit,
        r.qualifying_loss,
        r.qualifying_loss_pct,
        r.required_capital,
    ):
        assert isinstance(value, Decimal)


def test_float_inputs_are_not_binary_polluted():
    r = calculate_qualifying(100.0, 2.24, 2.30, 0.02)
    assert r.back_odds == Decimal("2.24")
    assert r.lay_odds == Decimal("2.30")
    assert r.commission == Decimal("0.02")


def test_outcomes_equal_for_realistic_values():
    r = calculate_qualifying(50, 4.5, 4.7, Decimal("0.02"))
    assert_close(r.profit_if_back, r.profit_if_lay)
    assert r.required_capital == r.back_stake + r.liability


def test_qualifying_loss_pct_matches_absolute_loss():
    r = calculate_qualifying(100, 3.2, 3.25, Decimal("0.02"))
    expected_pct = r.qualifying_loss / Decimal("100") * Decimal("100")
    assert r.qualifying_loss_pct == expected_pct


def test_no_silent_rounding_of_lay_stake():
    r = calculate_qualifying(100, 3.2, 3.25, Decimal("0.02"))
    assert r.lay_stake == (Decimal("100") * Decimal("3.2")) / (
        Decimal("3.25") - Decimal("0.02")
    )


@pytest.mark.parametrize(
    "args",
    [
        (0, 2.0, 2.0, 0.0),
        (-5, 2.0, 2.0, 0.0),
        (100, 1.0, 2.0, 0.0),
        (100, 0.5, 2.0, 0.0),
        (100, 2.0, 1.0, 0.0),
        (100, 2.0, 2.0, -0.01),
        (100, 2.0, 2.0, 1.0),
    ],
)
def test_invalid_inputs_rejected(args):
    with pytest.raises(ValueError):
        calculate_qualifying(*args)


def test_boolean_input_rejected():
    with pytest.raises(TypeError):
        calculate_qualifying(True, 2.0, 2.0, 0.0)
