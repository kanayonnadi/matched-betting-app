"""Qualifying-bet and reward-conversion optimizers, plus end-to-end valuation.

Operates on already-discovered, depth-aware opportunities (see
``opportunities.discovery``): qualifying candidates are ranked by the lowest
*executable* qualifying loss, conversion candidates by the highest guaranteed
conversion value. Nothing here depends on headline STX odds.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from opportunities import BetKind

from .models import EligibilityStatus, Promotion, PromotionStatus, Valuation

ZERO = Decimal("0")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class Candidate:
    opportunity: object
    score: Decimal
    liquidity_grade: Optional[str]
    fully_hedged: bool


def _fully_hedged(opportunity) -> bool:
    if getattr(opportunity, "fully_hedged", None) is not None:
        return bool(opportunity.fully_hedged)
    return bool(opportunity.liquidity_sufficient)


def best_qualifying(promotion: Promotion, opportunities, limit: int = 5):
    min_odds = promotion.qualifying_min_odds
    max_odds = promotion.qualifying_max_odds
    candidates = []
    for opportunity in opportunities:
        if opportunity.kind is not BetKind.QUALIFYING:
            continue
        if min_odds is not None and opportunity.back_odds < min_odds:
            continue
        if max_odds is not None and opportunity.back_odds > max_odds:
            continue
        if promotion.eligible_sports and opportunity.sport not in promotion.eligible_sports:
            continue
        candidates.append(
            Candidate(
                opportunity=opportunity,
                score=opportunity.qualifying_loss,
                liquidity_grade=getattr(opportunity, "liquidity_grade", None),
                fully_hedged=_fully_hedged(opportunity),
            )
        )
    candidates.sort(key=lambda candidate: candidate.score)
    return candidates[:limit]


def best_conversion(reward_amount, opportunities, limit: int = 5):
    candidates = []
    for opportunity in opportunities:
        if opportunity.kind is not BetKind.FREE_BET_SNR:
            continue
        if opportunity.conversion_rate is None:
            continue
        candidates.append(
            Candidate(
                opportunity=opportunity,
                score=opportunity.expected_profit,
                liquidity_grade=getattr(opportunity, "liquidity_grade", None),
                fully_hedged=_fully_hedged(opportunity),
            )
        )
    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    return candidates[:limit]


def value_promotion(
    promotion: Promotion,
    qualifying_opportunities,
    conversion_opportunities=(),
    free_capital=None,
) -> Valuation:
    q_candidates = best_qualifying(promotion, qualifying_opportunities, limit=1)
    best_q = q_candidates[0] if q_candidates else None
    expected_ql = best_q.opportunity.qualifying_loss if best_q else ZERO

    token_amount = promotion.reward_token_amount
    conv_candidates = (
        best_conversion(token_amount, conversion_opportunities, limit=1)
        if conversion_opportunities
        else []
    )
    best_c = conv_candidates[0] if conv_candidates else None
    token_value = best_c.opportunity.expected_profit if best_c else ZERO
    count = max(promotion.reward_count or 1, 1)
    expected_reward = token_value * Decimal(count)
    expected_net = expected_reward - expected_ql

    qualifying_capital = best_q.opportunity.required_capital if best_q is not None else ZERO
    conversion_capital = best_c.opportunity.required_capital if best_c is not None else ZERO
    # Conservative lifecycle peak: assume qualification and conversion capital
    # may overlap rather than understate the requirement.
    peak_capital = qualifying_capital + conversion_capital

    roi = expected_net / peak_capital * HUNDRED if peak_capital > ZERO else ZERO

    warnings = []
    from .restrictions import evaluate_restrictions

    restrictions = evaluate_restrictions(promotion)
    if restrictions.restricted:
        warnings.append(restrictions.note)
    verified = promotion.status in (PromotionStatus.VERIFIED.value, PromotionStatus.ACTIVE.value)
    if not verified:
        warnings.append(f"promotion status {promotion.status}")
    eligible = promotion.eligibility == EligibilityStatus.ELIGIBLE.value
    if not eligible:
        warnings.append(f"eligibility {promotion.eligibility}")
    if best_q is None:
        warnings.append("no qualifying bet found")
    elif not best_q.fully_hedged:
        warnings.append("qualifying hedge not fully executable")
    if conversion_opportunities:
        if best_c is None:
            warnings.append("no conversion candidate")
        elif not best_c.fully_hedged:
            warnings.append("conversion hedge not fully executable")
    if free_capital is not None and peak_capital > free_capital:
        warnings.append("insufficient free capital")

    ready = (
        verified
        and eligible
        and not restrictions.restricted
        and best_q is not None
        and best_q.fully_hedged
        and (not conversion_opportunities or (best_c is not None and best_c.fully_hedged))
        and (free_capital is None or peak_capital <= free_capital)
    )

    return Valuation(
        promotion_id=promotion.id,
        sportsbook=promotion.sportsbook,
        name=promotion.name,
        status=promotion.status,
        ready=ready,
        expected_qualifying_loss=expected_ql,
        reward_token_value=token_value,
        expected_reward_value=expected_reward,
        expected_net_profit=expected_net,
        peak_capital=peak_capital,
        roi_on_capital=roi,
        confidence=promotion.confidence,
        qualifying=best_q,
        conversions=tuple(conv_candidates),
        warnings=tuple(warnings),
    )
