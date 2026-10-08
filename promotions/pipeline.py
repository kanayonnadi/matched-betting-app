"""Live promotion-to-opportunity pipeline (M18).

Connects a verified + eligible promotion to live sportsbook odds and executable
STX depth, and classifies the result with explicit statuses. Mock data can never
produce a ``LIVE_CANDIDATE``.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional, Sequence

from monitoring.freshness import age_seconds
from opportunities import BetKind, discover_mock_opportunities

from .lifecycle import is_terms_verified
from .models import EligibilityStatus, Promotion, PromotionStatus
from .optimizer import best_conversion, best_qualifying

ZERO = Decimal("0")
HUNDRED = Decimal("100")


class CandidateStatus(str, Enum):
    DEMO = "DEMO"
    REVIEW = "REVIEW"
    LIVE_CANDIDATE = "LIVE_CANDIDATE"
    NOT_READY = "NOT_READY"
    STALE = "STALE"


@dataclass(frozen=True)
class PromotionEvaluation:
    promotion_id: Optional[int]
    sportsbook: str
    name: str
    status: str
    source_type: str
    is_live: bool
    qualifying: Optional[object]
    projected_conversion: Optional[object]
    confirmed_conversion: Optional[object]
    expected_qualifying_loss: Decimal
    projected_reward_value: Decimal
    expected_net_profit: Decimal
    qualifying_capital: Decimal
    conversion_capital: Decimal
    peak_capital: Decimal
    warnings: tuple
    provenance: Optional[object]


def _fully_hedged(opportunity) -> bool:
    if getattr(opportunity, "fully_hedged", None) is not None:
        return bool(opportunity.fully_hedged)
    return bool(opportunity.liquidity_sufficient)


def filter_for_promotion(promotion: Promotion, opportunities) -> list:
    """Apply promotion rules that the generic optimizer does not: exclusions."""
    excluded = set(promotion.excluded_markets or ())
    results = []
    for opportunity in opportunities:
        if excluded and opportunity.market in excluded:
            continue
        results.append(opportunity)
    return results


def evaluate_promotion(
    promotion: Promotion,
    qualifying_pool,
    conversion_pool=(),
    free_capital=None,
    now: Optional[datetime] = None,
    book_max_age_seconds: float = 300,
    exchange_max_age_seconds: float = 30,
    reward_confirmed: bool = False,
) -> PromotionEvaluation:
    now = now or datetime.now(timezone.utc)
    warnings = []

    source_type = "MOCK"
    is_live = False
    qualifying = None
    expected_ql = ZERO
    qualifying_capital = ZERO
    conversion_capital = ZERO
    projected_conversion = None
    confirmed_conversion = None

    # Gate 1: terms must be verified (lifecycle or legacy actionable states).
    verified = is_terms_verified(promotion.status)
    if not verified:
        warnings.append(f"promotion status {promotion.status}")

    # Gate 2: eligibility must be confirmed.
    eligible = promotion.eligibility == EligibilityStatus.ELIGIBLE.value
    if not eligible:
        warnings.append(f"eligibility {promotion.eligibility}")

    # Gate 3: not expired.
    expired = promotion.expires_at is not None and promotion.expires_at < now
    if expired:
        warnings.append("promotion expired")

    pool = filter_for_promotion(promotion, qualifying_pool)
    qualifying_candidates = best_qualifying(promotion, pool, limit=1)
    best_q = qualifying_candidates[0] if qualifying_candidates else None

    if best_q is not None:
        opportunity = best_q.opportunity
        source_type = getattr(opportunity, "source_type", "MOCK")
        is_live = bool(getattr(opportunity, "is_live", False))
        qualifying = best_q
        expected_ql = opportunity.qualifying_loss
        qualifying_capital = opportunity.required_capital
        if not _fully_hedged(opportunity):
            warnings.append("qualifying hedge not fully executable")

    token_amount = promotion.reward_token_amount
    if conversion_pool:
        projected = best_conversion(token_amount, conversion_pool, limit=1)
        projected_conversion = projected[0] if projected else None
        if reward_confirmed:
            confirmed_conversion = projected_conversion

    token_value = (
        projected_conversion.opportunity.expected_profit if projected_conversion else ZERO
    )
    count = max(promotion.reward_count or 1, 1)
    projected_reward = token_value * Decimal(count)
    expected_net = projected_reward - expected_ql

    # Conversion capital is only counted once a reward actually exists
    # (confirmed); projected conversion capital is shown separately and is
    # included conservatively in the lifecycle peak.
    if projected_conversion is not None:
        conversion_capital = projected_conversion.opportunity.required_capital
    peak_capital = qualifying_capital + conversion_capital

    provenance = best_q.opportunity.provenance if best_q is not None else None

    # Freshness: use the SOURCE timestamps (sportsbook last_update / exchange
    # book time) where available. Unknown source freshness is never "fresh".
    unknown_freshness = False
    book_stale = exchange_stale = False
    if provenance is None:
        unknown_freshness = True
    else:
        book_ts = getattr(provenance, "sportsbook_quote_timestamp", None)
        exchange_ts = getattr(provenance, "exchange_book_timestamp", None)
        if book_ts is None or exchange_ts is None:
            unknown_freshness = True
        else:
            book_stale = age_seconds(book_ts, now=now) > book_max_age_seconds
            exchange_stale = age_seconds(exchange_ts, now=now) > exchange_max_age_seconds

    # Status determination (order matters).
    if expired:
        status = CandidateStatus.NOT_READY.value
    elif not verified or not eligible:
        status = CandidateStatus.REVIEW.value
    elif best_q is None:
        status = CandidateStatus.NOT_READY.value
    elif not _fully_hedged(best_q.opportunity):
        status = CandidateStatus.NOT_READY.value
    elif source_type != "LIVE":
        status = CandidateStatus.DEMO.value
    elif unknown_freshness:
        status = CandidateStatus.STALE.value
        warnings.append("source freshness unknown")
    elif book_stale or exchange_stale:
        status = CandidateStatus.STALE.value
        warnings.append("sportsbook or exchange quote stale")
    elif free_capital is not None and peak_capital > free_capital:
        status = CandidateStatus.NOT_READY.value
        warnings.append("insufficient free capital")
    else:
        status = CandidateStatus.LIVE_CANDIDATE.value

    return PromotionEvaluation(
        promotion_id=promotion.id,
        sportsbook=promotion.sportsbook,
        name=promotion.name,
        status=status,
        source_type=source_type,
        is_live=is_live,
        qualifying=qualifying,
        projected_conversion=projected_conversion,
        confirmed_conversion=confirmed_conversion,
        expected_qualifying_loss=expected_ql,
        projected_reward_value=projected_reward,
        expected_net_profit=expected_net,
        qualifying_capital=qualifying_capital,
        conversion_capital=conversion_capital,
        peak_capital=peak_capital,
        warnings=tuple(warnings),
        provenance=provenance,
    )


def build_demo_pool(stake, kind=BetKind.QUALIFYING, sports=None):
    return discover_mock_opportunities(stake, sports=sports, kind=kind)
