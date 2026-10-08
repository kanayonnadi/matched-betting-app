"""Decimal helpers for money and odds calculations.

Money and odds must not be computed with binary floating point. These helpers
convert user/UI values into ``Decimal`` and provide explicit, opt-in rounding
for display only. Intermediate calculations are never silently rounded.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, getcontext

getcontext().prec = 28

MONEY_QUANTUM = Decimal("0.01")
ODDS_QUANTUM = Decimal("0.001")
PERCENT_QUANTUM = Decimal("0.01")


def to_decimal(value) -> Decimal:
    """Convert a value to Decimal without exposing binary float artefacts.

    ``float`` inputs are routed through ``str`` so that ``2.24`` becomes
    ``Decimal("2.24")`` rather than the nearest binary approximation.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("booleans are not valid numeric inputs")
    if isinstance(value, float):
        return Decimal(str(value))
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TypeError(f"cannot convert {value!r} to Decimal") from exc


def quantize_money(value: Decimal) -> Decimal:
    """Round a monetary amount to cents (for display/persistence only)."""
    return to_decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def quantize_odds(value: Decimal) -> Decimal:
    """Round decimal odds to three places (for display only)."""
    return to_decimal(value).quantize(ODDS_QUANTUM, rounding=ROUND_HALF_UP)
