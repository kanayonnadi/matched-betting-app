"""Arbitrage mathematics for mutually exclusive outcomes.

An arbitrage exists when the sum of the inverse decimal odds is below 1. Stakes
that equalise the payout across every outcome are then guaranteed to profit.

All values are ``Decimal``; results are ``None`` when there is no arbitrage.
"""

from dataclasses import dataclass
from decimal import Decimal

from .decimal_utils import to_decimal

ONE = Decimal("1")
ZERO = Decimal("0")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class ArbitrageResult:
    outcome_odds: tuple
    stakes: tuple
    total_stake: Decimal
    payout: Decimal
    sum_inverses: Decimal
    profit: Decimal
    roi_percent: Decimal


def calculate_arbitrage(odds, total_stake):
    values = tuple(to_decimal(value) for value in odds)
    stake = to_decimal(total_stake)

    if len(values) < 2:
        raise ValueError("at least two outcomes are required")
    if any(value <= ONE for value in values):
        raise ValueError("odds must be greater than 1")
    if stake <= ZERO:
        raise ValueError("total_stake must be greater than 0")

    inverses = tuple(ONE / value for value in values)
    total_inverse = sum(inverses, ZERO)
    if total_inverse >= ONE:
        return None

    stakes = tuple(stake * inverse / total_inverse for inverse in inverses)
    payout = stake / total_inverse
    profit = payout - stake
    roi = profit / stake * HUNDRED

    return ArbitrageResult(
        outcome_odds=values,
        stakes=stakes,
        total_stake=stake,
        payout=payout,
        sum_inverses=total_inverse,
        profit=profit,
        roi_percent=roi,
    )
