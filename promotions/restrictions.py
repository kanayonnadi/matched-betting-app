"""Operator promotion-strategy restrictions (M25.3, Phase 8).

Profitability must not rest on arithmetic alone. Operator general terms often
prohibit minimal-risk / matched / arbitrage betting. When those restrictions are
present, an offer must **not** be classified as a verified, terms-compliant
matched-betting opportunity — even if the maths is positive.

This distinguishes *personal account eligibility* (can this account take the
offer?) from *promotion-strategy compliance* (is this betting pattern allowed?).
The absence of an explicit restriction in promotion-specific terms is not
permission when incorporated general terms impose one.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class StrategyCompliance(str, Enum):
    COMPLIANT = "COMPLIANT"
    RESTRICTED = "RESTRICTED"
    UNKNOWN = "UNKNOWN"


# Phrases commonly found in general terms that prohibit matched/minimal-risk play.
MINIMAL_RISK_PHRASES = (
    "minimum risk",
    "minimal risk",
    "minimum-risk",
    "minimal-risk",
    "no risk",
    "risk free",
    "risk-free",
    "matched betting",
    "matched bet",
    "arbitrage",
    "arbitrage betting",
    "cover all outcomes",
    "lay bets",
    "lay betting",
    "betting exchange",
    "void bets",
    "voided bet",
    "unusual betting",
    "contrary to the spirit",
    "abnormal betting",
)

EVASION_PHRASES = (
    "multiple accounts",
    "duplicate account",
    "syndicate",
    "courtsiding",
)

STATUS_FLAG = "MATCHED_BETTING_RESTRICTED"


@dataclass(frozen=True)
class RestrictionCheck:
    compliance: str
    flags: Tuple[str, ...]
    matched_phrases: Tuple[str, ...]
    note: str

    @property
    def restricted(self) -> bool:
        return self.compliance == StrategyCompliance.RESTRICTED.value

    @property
    def compliant(self) -> bool:
        return self.compliance == StrategyCompliance.COMPLIANT.value


def _gather_text(promotion, extra_text: Optional[str]) -> str:
    parts = [
        getattr(promotion, "terms_text", None),
        getattr(promotion, "wagering_requirement_text", None),
        getattr(promotion, "withdrawal_restrictions", None),
        extra_text,
    ]
    return " ".join(str(part) for part in parts if part).lower()


def evaluate_restrictions(promotion, extra_text: Optional[str] = None) -> RestrictionCheck:
    """Flag general terms that prohibit minimal-risk / matched betting."""
    text = _gather_text(promotion, extra_text)
    if not text.strip():
        return RestrictionCheck(
            StrategyCompliance.UNKNOWN.value, (), (),
            "No terms text available; strategy compliance cannot be verified.",
        )
    matched = tuple(phrase for phrase in MINIMAL_RISK_PHRASES if phrase in text)
    evasion = tuple(phrase for phrase in EVASION_PHRASES if phrase in text)
    if matched:
        return RestrictionCheck(
            StrategyCompliance.RESTRICTED.value,
            (STATUS_FLAG,) + tuple("ACCOUNT_TERM:" + phrase for phrase in evasion),
            matched,
            "General terms restrict minimal-risk/matched betting; this offer is not "
            "a verified, terms-compliant matched-betting opportunity.",
        )
    if evasion:
        return RestrictionCheck(
            StrategyCompliance.RESTRICTED.value,
            tuple("ACCOUNT_TERM:" + phrase for phrase in evasion),
            evasion,
            "General terms restrict betting patterns associated with matched betting.",
        )
    return RestrictionCheck(
        StrategyCompliance.COMPLIANT.value, (), (),
        "No minimal-risk/matched-betting restriction found in the supplied terms.",
    )
