"""Terms validation and promotion construction.

Deterministic: promotion status is derived from which fields the parser could
resolve. Missing critical fields force ``NEEDS_REVIEW`` — a promotion is never
silently treated as verified.
"""

from typing import Tuple

from .models import (
    CRITICAL_FIELDS,
    Promotion,
    PromotionStatus,
    PromotionTerms,
)


def validate_terms(terms: PromotionTerms) -> Tuple[str, Tuple[str, ...]]:
    issues = []
    if terms.offer_type is None:
        issues.append("offer type unclear")
    if terms.qualifying_stake is None:
        issues.append("qualifying stake unclear")
    if terms.reward_amount is None:
        issues.append("reward amount unclear")
    if terms.reward_type is None:
        issues.append("reward type unclear")
    if terms.qualifying_min_odds is None:
        issues.append("minimum odds unknown")
    if terms.stake_returned is None:
        issues.append("stake returned unknown")
    if terms.reward_expiry_days is None:
        issues.append("reward expiry unknown")
    if terms.new_customer_only is None:
        issues.append("customer eligibility unknown")

    critical_missing = [name for name in CRITICAL_FIELDS if getattr(terms, name) is None]
    status = (
        PromotionStatus.NEEDS_REVIEW.value
        if critical_missing
        else PromotionStatus.VERIFIED.value
    )
    return status, tuple(issues)


def promotion_from_terms(
    terms: PromotionTerms,
    sportsbook: str,
    name: str,
    source_url=None,
    terms_text=None,
) -> Promotion:
    status, _issues = validate_terms(terms)
    return Promotion(
        sportsbook=sportsbook,
        name=name,
        jurisdiction=terms.jurisdiction,
        offer_type=terms.offer_type,
        new_customer_only=terms.new_customer_only,
        qualifying_stake=terms.qualifying_stake,
        qualifying_min_odds=terms.qualifying_min_odds,
        qualifying_max_odds=terms.qualifying_max_odds,
        reward_amount=terms.reward_amount,
        reward_type=terms.reward_type,
        reward_count=terms.reward_count or 1,
        stake_returned=terms.stake_returned,
        reward_expiry_days=terms.reward_expiry_days,
        source_url=source_url,
        terms_text=terms_text,
        status=status,
        confidence=terms.confidence,
    )
