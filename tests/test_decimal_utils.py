from decimal import Decimal

import pytest

from calculators import quantize_money, quantize_odds, to_decimal


def test_float_conversion_avoids_binary_artefacts():
    assert to_decimal(0.1) == Decimal("0.1")
    assert to_decimal(2.24) == Decimal("2.24")


def test_decimal_passthrough():
    value = Decimal("1.23")
    assert to_decimal(value) is value


def test_int_and_string_conversion():
    assert to_decimal(5) == Decimal("5")
    assert to_decimal("1.5") == Decimal("1.5")


def test_boolean_rejected():
    with pytest.raises(TypeError):
        to_decimal(True)


def test_invalid_value_rejected():
    with pytest.raises(TypeError):
        to_decimal("not-a-number")


def test_quantize_money_rounds_to_cents():
    assert quantize_money(Decimal("1.005")) == Decimal("1.01")
    assert quantize_money(Decimal("1.004")) == Decimal("1.00")


def test_quantize_odds_rounds_to_three_places():
    assert quantize_odds(Decimal("2.3456")) == Decimal("2.346")
