"""Projected, locked-in and realized profit (M24.1, Phase 7).

Projected = current quotes + estimated fees/slippage (an estimate).
Locked-in = outcome-dependent P&L from confirmed accepted wagers and fills.
Realized  = from actual settlement records only (never projected expected profit).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple

ZERO = Decimal("0")


@dataclass(frozen=True)
class ProfitView:
    projected: Decimal
    projected_note: str
    locked_back_win: Optional[Decimal]
    locked_lay_win: Optional[Decimal]
    realized: Decimal


def projected_profit(opportunity) -> Decimal:
    """Worst-case quoted result after estimated fees (an estimate, not a fill)."""
    return min(opportunity.profit_if_back, opportunity.profit_if_lay)


def locked_in_range(opportunity) -> Tuple[Decimal, Decimal]:
    """Outcome-dependent P&L from the confirmed fills (back win, lay win)."""
    return opportunity.profit_if_back, opportunity.profit_if_lay


def realized_profit(promotion_id, db_path=None) -> dict:
    from .promotion_workflow import calculate_net_profit

    net = calculate_net_profit(promotion_id, db_path=db_path)
    net["realized"] = net.get("realized_net", ZERO)
    return net


def profit_view(promotion_id, opportunity=None, db_path=None) -> ProfitView:
    if opportunity is not None:
        projected = projected_profit(opportunity)
        back_win, lay_win = locked_in_range(opportunity)
        note = "estimate from current quotes including estimated STX fees"
    else:
        projected = ZERO
        back_win = lay_win = None
        note = "no confirmed hedge"
    return ProfitView(
        projected=projected,
        projected_note=note,
        locked_back_win=back_win,
        locked_lay_win=lay_win,
        realized=realized_profit(promotion_id, db_path=db_path)["realized"] if promotion_id else ZERO,
    )
