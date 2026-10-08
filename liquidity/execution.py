"""Depth-aware execution simulator.

Given a lay-side :class:`~liquidity.models.OrderBook` and a required number of
contracts, walk the book best-price-first, producing an :class:`ExecutionEstimate`
with the fill vector, lay stake, liability and STX per-wager fee (ceil to the
cent once across the order).

Required contracts for a matched bet are fixed by the stake and odds, independent
of execution price:

* qualifying: ``Q = back_stake * back_odds / max_price``
* SNR free bet: ``Q = free_stake * (back_odds - 1) / max_price``
"""

from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from typing import Optional

from .models import ExecutionEstimate, Fill, OrderBook

ONE = Decimal("1")
ZERO = Decimal("0")
HUNDRED = Decimal("100")
CENT = Decimal("0.01")


def _to_decimal(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def required_contracts(back_stake, back_odds, max_price) -> Decimal:
    stake = _to_decimal(back_stake)
    odds = _to_decimal(back_odds)
    price = _to_decimal(max_price)
    if stake <= ZERO:
        raise ValueError("back_stake must be greater than 0")
    if odds <= ONE:
        raise ValueError("back_odds must be greater than 1.0")
    if price <= ZERO:
        raise ValueError("max_price must be greater than 0")
    return stake * odds / price


def required_contracts_free_bet(free_stake, back_odds, max_price) -> Decimal:
    stake = _to_decimal(free_stake)
    odds = _to_decimal(back_odds)
    price = _to_decimal(max_price)
    if stake <= ZERO:
        raise ValueError("free_stake must be greater than 0")
    if odds <= ONE:
        raise ValueError("back_odds must be greater than 1.0")
    if price <= ZERO:
        raise ValueError("max_price must be greater than 0")
    return stake * (odds - ONE) / price


def _ceil_cent(value: Decimal) -> Decimal:
    if value <= ZERO:
        return ZERO
    return value.quantize(CENT, rounding=ROUND_CEILING)


def simulate_execution(
    book: OrderBook,
    required_contracts,
    fee_factor=ZERO,
    now: Optional[datetime] = None,
) -> ExecutionEstimate:
    required = _to_decimal(required_contracts)
    if required <= ZERO:
        raise ValueError("required_contracts must be greater than 0")

    factor = _to_decimal(fee_factor)
    max_price = book.max_price

    remaining = required
    fills = []
    for level in book.levels:
        if remaining <= ZERO:
            break
        take = min(remaining, level.contracts)
        if take <= ZERO:
            continue
        fills.append(Fill(price=level.price, contracts=take, max_price=max_price))
        remaining -= take

    fills = tuple(fills)
    fillable = required - remaining
    fill_ratio = fillable / required

    lay_stake = sum((fill.lay_stake for fill in fills), ZERO)
    liability = sum((fill.liability for fill in fills), ZERO)

    raw_fee = (factor / max_price) * sum(
        (fill.contracts * fill.price * (max_price - fill.price) for fill in fills), ZERO
    )
    estimated_fee = _ceil_cent(raw_fee)

    if fillable > ZERO:
        effective_price = lay_stake / fillable
        effective_lay_odds = max_price / effective_price
    else:
        effective_lay_odds = None

    best_lay_odds = book.best.lay_odds if book.best else None
    worst_lay_odds = fills[-1].lay_odds if fills else None

    if best_lay_odds is not None and effective_lay_odds is not None and best_lay_odds > ZERO:
        slippage_pct = (effective_lay_odds - best_lay_odds) / best_lay_odds * HUNDRED
    else:
        slippage_pct = ZERO

    depth_ratio = book.total_contracts / required if required > ZERO else ZERO

    reference = now or datetime.now(timezone.utc)
    book_age_seconds = None
    if book.timestamp is not None:
        book_age_seconds = Decimal(str(max((reference - book.timestamp).total_seconds(), 0.0)))

    return ExecutionEstimate(
        requested_contracts=required,
        fillable_contracts=fillable,
        fill_ratio=fill_ratio,
        fills=fills,
        levels_consumed=len(fills),
        best_lay_odds=best_lay_odds,
        effective_lay_odds=effective_lay_odds,
        worst_lay_odds=worst_lay_odds,
        lay_stake=lay_stake,
        liability=liability,
        estimated_fee=estimated_fee,
        slippage_pct=slippage_pct,
        fully_fillable=fill_ratio >= ONE,
        shortfall_contracts=remaining,
        total_depth_contracts=book.total_contracts,
        depth_ratio=depth_ratio,
        book_timestamp=book.timestamp,
        completeness=book.completeness,
        book_age_seconds=book_age_seconds,
    )
