"""Bankroll-aware promotion ranking and greedy sequencing."""

from decimal import Decimal

from .models import Valuation

ZERO = Decimal("0")

SORT_KEYS = {
    "highest_profit": lambda valuation: valuation.expected_net_profit,
    "best_roi": lambda valuation: valuation.roi_on_capital,
    "lowest_cost": lambda valuation: -valuation.expected_qualifying_loss,
    "lowest_capital": lambda valuation: -valuation.peak_capital,
    "confidence": lambda valuation: valuation.confidence,
}


def rank_promotions(valuations, sort_by="highest_profit", only_ready=False, free_capital=None):
    items = list(valuations)
    if only_ready:
        items = [valuation for valuation in items if valuation.ready]
    if free_capital is not None:
        items = [valuation for valuation in items if valuation.peak_capital <= free_capital]
    key = SORT_KEYS.get(sort_by, SORT_KEYS["highest_profit"])
    return sorted(items, key=key, reverse=True)


def sequence_promotions(valuations, bankroll):
    """Greedy sequential plan: each promotion must fit the bankroll on its own.

    Capital is assumed released when an offer completes, so a simple greedy list
    ordered by expected net profit is a sound first pass (no ML).
    """
    bankroll = Decimal(str(bankroll))
    ready = [
        valuation
        for valuation in valuations
        if valuation.ready and valuation.peak_capital <= bankroll
    ]
    ready.sort(key=lambda valuation: valuation.expected_net_profit, reverse=True)
    total = sum((valuation.expected_net_profit for valuation in ready), ZERO)
    return ready, total
