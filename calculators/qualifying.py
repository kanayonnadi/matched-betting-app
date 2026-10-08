"""Qualifying-bet calculator.

A qualifying bet backs a selection at a bookmaker and lays the same selection
on an exchange, so both outcomes produce (almost) the same result. The lay
stake is chosen to equalise the two outcomes.

All monetary values are ``Decimal``. ``commission`` is expressed as a fraction
(e.g. ``0.02`` for 2%), not a percentage.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .decimal_utils import to_decimal

ONE = Decimal("1")
ZERO = Decimal("0")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class QualifyingResult:
    back_stake: Decimal
    back_odds: Decimal
    lay_odds: Decimal
    commission: Decimal
    lay_stake: Decimal
    liability: Decimal
    profit_if_back: Decimal
    profit_if_lay: Decimal
    expected_profit: Decimal
    qualifying_loss: Decimal
    qualifying_loss_pct: Decimal
    required_capital: Decimal
    lay_fee: Optional[Decimal] = None


def _validate_odds(value: Decimal, name: str) -> None:
    if value <= ONE:
        raise ValueError(f"{name} must be greater than 1.0")


def calculate_qualifying(
    back_stake,
    back_odds,
    lay_odds,
    commission,
) -> QualifyingResult:
    """Calculate a standard qualifying matched bet.

    ``lay_stake = back_stake * back_odds / (lay_odds - commission)``
    """
    stake = to_decimal(back_stake)
    b = to_decimal(back_odds)
    l = to_decimal(lay_odds)
    c = to_decimal(commission)

    if stake <= ZERO:
        raise ValueError("back_stake must be greater than 0")
    _validate_odds(b, "back_odds")
    _validate_odds(l, "lay_odds")
    if c < ZERO:
        raise ValueError("commission cannot be negative")
    if c >= ONE:
        raise ValueError("commission must be less than 1.0 (100%)")

    lay_stake = (stake * b) / (l - c)
    liability = lay_stake * (l - ONE)
    profit_if_back = stake * (b - ONE) - liability
    profit_if_lay = lay_stake * (ONE - c) - stake
    expected_profit = (profit_if_back + profit_if_lay) / Decimal(2)
    qualifying_loss = -expected_profit
    qualifying_loss_pct = qualifying_loss / stake * HUNDRED
    required_capital = stake + liability

    return QualifyingResult(
        back_stake=stake,
        back_odds=b,
        lay_odds=l,
        commission=c,
        lay_stake=lay_stake,
        liability=liability,
        profit_if_back=profit_if_back,
        profit_if_lay=profit_if_lay,
        expected_profit=expected_profit,
        qualifying_loss=qualifying_loss,
        qualifying_loss_pct=qualifying_loss_pct,
        required_capital=required_capital,
    )
