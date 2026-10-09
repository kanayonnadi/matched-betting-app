"""Domain models for exchange depth and execution.

STX prices are dollars in ``[0, max_price]`` (``max_price`` is the settlement
value of one contract). Quantities are contracts. A lay sells contracts into the
book's bids (highest price first); the resulting lay odds for a level are
``max_price / price``.

All money is ``Decimal``.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class BookLevel:
    """One aggregated price level on the lay side (a bid)."""

    price: Decimal
    contracts: Decimal
    max_price: Decimal

    def __post_init__(self):
        if self.price <= ZERO:
            raise ValueError("price must be greater than 0")
        if self.contracts <= ZERO:
            raise ValueError("contracts must be greater than 0")
        if self.max_price <= ZERO:
            raise ValueError("max_price must be greater than 0")

    @property
    def lay_odds(self) -> Decimal:
        return self.max_price / self.price

    @property
    def liquidity(self) -> Decimal:
        """Dollars available at this level (price x contracts)."""
        return self.price * self.contracts


class DepthCompleteness(str, Enum):
    """Whether an observed book is the full book or may be truncated."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    INVALID = "INVALID"


class DepthStatus(str, Enum):
    """Whether a hedge is genuinely executable from observed depth.

    VERIFIED      observed levels fully cover the required quantity.
    INSUFFICIENT  a complete book is observed but cannot cover the quantity.
    UNKNOWN       the book may be truncated and observed levels do not cover it.
    STALE         the book exceeded the freshness threshold.
    INVALID       malformed/contradictory depth that cannot be interpreted.
    """

    VERIFIED = "VERIFIED"
    INSUFFICIENT = "INSUFFICIENT"
    UNKNOWN = "UNKNOWN"
    STALE = "STALE"
    INVALID = "INVALID"


@dataclass(frozen=True)
class OrderBook:
    """Lay-side order book, best (highest price) first."""

    max_price: Decimal
    levels: tuple
    timestamp: Optional[datetime] = None
    source: str = "stx"
    completeness: str = DepthCompleteness.UNKNOWN.value
    malformed_levels: int = 0

    def __post_init__(self):
        ordered = tuple(sorted(self.levels, key=lambda level: level.price, reverse=True))
        object.__setattr__(self, "levels", ordered)

    @property
    def best(self) -> Optional[BookLevel]:
        return self.levels[0] if self.levels else None

    @property
    def total_contracts(self) -> Decimal:
        return sum((level.contracts for level in self.levels), ZERO)

    @property
    def total_liquidity(self) -> Decimal:
        return sum((level.liquidity for level in self.levels), ZERO)

    @property
    def is_empty(self) -> bool:
        return not self.levels


@dataclass(frozen=True)
class Fill:
    price: Decimal
    contracts: Decimal
    max_price: Decimal

    @property
    def lay_odds(self) -> Decimal:
        return self.max_price / self.price

    @property
    def lay_stake(self) -> Decimal:
        """Dollars won if the lay side wins (price x contracts)."""
        return self.price * self.contracts

    @property
    def liability(self) -> Decimal:
        return self.contracts * (self.max_price - self.price)


@dataclass(frozen=True)
class ExecutionEstimate:
    requested_contracts: Decimal
    fillable_contracts: Decimal
    fill_ratio: Decimal
    fills: tuple
    levels_consumed: int
    best_lay_odds: Optional[Decimal]
    effective_lay_odds: Optional[Decimal]
    worst_lay_odds: Optional[Decimal]
    lay_stake: Decimal
    liability: Decimal
    estimated_fee: Decimal
    slippage_pct: Decimal
    fully_fillable: bool
    shortfall_contracts: Decimal
    total_depth_contracts: Decimal
    depth_ratio: Decimal
    book_timestamp: Optional[datetime]
    completeness: str = DepthCompleteness.UNKNOWN.value
    depth_status: str = DepthStatus.UNKNOWN.value
    book_age_seconds: Optional[Decimal] = None


class LiquidityGrade(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"
