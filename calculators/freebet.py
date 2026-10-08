"""Stake-not-returned (SNR) free-bet calculator.

A free bet's stake is not returned if the free bet wins. The lay stake is
chosen so the two outcomes produce the same result; the resulting profit is
the "converted" value of the free bet.

All monetary values are ``Decimal``. ``commission`` is a fraction (e.g. 0.02).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .decimal_utils import to_decimal

ONE = Decimal("1")
ZERO = Decimal("0")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class FreeBetResult:
    free_stake: Decimal
    back_odds: Decimal
    lay_odds: Decimal
    commission: Decimal
    lay_stake: Decimal
    liability: Decimal
    profit_if_back: Decimal
    profit_if_lay: Decimal
    expected_profit: Decimal
    conversion_rate: Decimal
    required_capital: Decimal
    lay_fee: Optional[Decimal] = None


def _validate_odds(value: Decimal, name: str) -> None:
    if value <= ONE:
        raise ValueError(f"{name} must be greater than 1.0")


def calculate_free_bet_snr(
    free_stake,
    back_odds,
    lay_odds,
    commission,
) -> FreeBetResult:
    """Calculate an SNR free bet.

    ``lay_stake = free_stake * (back_odds - 1) / (lay_odds - commission)``
    """
    stake = to_decimal(free_stake)
    b = to_decimal(back_odds)
    l = to_decimal(lay_odds)
    c = to_decimal(commission)

    if stake <= ZERO:
        raise ValueError("free_stake must be greater than 0")
    _validate_odds(b, "back_odds")
    _validate_odds(l, "lay_odds")
    if c < ZERO:
        raise ValueError("commission cannot be negative")
    if c >= ONE:
        raise ValueError("commission must be less than 1.0 (100%)")

    lay_stake = (stake * (b - ONE)) / (l - c)
    liability = lay_stake * (l - ONE)
    profit_if_back = stake * (b - ONE) - liability
    profit_if_lay = lay_stake * (ONE - c)
    expected_profit = (profit_if_back + profit_if_lay) / Decimal(2)
    conversion_rate = expected_profit / stake * HUNDRED
    required_capital = liability

    return FreeBetResult(
        free_stake=stake,
        back_odds=b,
        lay_odds=l,
        commission=c,
        lay_stake=lay_stake,
        liability=liability,
        profit_if_back=profit_if_back,
        profit_if_lay=profit_if_lay,
        expected_profit=expected_profit,
        conversion_rate=conversion_rate,
        required_capital=required_capital,
    )
