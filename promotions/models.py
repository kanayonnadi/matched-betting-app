"""Promotion domain models.

A promotion is *structured* and carries provenance. Nothing is invented: any
field that the source text does not make explicit stays ``None`` and is recorded
in ``unknown_fields`` so it can be surfaced for review.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, Tuple


class PromotionStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    PARSED = "PARSED"
    TERMS_RETRIEVED = "TERMS_RETRIEVED"
    TERMS_VERIFIED = "TERMS_VERIFIED"
    ELIGIBILITY_CONFIRMED = "ELIGIBILITY_CONFIRMED"
    QUALIFICATION_PENDING = "QUALIFICATION_PENDING"
    QUALIFICATION_SETTLED = "QUALIFICATION_SETTLED"
    REWARD_PENDING = "REWARD_PENDING"
    REWARD_RECEIVED = "REWARD_RECEIVED"
    REWARD_USED = "REWARD_USED"
    # Legacy actionable states retained for backwards compatibility.
    VERIFIED = "VERIFIED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    INELIGIBLE = "INELIGIBLE"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class PromotionType(str, Enum):
    BET_GET = "BET_GET"
    FREE_BET_SNR = "FREE_BET_SNR"
    DEPOSIT_MATCH = "DEPOSIT_MATCH"
    PROFIT_BOOST = "PROFIT_BOOST"
    ODDS_BOOST = "ODDS_BOOST"
    BET_INSURANCE = "BET_INSURANCE"
    RISK_FREE = "RISK_FREE"
    RELOAD = "RELOAD"
    EXISTING_CUSTOMER = "EXISTING_CUSTOMER"
    OTHER = "OTHER"


class RewardType(str, Enum):
    FREE_BET_SNR = "FREE_BET_SNR"
    FREE_BET_SR = "FREE_BET_SR"
    CASH = "CASH"
    BONUS = "BONUS"
    UNKNOWN = "UNKNOWN"


class EligibilityStatus(str, Enum):
    UNKNOWN = "Unknown"
    ELIGIBLE = "Eligible"
    NOT_ELIGIBLE = "Not eligible"
    ALREADY_USED = "Already used"


ALL_STATUSES = tuple(status.value for status in PromotionStatus)
ALL_TYPES = tuple(item.value for item in PromotionType)


@dataclass(frozen=True)
class PromotionTerms:
    offer_type: Optional[str] = None
    qualifying_stake: Optional[Decimal] = None
    qualifying_min_odds: Optional[Decimal] = None
    qualifying_max_odds: Optional[Decimal] = None
    reward_amount: Optional[Decimal] = None
    reward_type: Optional[str] = None
    reward_count: Optional[int] = None
    stake_returned: Optional[bool] = None
    reward_expiry_days: Optional[int] = None
    new_customer_only: Optional[bool] = None
    jurisdiction: Optional[str] = None
    unknown_fields: Tuple[str, ...] = ()
    confidence: float = 0.0


CRITICAL_FIELDS = ("offer_type", "qualifying_stake", "reward_amount", "reward_type")


@dataclass(frozen=True)
class Promotion:
    id: Optional[int] = None
    sportsbook: str = ""
    name: str = ""
    jurisdiction: Optional[str] = None
    offer_type: Optional[str] = None
    new_customer_only: Optional[bool] = None
    qualifying_stake: Optional[Decimal] = None
    qualifying_min_odds: Optional[Decimal] = None
    qualifying_max_odds: Optional[Decimal] = None
    reward_amount: Optional[Decimal] = None
    reward_type: Optional[str] = None
    reward_count: int = 1
    stake_returned: Optional[bool] = None
    reward_expiry_days: Optional[int] = None
    eligible_sports: Tuple[str, ...] = ()
    excluded_markets: Tuple[str, ...] = ()
    deposit_requirement: Optional[Decimal] = None
    source_url: Optional[str] = None
    terms_text: Optional[str] = None
    discovered_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    status: str = PromotionStatus.DISCOVERED.value
    confidence: float = 0.0
    eligibility: str = EligibilityStatus.UNKNOWN.value
    # Verification / lifecycle (M20).
    official_source_url: Optional[str] = None
    terms_source_url: Optional[str] = None
    terms_verified_at: Optional[datetime] = None
    effective_date: Optional[str] = None
    lifecycle_status: str = PromotionStatus.DISCOVERED.value
    withdrawal_restrictions: Optional[str] = None
    wagering_requirement_text: Optional[str] = None
    min_deposit: Optional[Decimal] = None
    max_qualifying_stake: Optional[Decimal] = None

    @property
    def reward_token_amount(self) -> Decimal:
        if self.reward_amount is None:
            return Decimal("0")
        count = max(self.reward_count or 1, 1)
        return self.reward_amount / Decimal(count)


@dataclass(frozen=True)
class PromotionSource:
    promotion_id: Optional[int] = None
    url: Optional[str] = None
    text: Optional[str] = None
    note: Optional[str] = None
    source_type: str = "THIRD_PARTY"  # OFFICIAL | THIRD_PARTY | MANUAL
    jurisdiction: Optional[str] = None
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    version_hash: Optional[str] = None
    retrieved_at: Optional[datetime] = None


@dataclass(frozen=True)
class Valuation:
    promotion_id: Optional[int]
    sportsbook: str
    name: str
    status: str
    ready: bool
    expected_qualifying_loss: Decimal
    reward_token_value: Decimal
    expected_reward_value: Decimal
    expected_net_profit: Decimal
    peak_capital: Decimal
    roi_on_capital: Decimal
    confidence: float
    qualifying: Optional[object] = None
    conversions: Tuple[object, ...] = ()
    warnings: Tuple[str, ...] = field(default=())
