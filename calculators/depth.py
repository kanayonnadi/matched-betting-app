"""Depth-aware matched-betting calculations.

These consume an execution estimate (any object exposing ``lay_stake``,
``liability``, ``estimated_fee`` and ``effective_lay_odds``) produced by the
liquidity engine, so the P&L reflects the *actual* fills rather than a headline
single-level price. Kept free of any ``liquidity`` import (duck-typed) to avoid
coupling; the engine wires the two together.
"""

from decimal import Decimal

from .decimal_utils import to_decimal
from .freebet import FreeBetResult
from .qualifying import QualifyingResult

ONE = Decimal("1")
ZERO = Decimal("0")
HUNDRED = Decimal("100")


def _effective_odds(execution) -> Decimal:
    value = getattr(execution, "effective_lay_odds", None)
    return to_decimal(value) if value is not None else ZERO


def calculate_qualifying_with_execution(back_stake, back_odds, execution) -> QualifyingResult:
    stake = to_decimal(back_stake)
    b = to_decimal(back_odds)
    if stake <= ZERO:
        raise ValueError("back_stake must be greater than 0")
    if b <= ONE:
        raise ValueError("back_odds must be greater than 1.0")

    lay_stake = to_decimal(execution.lay_stake)
    liability = to_decimal(execution.liability)
    fee = to_decimal(execution.estimated_fee)

    profit_if_back = stake * (b - ONE) - liability - fee
    profit_if_lay = lay_stake - stake - fee
    expected_profit = (profit_if_back + profit_if_lay) / Decimal(2)
    qualifying_loss = -expected_profit

    return QualifyingResult(
        back_stake=stake,
        back_odds=b,
        lay_odds=_effective_odds(execution),
        commission=ZERO,
        lay_stake=lay_stake,
        liability=liability,
        profit_if_back=profit_if_back,
        profit_if_lay=profit_if_lay,
        expected_profit=expected_profit,
        qualifying_loss=qualifying_loss,
        qualifying_loss_pct=qualifying_loss / stake * HUNDRED,
        required_capital=stake + liability,
        lay_fee=fee,
    )


def calculate_free_bet_snr_with_execution(free_stake, back_odds, execution) -> FreeBetResult:
    stake = to_decimal(free_stake)
    b = to_decimal(back_odds)
    if stake <= ZERO:
        raise ValueError("free_stake must be greater than 0")
    if b <= ONE:
        raise ValueError("back_odds must be greater than 1.0")

    lay_stake = to_decimal(execution.lay_stake)
    liability = to_decimal(execution.liability)
    fee = to_decimal(execution.estimated_fee)

    profit_if_back = stake * (b - ONE) - liability - fee
    profit_if_lay = lay_stake - fee
    expected_profit = (profit_if_back + profit_if_lay) / Decimal(2)

    return FreeBetResult(
        free_stake=stake,
        back_odds=b,
        lay_odds=_effective_odds(execution),
        commission=ZERO,
        lay_stake=lay_stake,
        liability=liability,
        profit_if_back=profit_if_back,
        profit_if_lay=profit_if_lay,
        expected_profit=expected_profit,
        conversion_rate=expected_profit / stake * HUNDRED,
        required_capital=liability,
        lay_fee=fee,
    )
