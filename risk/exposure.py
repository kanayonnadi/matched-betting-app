"""Partial-hedge exposure.

When a hedge is incomplete, the unhedged portion behaves like a straight back
bet. This computes the real exposure and worst-case P&L from the actual fill
vector — never assuming the original qualifying loss holds.
"""

from decimal import Decimal
from typing import Optional, Sequence

from .models import ExposureResult, HedgeStatus, RiskLevel

ZERO = Decimal("0")
ONE = Decimal("1")


def _d(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def compute_exposure(
    back_stake,
    back_odds,
    requested_contracts,
    fills: Sequence,
    max_price,
    fee=ZERO,
    reference_price=None,
) -> ExposureResult:
    stake = _d(back_stake)
    odds = _d(back_odds)
    required = _d(requested_contracts)
    maximum = _d(max_price)
    estimated_fee = _d(fee)

    filled = ZERO
    lay_stake = ZERO
    liability = ZERO
    for price_raw, contracts_raw in fills:
        price = _d(price_raw)
        contracts = _d(contracts_raw)
        filled += contracts
        lay_stake += price * contracts
        liability += contracts * (maximum - price)

    # The STX per-wager fee is charged regardless of outcome, so it reduces both.
    back_win_pnl = stake * (odds - ONE) - liability - estimated_fee
    lay_win_pnl = -stake + lay_stake - estimated_fee
    worst_case = min(back_win_pnl, lay_win_pnl)

    unhedged = max(required - filled, ZERO)
    fraction = unhedged / required if required > ZERO else ZERO
    unhedged_back_stake = stake * fraction

    if reference_price is not None:
        reference = _d(reference_price)
    elif fills:
        reference = _d(fills[-1][0])
    else:
        reference = None
    additional_collateral = (
        unhedged * (maximum - reference) if (unhedged > ZERO and reference is not None) else ZERO
    )

    if required > ZERO and filled >= required:
        status = HedgeStatus.HEDGED.value
        risk = RiskLevel.LOW.value
    elif filled <= ZERO:
        status = HedgeStatus.NO_HEDGE.value
        risk = RiskLevel.HIGH.value
    else:
        status = HedgeStatus.INCOMPLETE.value
        risk = RiskLevel.HIGH.value

    return ExposureResult(
        back_stake=stake,
        back_odds=odds,
        max_price=maximum,
        requested_contracts=required,
        filled_contracts=filled,
        fill_ratio=(filled / required if required > ZERO else ZERO),
        unhedged_contracts=unhedged,
        unhedged_back_stake=unhedged_back_stake,
        lay_stake_filled=lay_stake,
        liability_filled=liability,
        back_win_pnl=back_win_pnl,
        lay_win_pnl=lay_win_pnl,
        worst_case_pnl=worst_case,
        best_case_pnl=max(back_win_pnl, lay_win_pnl),
        estimated_fee=estimated_fee,
        additional_collateral_required=additional_collateral,
        status=status,
        risk=risk,
        details={
            "back_win_derivation": (
                f"stake*(odds-1) - liability - fee = {stake}*({odds}-1) - {liability} - {estimated_fee}"
            ),
            "lay_win_derivation": f"-stake + lay_stake - fee = -{stake} + {lay_stake} - {estimated_fee}",
            "worst_case_is_back_loss": worst_case == lay_win_pnl,
            "reference_price": str(reference) if reference is not None else None,
        },
    )
