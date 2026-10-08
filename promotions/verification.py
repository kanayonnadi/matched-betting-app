"""Deterministic Ontario promotion verification.

Publicly advertised terms are validated structurally; personal eligibility can
only be confirmed by the user. Unknown critical terms block actionable status.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Tuple

from .lifecycle import is_terms_verified
from .models import EligibilityStatus, Promotion

CRITICAL_TERMS = (
    "offer_type",
    "qualifying_stake",
    "reward_amount",
    "reward_type",
    "qualifying_min_odds",
)

ONE = Decimal("1")


def terms_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


_REVIEW_LABELS = {
    "offer_type": "offer type",
    "qualifying_stake": "qualifying stake",
    "reward_amount": "reward amount",
    "reward_type": "reward type",
    "qualifying_min_odds": "minimum odds",
}


def review_issues(promotion: Promotion) -> Tuple[str, ...]:
    """Human-readable list of exactly what must be fixed for actionable status."""
    issues = []
    for name in CRITICAL_TERMS:
        if getattr(promotion, name, None) is None:
            issues.append(f"missing {_REVIEW_LABELS.get(name, name)}")
    if (promotion.reward_count or 0) < 1:
        issues.append("reward token count must be at least 1")
    if promotion.reward_type in ("FREE_BET_SNR", "FREE_BET_SR") and promotion.stake_returned is None:
        issues.append("stake-return rule unknown (needed to classify the bonus)")
    if promotion.qualifying_min_odds is not None and promotion.qualifying_min_odds <= ONE:
        issues.append("minimum odds must be greater than 1.0")
    if promotion.jurisdiction is None or "ontario" not in (promotion.jurisdiction or "").lower():
        issues.append("jurisdiction not confirmed as Ontario")
    return tuple(issues)


@dataclass(frozen=True)
class VerificationCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class VerificationReport:
    ontario_ok: bool
    terms_verified: bool
    account_eligibility_required: bool
    account_eligible: Optional[bool]
    actionable: bool
    checks: Tuple[VerificationCheck, ...]
    missing_terms: Tuple[str, ...]
    warnings: Tuple[str, ...]


def validate_ontario(
    promotion: Promotion,
    now: Optional[datetime] = None,
    account_eligible: Optional[bool] = None,
) -> VerificationReport:
    now = now or datetime.now(timezone.utc)
    checks = []
    missing = []
    warnings = []

    jurisdiction = (promotion.jurisdiction or "").lower()
    ontario_ok = "ontario" in jurisdiction
    checks.append(
        VerificationCheck("ontario_availability", ontario_ok, promotion.jurisdiction or "unknown")
    )

    operator_ok = bool(promotion.sportsbook and promotion.sportsbook.strip())
    checks.append(VerificationCheck("operator", operator_ok, promotion.sportsbook or "unknown"))

    for name in CRITICAL_TERMS:
        value = getattr(promotion, name, None)
        ok = value is not None
        checks.append(VerificationCheck(f"term_{name}", ok, str(value) if ok else "UNKNOWN"))
        if not ok:
            missing.append(name)

    count_ok = (promotion.reward_count or 0) >= 1
    checks.append(VerificationCheck("reward_count", count_ok, str(promotion.reward_count)))
    if not count_ok:
        missing.append("reward_count")

    if promotion.reward_type in ("FREE_BET_SNR", "FREE_BET_SR") and promotion.stake_returned is None:
        checks.append(VerificationCheck("stake_return_rule", False, "UNKNOWN"))
        missing.append("stake_returned")
    else:
        checks.append(
            VerificationCheck("stake_return_rule", True, str(promotion.stake_returned))
        )

    if promotion.qualifying_min_odds is not None and promotion.qualifying_min_odds <= ONE:
        checks.append(
            VerificationCheck("min_odds_sane", False, str(promotion.qualifying_min_odds))
        )
        missing.append("qualifying_min_odds")

    expired = promotion.expires_at is not None and promotion.expires_at < now
    checks.append(VerificationCheck("not_expired", not expired, str(promotion.expires_at)))

    if promotion.new_customer_only is None:
        warnings.append("new vs existing customer eligibility unknown")
    if promotion.wagering_requirement_text:
        warnings.append(f"wagering requirement: {promotion.wagering_requirement_text}")
    if promotion.withdrawal_restrictions:
        warnings.append(f"withdrawal restrictions: {promotion.withdrawal_restrictions}")

    terms_verified = is_terms_verified(promotion.status) and not missing

    eligibility_known = promotion.eligibility in (
        EligibilityStatus.ELIGIBLE.value,
        EligibilityStatus.NOT_ELIGIBLE.value,
        EligibilityStatus.ALREADY_USED.value,
    )
    account_eligibility_required = not eligibility_known
    resolved_eligible = account_eligible
    if resolved_eligible is None and eligibility_known:
        resolved_eligible = promotion.eligibility == EligibilityStatus.ELIGIBLE.value

    actionable = (
        ontario_ok
        and operator_ok
        and terms_verified
        and not expired
        and not missing
        and resolved_eligible is True
    )

    return VerificationReport(
        ontario_ok=ontario_ok,
        terms_verified=terms_verified,
        account_eligibility_required=account_eligibility_required,
        account_eligible=resolved_eligible,
        actionable=actionable,
        checks=tuple(checks),
        missing_terms=tuple(sorted(set(missing))),
        warnings=tuple(warnings),
    )
