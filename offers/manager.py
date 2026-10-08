"""Offer lifecycle management.

Expands the offer planner into a workflow with an explicit status lifecycle and
links offers to bet-log entries so their qualifying losses and rewards roll into
the dashboard.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

HUNDRED = Decimal("100")
ZERO = Decimal("0")
DEFAULT_REWARD_CONVERSION = Decimal("75.0")  # percent


class OfferStatus(str, Enum):
    AVAILABLE = "Available"
    QUALIFYING_PLACED = "Qualifying bet placed"
    QUALIFIED = "Qualified"
    REWARD_RECEIVED = "Reward received"
    REWARD_USED = "Reward used"
    COMPLETED = "Completed"
    EXPIRED = "Expired"


ALL_STATUSES = tuple(status.value for status in OfferStatus)

ACTIVE_STATUSES = (
    OfferStatus.AVAILABLE.value,
    OfferStatus.QUALIFYING_PLACED.value,
    OfferStatus.QUALIFIED.value,
    OfferStatus.REWARD_RECEIVED.value,
    OfferStatus.REWARD_USED.value,
)
TERMINAL_STATUSES = (OfferStatus.COMPLETED.value, OfferStatus.EXPIRED.value)
REWARD_RECEIVED_STATUSES = (
    OfferStatus.REWARD_RECEIVED.value,
    OfferStatus.REWARD_USED.value,
    OfferStatus.COMPLETED.value,
)
QUALIFYING_DONE_STATUSES = (
    OfferStatus.QUALIFYING_PLACED.value,
    OfferStatus.QUALIFIED.value,
    OfferStatus.REWARD_RECEIVED.value,
    OfferStatus.REWARD_USED.value,
    OfferStatus.COMPLETED.value,
)

ALLOWED_TRANSITIONS = {
    OfferStatus.AVAILABLE.value: {OfferStatus.QUALIFYING_PLACED.value, OfferStatus.EXPIRED.value},
    OfferStatus.QUALIFYING_PLACED.value: {
        OfferStatus.QUALIFIED.value,
        OfferStatus.AVAILABLE.value,
        OfferStatus.EXPIRED.value,
    },
    OfferStatus.QUALIFIED.value: {OfferStatus.REWARD_RECEIVED.value, OfferStatus.EXPIRED.value},
    OfferStatus.REWARD_RECEIVED.value: {
        OfferStatus.REWARD_USED.value,
        OfferStatus.COMPLETED.value,
        OfferStatus.EXPIRED.value,
    },
    OfferStatus.REWARD_USED.value: {OfferStatus.COMPLETED.value, OfferStatus.EXPIRED.value},
    OfferStatus.COMPLETED.value: set(),
    OfferStatus.EXPIRED.value: {OfferStatus.AVAILABLE.value},
}


def is_active(status) -> bool:
    return status in ACTIVE_STATUSES


def can_transition(current, target) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())


def advance(current, target) -> str:
    """Validate a lifecycle transition, returning the new status."""
    if target not in ALL_STATUSES:
        raise ValueError(f"unknown offer status {target!r}")
    if not can_transition(current, target):
        raise ValueError(f"cannot move offer from {current!r} to {target!r}")
    return target


def _to_decimal(value, default=ZERO) -> Decimal:
    if value is None or value == "":
        return default
    return Decimal(str(value))


def _value(row, key, default=None):
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def expected_value(reward, reward_conversion=None, max_qualifying_loss=0) -> Decimal:
    conversion = (
        DEFAULT_REWARD_CONVERSION if reward_conversion is None else _to_decimal(reward_conversion)
    )
    return _to_decimal(reward) * conversion / HUNDRED - _to_decimal(max_qualifying_loss)


def _bet_qualifying_loss(bet_row) -> Decimal:
    profit_if_back = _to_decimal(_value(bet_row, "profit_if_back"))
    profit_if_lay = _to_decimal(_value(bet_row, "profit_if_lay"))
    return -((profit_if_back + profit_if_lay) / Decimal("2"))


@dataclass(frozen=True)
class OfferSummary:
    active_offers: int
    expected_value: Decimal
    qualifying_losses_incurred: Decimal
    rewards_received: Decimal
    converted_profit: Decimal
    net_profit: Decimal


def summarize(offer_rows, bets_by_id=None) -> OfferSummary:
    bets_by_id = bets_by_id or {}
    active = 0
    expected = ZERO
    qualifying_losses = ZERO
    rewards = ZERO
    converted = ZERO

    for row in offer_rows:
        status = _value(row, "status")
        reward = _to_decimal(_value(row, "reward"))
        raw_conversion = _value(row, "reward_conversion")
        conversion = DEFAULT_REWARD_CONVERSION if raw_conversion is None else _to_decimal(raw_conversion)
        max_ql = _to_decimal(_value(row, "max_qualifying_loss"))

        if status in ACTIVE_STATUSES:
            active += 1
            expected += reward * conversion / HUNDRED - max_ql

        bet_id = _value(row, "bet_id")
        if bet_id is not None and bet_id in bets_by_id:
            qualifying_losses += _bet_qualifying_loss(bets_by_id[bet_id])
        elif status in QUALIFYING_DONE_STATUSES:
            qualifying_losses += max_ql

        if status in REWARD_RECEIVED_STATUSES:
            rewards += reward
            converted += reward * conversion / HUNDRED

    return OfferSummary(
        active_offers=active,
        expected_value=expected,
        qualifying_losses_incurred=qualifying_losses,
        rewards_received=rewards,
        converted_profit=converted,
        net_profit=converted - qualifying_losses,
    )
