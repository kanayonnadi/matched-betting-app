"""Individual reward-token tracking.

A qualifying bet settling does **not** imply a reward was issued. Tokens are
created only on explicit confirmation, and carry their own lifecycle so
projected value is never confused with realized profit.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, Tuple

ZERO = Decimal("0")


class RewardTokenStatus(str, Enum):
    PENDING = "PENDING"
    RECEIVED = "RECEIVED"
    USED = "USED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


ALL_TOKEN_STATUSES = tuple(status.value for status in RewardTokenStatus)


@dataclass(frozen=True)
class RewardToken:
    id: Optional[int] = None
    promotion_id: Optional[int] = None
    token_key: Optional[str] = None
    face_value: Decimal = ZERO
    reward_type: str = "FREE_BET_SNR"  # FREE_BET_SNR | FREE_BET_SR | CASH
    eligible_markets: Tuple[str, ...] = ()
    received_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    used_at: Optional[datetime] = None
    status: str = RewardTokenStatus.PENDING.value
    linked_qualifying_bet_id: Optional[int] = None
    linked_conversion_bet_id: Optional[int] = None
    realized_value: Optional[Decimal] = None
    note: Optional[str] = None


def is_expired(token: RewardToken, now: Optional[datetime] = None) -> bool:
    if token.expires_at is None:
        return False
    reference = now or datetime.now(token.expires_at.tzinfo)
    return token.expires_at < reference


def effective_status(token: RewardToken, now: Optional[datetime] = None) -> str:
    if token.status == RewardTokenStatus.RECEIVED.value and is_expired(token, now):
        return RewardTokenStatus.EXPIRED.value
    return token.status


def outstanding_face_value(tokens, now: Optional[datetime] = None) -> Decimal:
    total = ZERO
    for token in tokens:
        if effective_status(token, now) == RewardTokenStatus.RECEIVED.value:
            total += token.face_value
    return total


def _bet_expected_profit(row) -> Decimal:
    if row is None:
        return ZERO
    back = Decimal(str(row["profit_if_back"] or 0))
    lay = Decimal(str(row["profit_if_lay"] or 0))
    return (back + lay) / Decimal("2")


def is_settled(bet_row) -> bool:
    return bet_row is not None and (bet_row["status"] or "Open") != "Open"


def realized_value(tokens, bets_by_id=None) -> Decimal:
    """Realized conversion profit — only for tokens whose conversion bet SETTLED.

    A token marked USED means the conversion wager was placed, not that profit is
    realized. Manually-entered token values are never used.
    """
    if not bets_by_id:
        return ZERO
    total = ZERO
    for token in tokens:
        if token.status != RewardTokenStatus.USED.value:
            continue
        bet_id = token.linked_conversion_bet_id
        if bet_id is None:
            continue
        row = bets_by_id.get(int(bet_id))
        if is_settled(row):
            total += _bet_expected_profit(row)
    return total


def pending_conversion_value(tokens, bets_by_id=None) -> Decimal:
    """Guaranteed conversion value of placed-but-unsettled conversion bets."""
    if not bets_by_id:
        return ZERO
    total = ZERO
    for token in tokens:
        if token.status != RewardTokenStatus.USED.value:
            continue
        bet_id = token.linked_conversion_bet_id
        if bet_id is None:
            continue
        row = bets_by_id.get(int(bet_id))
        if row is not None and not is_settled(row):
            total += _bet_expected_profit(row)
    return total


def projected_value(face_value: Decimal, conversion_rate: Decimal) -> Decimal:
    """Projected (not realized) value of a token at a conversion rate (%)."""
    return face_value * conversion_rate / Decimal("100")
