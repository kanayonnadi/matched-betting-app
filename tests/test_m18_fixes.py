from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from liquidity import (
    DepthCompleteness,
    LiquidityGrade,
    build_order_book,
    build_order_book_from_ws,
    score_liquidity,
    simulate_execution,
)
from models import Provenance
from opportunities import BetKind, discover_mock_opportunities
from promotions import (
    CandidateStatus,
    EligibilityStatus,
    Promotion,
    PromotionStatus,
    evaluate_promotion,
)
from promotions.optimizer import value_promotion

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
BIDS = [
    {"price": "0.6200", "quantity": "461.00"},
    {"price": "0.6100", "quantity": "399.00"},
    {"price": "0.5900", "quantity": "399.00"},
]


def ready_promotion(**overrides):
    base = dict(
        id=1, sportsbook="Book A", name="$10 -> 2x$25", offer_type="BET_GET",
        qualifying_stake=Decimal("10"), qualifying_min_odds=Decimal("1.4"),
        reward_amount=Decimal("50"), reward_type="FREE_BET_SNR", reward_count=2,
        status=PromotionStatus.VERIFIED.value, eligibility=EligibilityStatus.ELIGIBLE.value,
        confidence=1.0,
    )
    base.update(overrides)
    return Promotion(**base)


def make_live(pool, book_ts=NOW, exchange_ts=NOW, retrieved_at=NOW):
    provenance = Provenance(
        source_type="LIVE", sportsbook_provider="theoddsapi:betano_ca_on", exchange_provider="stx",
        event_id="e1", market_id="moneyline", selection_id="x",
        sportsbook_quote_timestamp=book_ts, exchange_book_timestamp=exchange_ts,
        retrieved_at=retrieved_at, event_match_confidence=1.0, market_match_confidence=1.0,
        liquidity_snapshot_id="snap", is_live=True,
    )
    return [
        replace(o, source_type="LIVE", is_live=True, fully_hedged=True,
                provenance=provenance, timestamp=book_ts)
        for o in pool
    ]


# --- 1. Quote freshness -------------------------------------------------------

def test_unknown_source_freshness_is_not_fresh():
    pool = make_live(discover_mock_opportunities(10), book_ts=None)
    evaluation = evaluate_promotion(ready_promotion(), pool, free_capital=Decimal("500"), now=NOW)
    assert evaluation.status == CandidateStatus.STALE.value
    assert any("freshness unknown" in w for w in evaluation.warnings)


def test_stale_sportsbook_source_timestamp():
    pool = make_live(discover_mock_opportunities(10), book_ts=NOW - timedelta(seconds=400))
    evaluation = evaluate_promotion(
        ready_promotion(), pool, free_capital=Decimal("500"), now=NOW, book_max_age_seconds=300
    )
    assert evaluation.status == CandidateStatus.STALE.value


def test_stale_exchange_timestamp():
    pool = make_live(discover_mock_opportunities(10), exchange_ts=NOW - timedelta(seconds=90))
    evaluation = evaluate_promotion(
        ready_promotion(), pool, free_capital=Decimal("500"), now=NOW, exchange_max_age_seconds=30
    )
    assert evaluation.status == CandidateStatus.STALE.value


def test_fresh_both_timestamps_is_candidate():
    pool = make_live(discover_mock_opportunities(10))
    evaluation = evaluate_promotion(ready_promotion(), pool, free_capital=Decimal("500"), now=NOW)
    assert evaluation.status == CandidateStatus.LIVE_CANDIDATE.value


def test_last_update_preserved_separately_from_retrieval():
    pool = make_live(discover_mock_opportunities(10), book_ts=NOW - timedelta(seconds=120), retrieved_at=NOW)
    provenance = pool[0].provenance
    assert provenance.sportsbook_quote_timestamp != provenance.retrieved_at
    assert (provenance.retrieved_at - provenance.sportsbook_quote_timestamp).total_seconds() == 120


# --- 2. Depth completeness ----------------------------------------------------

def test_rest_book_is_unknown_ws_is_complete():
    rest = build_order_book(BIDS, "1", NOW)
    assert rest.completeness == DepthCompleteness.UNKNOWN.value
    ws = build_order_book_from_ws({"b": [{"p": 0.62, "q": 461}]}, "1", NOW)
    assert ws.completeness == DepthCompleteness.COMPLETE.value


def test_execution_carries_completeness():
    execution = simulate_execution(build_order_book(BIDS, "1", NOW), 100, Decimal("0.10"), now=NOW)
    assert execution.completeness == DepthCompleteness.UNKNOWN.value


def test_scoring_caps_high_when_depth_incomplete():
    unknown = score_liquidity(simulate_execution(build_order_book(BIDS, "1", NOW), 100, Decimal("0.10"), now=NOW))
    assert unknown.grade is LiquidityGrade.MEDIUM
    complete = score_liquidity(
        simulate_execution(build_order_book(BIDS, "1", NOW, completeness="COMPLETE"), 100, Decimal("0.10"), now=NOW)
    )
    assert complete.grade is LiquidityGrade.HIGH


def test_insufficient_still_insufficient_regardless_of_completeness():
    execution = simulate_execution(
        build_order_book(BIDS, "1", NOW, completeness="COMPLETE"), 5000, Decimal("0.10"), now=NOW
    )
    assert score_liquidity(execution).grade is LiquidityGrade.INSUFFICIENT


# --- 3. Capital requirements --------------------------------------------------

def test_lifecycle_capital_is_not_understated():
    qualifying = make_live(discover_mock_opportunities(10))
    conversion = make_live(discover_mock_opportunities(25, kind=BetKind.FREE_BET_SNR))
    evaluation = evaluate_promotion(
        ready_promotion(), qualifying, conversion, free_capital=Decimal("500"), now=NOW
    )
    assert evaluation.qualifying_capital > 0
    assert evaluation.conversion_capital > 0
    assert evaluation.peak_capital == evaluation.qualifying_capital + evaluation.conversion_capital
    assert evaluation.peak_capital >= evaluation.qualifying_capital
    assert evaluation.peak_capital >= evaluation.conversion_capital


def test_value_promotion_peak_includes_conversion():
    promotion = ready_promotion()
    qualifying = discover_mock_opportunities(10)
    conversion = discover_mock_opportunities(25, kind=BetKind.FREE_BET_SNR)
    valuation = value_promotion(promotion, qualifying, conversion, free_capital=Decimal("500"))
    from promotions.optimizer import best_qualifying
    best = best_qualifying(promotion, qualifying, limit=1)[0]
    assert valuation.peak_capital > best.opportunity.required_capital
