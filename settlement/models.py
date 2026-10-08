"""Settlement reconciliation models.

Predictions are immutable snapshots captured when a bet is recorded. Actual
execution/settlement is stored separately. Reconciliation compares the *matched*
predicted scenario against actuals and never treats missing data as reconciled.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, Tuple


class SettledOutcome(str, Enum):
    BACK_WON = "BACK_WON"
    LAY_WON = "LAY_WON"
    VOID = "VOID"
    PUSH = "PUSH"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class ReconciliationStatus(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    WITHIN_TOLERANCE = "WITHIN_TOLERANCE"
    DISCREPANCY = "DISCREPANCY"
    MISSING_DATA = "MISSING_DATA"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class PredictionSnapshot:
    bet_id: int
    sportsbook: str
    exchange: str
    event: str
    market: str
    selection: str
    predicted_back_stake: Decimal
    predicted_back_odds: Decimal
    predicted_liability: Decimal
    predicted_exchange_fee: Decimal
    predicted_back_win_pnl: Decimal
    predicted_lay_win_pnl: Decimal
    predicted_total_capital: Decimal
    predicted_qualifying_loss: Decimal
    predicted_lay_contracts: Optional[Decimal] = None
    predicted_lay_odds: Optional[Decimal] = None
    predicted_fills: Tuple[Tuple[Decimal, Decimal], ...] = ()
    max_price: Optional[Decimal] = None
    fee_factor: Optional[Decimal] = None
    calculation_version: str = "m19.0"
    fee_model_version: str = "stx-per-wager-v1"
    timestamp: Optional[datetime] = None
    source_provenance: Optional[object] = None


@dataclass(frozen=True)
class ActualSettlement:
    bet_id: int
    outcome: str = SettledOutcome.UNKNOWN.value
    actual_back_stake: Optional[Decimal] = None
    actual_back_odds: Optional[Decimal] = None
    actual_lay_contracts: Optional[Decimal] = None
    actual_fill_prices: Tuple[Tuple[Decimal, Decimal], ...] = ()
    actual_liability: Optional[Decimal] = None
    actual_exchange_fee: Optional[Decimal] = None
    actual_sportsbook_settlement: Optional[Decimal] = None
    actual_exchange_settlement: Optional[Decimal] = None
    actual_total_pnl: Optional[Decimal] = None
    settled_at: Optional[datetime] = None
    source: str = "manual"
    settlement_key: Optional[str] = None


@dataclass(frozen=True)
class Difference:
    metric: str
    predicted: Optional[Decimal]
    actual: Optional[Decimal]
    difference: Optional[Decimal]


@dataclass(frozen=True)
class ReconciliationResult:
    bet_id: int
    status: str
    predicted_outcome: str
    predicted_pnl: Optional[Decimal]
    actual_pnl: Optional[Decimal]
    pnl_difference: Optional[Decimal]
    fee_difference: Optional[Decimal]
    liability_difference: Optional[Decimal]
    contracts_difference: Optional[Decimal]
    differences: Tuple[Difference, ...] = ()
    flags: Tuple[str, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class FeeValidation:
    computed_fee: Optional[Decimal]
    declared_fee: Optional[Decimal]
    difference: Optional[Decimal]
    matches: Optional[bool]
    note: str = ""
