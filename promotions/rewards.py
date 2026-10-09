"""Structured reward collections (M25.3).

A promotion may award one free bet **or several individual bonus bets** (e.g.
five $20 sports bonus bets). This module represents rewards as a collection of
individually-identified units with their own amount, status, expiry and
settlement link, instead of assuming ``reward_amount`` is a single wager.

Backward compatible: a single-reward promotion builds a collection with one
unit whose amount equals ``reward_amount``.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional, Sequence, Tuple

ZERO = Decimal("0")


class RewardStatus(str, Enum):
    PENDING = "PENDING"
    CREDITED = "CREDITED"
    RESERVED = "RESERVED"
    REDEEMED = "REDEEMED"
    SETTLED = "SETTLED"
    EXPIRED = "EXPIRED"
    VOIDED = "VOIDED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


ALL_REWARD_STATUSES = tuple(status.value for status in RewardStatus)

# Older records (and existing callers) used RECEIVED/USED/REJECTED. Map them to
# the canonical M25.3 states so nothing breaks.
LEGACY_STATUS_MAP = {
    "RECEIVED": RewardStatus.CREDITED.value,
    "USED": RewardStatus.REDEEMED.value,
    "REJECTED": RewardStatus.VOIDED.value,
}

# Forward-only lifecycle: PENDING -> CREDITED -> RESERVED -> REDEEMED -> SETTLED
LIFECYCLE_ORDER = (
    RewardStatus.PENDING.value,
    RewardStatus.CREDITED.value,
    RewardStatus.RESERVED.value,
    RewardStatus.REDEEMED.value,
    RewardStatus.SETTLED.value,
)
TERMINAL_STATUSES = (
    RewardStatus.SETTLED.value,
    RewardStatus.EXPIRED.value,
    RewardStatus.VOIDED.value,
    RewardStatus.CANCELLED.value,
    RewardStatus.FAILED.value,
)
# Statuses that mean the reward has actually been issued by the operator.
CREDITED_STATUSES = (
    RewardStatus.CREDITED.value,
    RewardStatus.RESERVED.value,
    RewardStatus.REDEEMED.value,
    RewardStatus.SETTLED.value,
)


def canonical_status(status: Optional[str]) -> str:
    if status is None:
        return RewardStatus.PENDING.value
    return LEGACY_STATUS_MAP.get(status, status)


def can_transition(current: Optional[str], target: str) -> bool:
    """Allow forward progress or a terminal flag; never un-settle."""
    current = canonical_status(current)
    target = canonical_status(target)
    if current == target:
        return True
    if current in TERMINAL_STATUSES:
        return False
    if target in TERMINAL_STATUSES:
        return True
    if current not in LIFECYCLE_ORDER or target not in LIFECYCLE_ORDER:
        return False
    return LIFECYCLE_ORDER.index(target) > LIFECYCLE_ORDER.index(current)


@dataclass(frozen=True)
class RewardUnit:
    reward_id: str
    amount: Decimal
    status: str = RewardStatus.PENDING.value
    expires_at: Optional[datetime] = None
    received_at: Optional[datetime] = None
    reserved_at: Optional[datetime] = None
    settled_at: Optional[datetime] = None
    linked_conversion_bet_id: Optional[int] = None
    realized_pnl: Optional[Decimal] = None
    note: Optional[str] = None

    @property
    def canonical_status(self) -> str:
        return canonical_status(self.status)

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        if self.expires_at is None:
            return False
        reference = now or datetime.now(self.expires_at.tzinfo or timezone.utc)
        return self.expires_at < reference

    def effective_status(self, now: Optional[datetime] = None) -> str:
        if self.canonical_status in CREDITED_STATUSES and self.is_expired(now):
            return RewardStatus.EXPIRED.value
        return self.canonical_status

    @property
    def is_active(self) -> bool:
        return self.canonical_status in (
            RewardStatus.CREDITED.value,
            RewardStatus.RESERVED.value,
            RewardStatus.REDEEMED.value,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.reward_id,
            "amount": str(self.amount),
            "status": self.status,
            "canonical_status": self.canonical_status,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "settled_at": self.settled_at.isoformat() if self.settled_at else None,
            "linked_conversion_bet_id": self.linked_conversion_bet_id,
            "realized_pnl": None if self.realized_pnl is None else str(self.realized_pnl),
        }


@dataclass(frozen=True)
class RewardCollection:
    reward_type: str
    rewards: Tuple[RewardUnit, ...] = ()
    expiry_hours: Optional[int] = None
    issue_after_settlement: Optional[bool] = None
    restrictions: Tuple[str, ...] = ()
    label: str = ""

    @property
    def count(self) -> int:
        return len(self.rewards)

    @property
    def total_face_value(self) -> Decimal:
        return sum((unit.amount for unit in self.rewards), ZERO)

    @property
    def unit_amount(self) -> Optional[Decimal]:
        """Return the per-unit amount when every reward is equal, else None."""
        if not self.rewards:
            return None
        first = self.rewards[0].amount
        return first if all(unit.amount == first for unit in self.rewards) else None

    @property
    def is_equal_split(self) -> bool:
        return self.unit_amount is not None

    def amounts(self) -> Tuple[Decimal, ...]:
        return tuple(unit.amount for unit in self.rewards)

    def distinct_amounts(self) -> Tuple[Decimal, ...]:
        seen = []
        for amount in self.amounts():
            if amount not in seen:
                seen.append(amount)
        return tuple(seen)

    def active_units(self, now: Optional[datetime] = None) -> Tuple[RewardUnit, ...]:
        return tuple(u for u in self.rewards if u.effective_status(now) in CREDITED_STATUSES)

    def unconverted_face_value(self, now: Optional[datetime] = None) -> Decimal:
        """Face value not yet redeemed or settled (never counted as profit)."""
        return sum(
            (
                u.amount
                for u in self.rewards
                if u.effective_status(now)
                not in (RewardStatus.REDEEMED.value, RewardStatus.SETTLED.value)
            ),
            ZERO,
        )

    def expired_face_value(self, now: Optional[datetime] = None) -> Decimal:
        return sum((u.amount for u in self.rewards if u.effective_status(now) == RewardStatus.EXPIRED.value), ZERO)

    @property
    def rewards_received(self) -> int:
        return sum(1 for u in self.rewards if u.canonical_status in CREDITED_STATUSES)

    @property
    def rewards_redeemed(self) -> int:
        return sum(1 for u in self.rewards if u.canonical_status in (RewardStatus.REDEEMED.value, RewardStatus.SETTLED.value))

    @property
    def rewards_settled(self) -> int:
        return sum(1 for u in self.rewards if u.canonical_status == RewardStatus.SETTLED.value)

    def to_dict(self) -> dict:
        return {
            "reward_type": self.reward_type,
            "total_reward_amount": str(self.total_face_value),
            "reward_count": self.count,
            "reward_unit_amount": None if self.unit_amount is None else str(self.unit_amount),
            "is_equal_split": self.is_equal_split,
            "reward_expiry_hours": self.expiry_hours,
            "reward_issue_after_settlement": self.issue_after_settlement,
            "restrictions": list(self.restrictions),
            "rewards": [unit.to_dict() for unit in self.rewards],
        }


def _decode_denominations(promotion) -> Tuple[Decimal, ...]:
    raw = getattr(promotion, "reward_denominations", ()) or ()
    denominations = []
    for value in raw:
        if value is None:
            continue
        denominations.append(value if isinstance(value, Decimal) else Decimal(str(value)))
    return tuple(denominations)


def collection_from_promotion(
    promotion, now: Optional[datetime] = None
) -> RewardCollection:
    """Build a reward collection from a promotion record.

    Equal-value rewards come from ``reward_unit_amount`` (or an even split of
    ``reward_amount`` over ``reward_count``). Unequal rewards come from an
    explicit ``reward_denominations`` list. Nothing is invented: an unknown
    split yields an empty collection.
    """
    reward_type = getattr(promotion, "reward_type", None) or "FREE_BET_SNR"
    count = max(getattr(promotion, "reward_count", None) or 1, 1)
    total = getattr(promotion, "reward_amount", None)
    expiry_hours = getattr(promotion, "reward_expiry_hours", None)
    issue_after = getattr(promotion, "reward_issue_after_settlement", None)
    restrictions = tuple(getattr(promotion, "reward_restrictions", ()) or ())

    denominations = _decode_denominations(promotion)
    if denominations:
        amounts = denominations
    else:
        unit = getattr(promotion, "reward_unit_amount", None)
        if unit is None and total is not None:
            unit = Decimal(str(total)) / Decimal(count)
        if unit is None:
            return RewardCollection(
                reward_type=reward_type, rewards=(), expiry_hours=expiry_hours,
                issue_after_settlement=issue_after, restrictions=restrictions,
            )
        unit = unit if isinstance(unit, Decimal) else Decimal(str(unit))
        amounts = tuple(unit for _ in range(count))

    expiry = None
    if expiry_hours is not None:
        expiry = timedelta(hours=int(expiry_hours))
    units = []
    for index, amount in enumerate(amounts):
        units.append(
            RewardUnit(
                reward_id=f"reward_{index + 1}",
                amount=amount if isinstance(amount, Decimal) else Decimal(str(amount)),
                expires_at=None,  # set when credited
            )
        )
    return RewardCollection(
        reward_type=reward_type,
        rewards=tuple(units),
        expiry_hours=expiry_hours,
        issue_after_settlement=issue_after,
        restrictions=restrictions,
    )


def apply_credit(
    collection: RewardCollection,
    now: datetime,
    credited_ids: Optional[Sequence[str]] = None,
) -> RewardCollection:
    """Return a copy with rewards marked CREDITED and expiry stamped."""
    credited_ids = set(credited_ids) if credited_ids is not None else None
    expiry_delta = (
        timedelta(hours=int(collection.expiry_hours))
        if collection.expiry_hours is not None
        else None
    )
    units = []
    for unit in collection.rewards:
        if credited_ids is not None and unit.reward_id not in credited_ids:
            units.append(unit)
            continue
        units.append(
            RewardUnit(
                reward_id=unit.reward_id, amount=unit.amount,
                status=RewardStatus.CREDITED.value,
                received_at=now,
                expires_at=(now + expiry_delta) if expiry_delta is not None else None,
                reserved_at=unit.reserved_at,
                linked_conversion_bet_id=unit.linked_conversion_bet_id,
                realized_pnl=unit.realized_pnl, note=unit.note,
            )
        )
    return RewardCollection(
        reward_type=collection.reward_type, rewards=tuple(units),
        expiry_hours=collection.expiry_hours,
        issue_after_settlement=collection.issue_after_settlement,
        restrictions=collection.restrictions, label=collection.label,
    )
