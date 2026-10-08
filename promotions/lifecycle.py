"""Promotion lifecycle.

Forward-only chain with explicit exception states and validated transitions. The
legacy ``VERIFIED``/``ACTIVE`` states remain actionable for backwards
compatibility with earlier milestones.
"""

from .models import PromotionStatus

S = PromotionStatus

CHAIN = (
    S.DISCOVERED.value,
    S.TERMS_RETRIEVED.value,
    S.TERMS_VERIFIED.value,
    S.ELIGIBILITY_CONFIRMED.value,
    S.QUALIFICATION_PENDING.value,
    S.QUALIFICATION_SETTLED.value,
    S.REWARD_PENDING.value,
    S.REWARD_RECEIVED.value,
    S.REWARD_USED.value,
    S.COMPLETED.value,
)

EXCEPTION_STATES = (
    S.EXPIRED.value,
    S.REJECTED.value,
    S.INELIGIBLE.value,
    S.NEEDS_REVIEW.value,
)

TERMINAL_STATES = (
    S.COMPLETED.value,
    S.EXPIRED.value,
    S.REJECTED.value,
    S.INELIGIBLE.value,
)

_ACTIONABLE = {
    S.ELIGIBILITY_CONFIRMED.value,
    S.QUALIFICATION_PENDING.value,
    S.VERIFIED.value,  # legacy
    S.ACTIVE.value,  # legacy
}

_TERMS_VERIFIED = {
    S.TERMS_VERIFIED.value,
    S.ELIGIBILITY_CONFIRMED.value,
    S.QUALIFICATION_PENDING.value,
    S.QUALIFICATION_SETTLED.value,
    S.REWARD_PENDING.value,
    S.REWARD_RECEIVED.value,
    S.REWARD_USED.value,
    S.COMPLETED.value,
    S.VERIFIED.value,  # legacy
    S.ACTIVE.value,  # legacy
}

TO_REVIEW = {S.NEEDS_REVIEW.value, S.REJECTED.value, S.EXPIRED.value, S.INELIGIBLE.value}

ALLOWED_TRANSITIONS = {
    S.DISCOVERED.value: {S.PARSED.value, S.TERMS_RETRIEVED.value, *TO_REVIEW},
    S.PARSED.value: {S.TERMS_RETRIEVED.value, *TO_REVIEW},
    S.TERMS_RETRIEVED.value: {S.TERMS_VERIFIED.value, *TO_REVIEW},
    S.TERMS_VERIFIED.value: {S.ELIGIBILITY_CONFIRMED.value, S.VERIFIED.value, *TO_REVIEW},
    S.ELIGIBILITY_CONFIRMED.value: {S.QUALIFICATION_PENDING.value, *TO_REVIEW},
    S.QUALIFICATION_PENDING.value: {S.QUALIFICATION_SETTLED.value, S.EXPIRED.value},
    S.QUALIFICATION_SETTLED.value: {S.REWARD_PENDING.value},
    S.REWARD_PENDING.value: {S.REWARD_RECEIVED.value, S.EXPIRED.value},
    S.REWARD_RECEIVED.value: {S.REWARD_USED.value, S.COMPLETED.value, S.EXPIRED.value},
    S.REWARD_USED.value: {S.COMPLETED.value},
    S.VERIFIED.value: {S.ELIGIBILITY_CONFIRMED.value, S.QUALIFICATION_PENDING.value, *TO_REVIEW},
    S.ACTIVE.value: {S.QUALIFICATION_PENDING.value, S.EXPIRED.value, S.REJECTED.value},
    S.NEEDS_REVIEW.value: {
        S.PARSED.value, S.TERMS_RETRIEVED.value, S.REJECTED.value, S.EXPIRED.value
    },
    S.COMPLETED.value: set(),
    S.EXPIRED.value: set(),
    S.REJECTED.value: set(),
    S.INELIGIBLE.value: set(),
}


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())


def advance_lifecycle(current: str, target: str) -> str:
    if target not in ALLOWED_TRANSITIONS:
        raise ValueError(f"unknown promotion lifecycle state {target!r}")
    if not can_transition(current, target):
        raise ValueError(f"invalid lifecycle transition {current!r} -> {target!r}")
    return target


def is_terms_verified(status: str) -> bool:
    return status in _TERMS_VERIFIED


def is_actionable(status: str) -> bool:
    return status in _ACTIONABLE


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATES
