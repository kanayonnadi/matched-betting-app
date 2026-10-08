"""Calculations for exchanges that charge a per-wager fee (e.g. STX).

STX charges ``fee = ceil(factor * contracts * price * (max_price - price) / max_price)``
on **every** wager, independent of the outcome (taker factor, maker 0). With lay
odds ``L = max_price / bid`` that reduces to ``fee = rate * lay_stake`` where
``rate = factor * (L - 1) / L``.

Because the fee does not depend on which side wins, the equalising lay stake has
no commission term in the denominator:

* qualifying: ``lay_stake = back_stake * back_odds / lay_odds``
* SNR free bet: ``lay_stake = free_stake * (back_odds - 1) / lay_odds``

The fee is subtracted from both outcomes, so both stay equal.
"""

from decimal import Decimal

from .decimal_utils import to_decimal
from .freebet import FreeBetResult
from .qualifying import QualifyingResult

ONE = Decimal("1")
ZERO = Decimal("0")
HUNDRED = Decimal("100")


def fee_rate(lay_odds: Decimal, fee_factor: Decimal) -> Decimal:
    """Fee per unit of lay stake: ``factor * (L - 1) / L``."""
    return fee_factor * (lay_odds - ONE) / lay_odds


def _validate(fee_factor: Decimal) -> None:
    if fee_factor < ZERO:
        raise ValueError("fee_factor cannot be negative")
    if fee_factor >= ONE:
        raise ValueError("fee_factor must be less than 1.0")


def calculate_qualifying_per_wager(
    back_stake, back_odds, lay_odds, fee_factor
) -> QualifyingResult:
    stake = to_decimal(back_stake)
    b = to_decimal(back_odds)
    l = to_decimal(lay_odds)
    factor = to_decimal(fee_factor)

    if stake <= ZERO:
        raise ValueError("back_stake must be greater than 0")
    if b <= ONE:
        raise ValueError("back_odds must be greater than 1.0")
    if l <= ONE:
        raise ValueError("lay_odds must be greater than 1.0")
    _validate(factor)

    lay_stake = stake * b / l
    liability = lay_stake * (l - ONE)
    lay_fee = fee_rate(l, factor) * lay_stake
    profit_if_back = stake * (b - ONE) - liability - lay_fee
    profit_if_lay = lay_stake - stake - lay_fee
    expected_profit = (profit_if_back + profit_if_lay) / Decimal(2)
    qualifying_loss = -expected_profit

    return QualifyingResult(
        back_stake=stake,
        back_odds=b,
        lay_odds=l,
        commission=ZERO,
        lay_stake=lay_stake,
        liability=liability,
        profit_if_back=profit_if_back,
        profit_if_lay=profit_if_lay,
        expected_profit=expected_profit,
        qualifying_loss=qualifying_loss,
        qualifying_loss_pct=qualifying_loss / stake * HUNDRED,
        required_capital=stake + liability,
        lay_fee=lay_fee,
    )


def calculate_free_bet_snr_per_wager(
    free_stake, back_odds, lay_odds, fee_factor
) -> FreeBetResult:
    stake = to_decimal(free_stake)
    b = to_decimal(back_odds)
    l = to_decimal(lay_odds)
    factor = to_decimal(fee_factor)

    if stake <= ZERO:
        raise ValueError("free_stake must be greater than 0")
    if b <= ONE:
        raise ValueError("back_odds must be greater than 1.0")
    if l <= ONE:
        raise ValueError("lay_odds must be greater than 1.0")
    _validate(factor)

    lay_stake = stake * (b - ONE) / l
    liability = lay_stake * (l - ONE)
    lay_fee = fee_rate(l, factor) * lay_stake
    profit_if_back = stake * (b - ONE) - liability - lay_fee
    profit_if_lay = lay_stake - lay_fee
    expected_profit = (profit_if_back + profit_if_lay) / Decimal(2)

    return FreeBetResult(
        free_stake=stake,
        back_odds=b,
        lay_odds=l,
        commission=ZERO,
        lay_stake=lay_stake,
        liability=liability,
        profit_if_back=profit_if_back,
        profit_if_lay=profit_if_lay,
        expected_profit=expected_profit,
        conversion_rate=expected_profit / stake * HUNDRED,
        required_capital=liability,
        lay_fee=lay_fee,
    )
