"""Normalized domain models for odds ingestion.

Providers return these types regardless of the wire format of their source.
Internally all odds are decimal and all money is ``Decimal``. Databases and
exchange data use the same schema so matching and opportunity code can consume
any provider.

This module intentionally has no Streamlit or network dependencies.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from calculators.decimal_utils import to_decimal

ONE = Decimal("1")
ZERO = Decimal("0")


class Side(str, Enum):
    """Which side of the market a quote represents."""

    BACK = "back"
    LAY = "lay"


class MarketType(str, Enum):
    """Market families V2 can match reliably.

    Spreads, totals and player props are deliberately excluded until the
    matching engine is proven.
    """

    MONEYLINE = "moneyline"
    TWO_WAY = "two_way"
    THREE_WAY = "three_way"


def _require_aware(value, name):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")


def _require_non_empty(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class Event:
    """A sporting event offered by one provider."""

    provider: str
    event_id: str
    sport: str
    league: str
    home_team: str
    away_team: str
    start_time: datetime

    def __post_init__(self):
        for name in ("provider", "event_id", "sport", "home_team", "away_team"):
            _require_non_empty(getattr(self, name), name)
        _require_aware(self.start_time, "start_time")


@dataclass(frozen=True)
class Market:
    """A betting market belonging to an event."""

    provider: str
    event_id: str
    name: str
    market_type: MarketType
    selections: tuple = ()

    def __post_init__(self):
        _require_non_empty(self.provider, "provider")
        _require_non_empty(self.event_id, "event_id")
        _require_non_empty(self.name, "name")
        if not isinstance(self.market_type, MarketType):
            object.__setattr__(self, "market_type", MarketType(self.market_type))
        object.__setattr__(self, "selections", tuple(self.selections))


@dataclass(frozen=True)
class NormalizedOdds:
    """A single normalized back/lay quote.

    Sportsbook quotes use this class directly. Exchange quotes use the
    ``ExchangeOdds`` subclass, which adds liquidity and commission.
    """

    provider: str
    event_id: str
    sport: str
    league: str
    home_team: str
    away_team: str
    start_time: datetime
    market: str
    selection: str
    side: Side
    decimal_odds: Decimal
    timestamp: datetime

    def __post_init__(self):
        for name in (
            "provider",
            "event_id",
            "sport",
            "home_team",
            "away_team",
            "market",
            "selection",
        ):
            _require_non_empty(getattr(self, name), name)
        if not isinstance(self.side, Side):
            object.__setattr__(self, "side", Side(self.side))
        _require_aware(self.start_time, "start_time")
        _require_aware(self.timestamp, "timestamp")
        odds = to_decimal(self.decimal_odds)
        if odds <= ONE:
            raise ValueError("decimal_odds must be greater than 1.0")
        object.__setattr__(self, "decimal_odds", odds)


@dataclass(frozen=True)
class ExchangeOdds(NormalizedOdds):
    """An exchange lay quote with liquidity and commission.

    The side is fixed to ``LAY`` because exchanges quote the lay side for
    matched betting; ``available_size`` and ``commission`` are mandatory.
    """

    available_size: Decimal
    commission: Decimal
    fee_model: str = "commission"
    fee_factor: Optional[Decimal] = None
    max_price: Optional[Decimal] = None

    def __post_init__(self):
        object.__setattr__(self, "side", Side.LAY)
        super().__post_init__()
        size = to_decimal(self.available_size)
        if size <= ZERO:
            raise ValueError("available_size must be greater than 0")
        object.__setattr__(self, "available_size", size)
        commission = to_decimal(self.commission)
        if commission < ZERO or commission >= ONE:
            raise ValueError("commission must be in [0, 1)")
        object.__setattr__(self, "commission", commission)
        if self.fee_model not in ("commission", "per_wager"):
            raise ValueError("fee_model must be 'commission' or 'per_wager'")
        if self.fee_factor is not None:
            factor = to_decimal(self.fee_factor)
            if factor < ZERO or factor >= ONE:
                raise ValueError("fee_factor must be in [0, 1)")
            object.__setattr__(self, "fee_factor", factor)
        if self.max_price is not None:
            max_price = to_decimal(self.max_price)
            if max_price <= ZERO:
                raise ValueError("max_price must be greater than 0")
            object.__setattr__(self, "max_price", max_price)
