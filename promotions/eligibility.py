"""Promotion eligibility evaluation (M24.1).

Distinguishes VERIFIED ELIGIBLE / USER-CONFIRMED ELIGIBLE / ELIGIBILITY UNKNOWN /
INELIGIBLE and enforces every condition the parser captured. Unsupported
structures (parlays, deposit matches, non-SNR reward types) are surfaced as
INELIGIBLE rather than guessed.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Tuple

from providers.bookmakers import resolve_bookmaker

from .auto_lifecycle import terms_complete
from .models import EligibilityStatus, Promotion

ONE = 1


class EligibilityState(str, Enum):
    VERIFIED_ELIGIBLE = "VERIFIED ELIGIBLE"
    USER_CONFIRMED_ELIGIBLE = "USER-CONFIRMED ELIGIBLE"
    ELIGIBILITY_UNKNOWN = "ELIGIBILITY UNKNOWN"
    INELIGIBLE = "INELIGIBLE"


UNSUPPORTED_OFFER_TYPES = {"PARLAY", "DEPOSIT_MATCH"}
SUPPORTED_REWARD_TYPES = {"FREE_BET_SNR", "FREE_BET_SR"}


@dataclass(frozen=True)
class EligibilityResult:
    state: str
    reasons: Tuple[str, ...] = ()
    applied: Tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        return self.state in (
            EligibilityState.VERIFIED_ELIGIBLE.value,
            EligibilityState.USER_CONFIRMED_ELIGIBLE.value,
        )


def evaluate_eligibility(promotion: Promotion, opportunity=None, now=None) -> EligibilityResult:
    now = now or datetime.now(timezone.utc)
    reasons = []
    applied = []

    # Unsupported promotion structures must never be recommended.
    if promotion.offer_type in UNSUPPORTED_OFFER_TYPES:
        return EligibilityResult(
            EligibilityState.INELIGIBLE.value,
            (f"unsupported offer type '{promotion.offer_type}'",),
            ("offer_type",),
        )
    if promotion.reward_type is not None and promotion.reward_type not in SUPPORTED_REWARD_TYPES:
        return EligibilityResult(
            EligibilityState.INELIGIBLE.value,
            (f"reward type '{promotion.reward_type}' not supported by the engine",),
            ("reward_type",),
        )

    applied.append("expiry")
    if promotion.expires_at is not None and promotion.expires_at < now:
        reasons.append("promotion expired")

    applied.append("account_eligibility")
    if promotion.eligibility in (
        EligibilityStatus.NOT_ELIGIBLE.value,
        EligibilityStatus.ALREADY_USED.value,
    ):
        reasons.append(f"account eligibility {promotion.eligibility}")

    if opportunity is not None:
        # Only enforce sportsbook identity for live quotes; simulated data is not
        # attributable to a real sportsbook.
        if getattr(opportunity, "source_type", "MOCK") == "LIVE":
            applied.append("sportsbook")
            key = resolve_bookmaker(promotion.sportsbook).key
            if key and key not in (opportunity.book_provider or ""):
                reasons.append("sportsbook mismatch")

        if promotion.qualifying_stake is not None:
            applied.append("min_stake")
            if opportunity.stake < promotion.qualifying_stake:
                reasons.append("below minimum qualifying stake")

        if promotion.qualifying_min_odds is not None:
            applied.append("min_odds")
            if opportunity.back_odds < promotion.qualifying_min_odds:
                reasons.append("below minimum odds")
        if promotion.qualifying_max_odds is not None:
            applied.append("max_odds")
            if opportunity.back_odds > promotion.qualifying_max_odds:
                reasons.append("above maximum odds")

        if promotion.eligible_sports:
            applied.append("eligible_sports")
            if opportunity.sport not in promotion.eligible_sports:
                reasons.append("sport not eligible")

        if promotion.eligible_markets:
            applied.append("eligible_markets")
            if opportunity.market not in promotion.eligible_markets:
                reasons.append("market not eligible")

        if promotion.excluded_markets:
            applied.append("excluded_markets")
            if opportunity.market in promotion.excluded_markets:
                reasons.append("market excluded")

        if promotion.pre_match_only:
            applied.append("pre_match_only")
            if opportunity.start_time <= now:
                reasons.append("in-play event but promotion is pre-match only")

    if reasons:
        return EligibilityResult(EligibilityState.INELIGIBLE.value, tuple(reasons), tuple(applied))

    if promotion.eligibility == EligibilityStatus.ELIGIBLE.value:
        state = (
            EligibilityState.VERIFIED_ELIGIBLE.value
            if terms_complete(promotion)
            else EligibilityState.USER_CONFIRMED_ELIGIBLE.value
        )
    else:
        state = EligibilityState.ELIGIBILITY_UNKNOWN.value
    return EligibilityResult(state, (), tuple(applied))
