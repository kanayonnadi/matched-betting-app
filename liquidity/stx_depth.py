"""Build :class:`~liquidity.models.OrderBook` from STX payloads.

Supports both shapes seen live:

* REST ``list_markets`` -> ``bids: [{price, quantity}]`` (dollar strings)
* WS ``market:<id>`` -> ``ob: {b: [{p, q, l, tc, tl}]}`` (JSON numbers)
* WS ``orderbook`` topic -> ``{bids: [{price, quantity, liquidity, ...}]}``
"""

from decimal import Decimal

from .models import BookLevel, DepthCompleteness, OrderBook

ZERO = Decimal("0")


def _decimal(value):
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _levels_from_pairs(pairs, max_price):
    levels = []
    malformed = 0
    for price_raw, quantity_raw in pairs:
        price = _decimal(price_raw)
        quantity = _decimal(quantity_raw)
        if price is None or quantity is None or price <= ZERO or quantity <= ZERO or price >= max_price:
            malformed += 1
            continue
        levels.append(BookLevel(price=price, contracts=quantity, max_price=max_price))
    return levels, malformed


def build_order_book(
    bids,
    max_price,
    timestamp=None,
    source="stx-rest",
    completeness: str = DepthCompleteness.UNKNOWN.value,
    *,
    received_at=None,
    source_timestamp=None,
    timestamp_source: str = "unknown",
    freshness_verified: bool = True,
):
    max_price = _decimal(max_price)
    if max_price is None or max_price <= ZERO:
        raise ValueError("max_price must be greater than 0")
    pairs = [(bid.get("price"), bid.get("quantity")) for bid in (bids or [])]
    levels, malformed = _levels_from_pairs(pairs, max_price)
    if malformed:
        completeness = DepthCompleteness.INVALID.value
    return OrderBook(
        max_price=max_price,
        levels=levels,
        timestamp=source_timestamp or received_at or timestamp,
        source=source,
        completeness=completeness,
        malformed_levels=malformed,
        received_at=received_at,
        source_timestamp=source_timestamp,
        timestamp_source=timestamp_source,
        freshness_verified=freshness_verified,
    )


def build_order_book_from_ws(
    ob,
    max_price,
    timestamp=None,
    source="stx-ws",
    completeness: str = DepthCompleteness.COMPLETE.value,
    *,
    received_at=None,
    source_timestamp=None,
    timestamp_source: str = "ws_received",
    freshness_verified: bool = True,
):
    """``ob`` is ``{"b": [{"p","q","l","tc","tl"}, ...]}`` (market channel)."""
    max_price = _decimal(max_price)
    if max_price is None or max_price <= ZERO:
        raise ValueError("max_price must be greater than 0")
    pairs = [((level or {}).get("p"), (level or {}).get("q")) for level in (ob or {}).get("b") or []]
    levels, malformed = _levels_from_pairs(pairs, max_price)
    if malformed:
        completeness = DepthCompleteness.INVALID.value
    return OrderBook(
        max_price=max_price,
        levels=levels,
        timestamp=source_timestamp or received_at or timestamp,
        source=source,
        completeness=completeness,
        malformed_levels=malformed,
        received_at=received_at,
        source_timestamp=source_timestamp,
        timestamp_source=timestamp_source,
        freshness_verified=freshness_verified,
    )
