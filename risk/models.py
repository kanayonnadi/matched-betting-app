"""Risk and stress-testing models."""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class HedgeStatus(str, Enum):
    HEDGED = "HEDGED"
    INCOMPLETE = "INCOMPLETE"
    NO_HEDGE = "NO_HEDGE"


@dataclass(frozen=True)
class ExposureResult:
    back_stake: Decimal
    back_odds: Decimal
    max_price: Decimal
    requested_contracts: Decimal
    filled_contracts: Decimal
    fill_ratio: Decimal
    unhedged_contracts: Decimal
    unhedged_back_stake: Decimal
    lay_stake_filled: Decimal
    liability_filled: Decimal
    back_win_pnl: Decimal
    lay_win_pnl: Decimal
    worst_case_pnl: Decimal
    best_case_pnl: Decimal
    estimated_fee: Decimal
    additional_collateral_required: Decimal
    status: str
    risk: str
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AvailableCapital:
    balances: dict
    reserved: dict
    available: dict
    total_available: Decimal
    total_reserved: Decimal


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    expected: str
    actual: str
    passed: bool
    details: dict = field(default_factory=dict)
