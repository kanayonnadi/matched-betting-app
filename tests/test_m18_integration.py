from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from models import DataSource, Provenance
from opportunities import BetKind, discover_mock_opportunities
from promotions import (
    CandidateStatus,
    EligibilityStatus,
    Promotion,
    PromotionStatus,
    evaluate_promotion,
)
from providers import (
    CachedProvider,
    MockExchangeProvider,
    MockSportsbookProvider,
    MultiSportsbookProvider,
    StxProvider,
    TheOddsApiProvider,
)
from providers.quota import get_usage_tracker, reset_usage_tracker

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def mock_book():
    return MockSportsbookProvider()


def live_book(name="draftkings"):
    return TheOddsApiProvider("key", name, region="ca")


def test_provider_source_types_are_explicit():
    assert MockSportsbookProvider.source_type == "MOCK"
    assert MockExchangeProvider.source_type == "MOCK"
    assert TheOddsApiProvider.source_type == "LIVE"
    assert StxProvider.source_type == "LIVE"


def test_multi_and_cached_delegate_source_type():
    assert MultiSportsbookProvider([MockSportsbookProvider(), MockExchangeProvider()]).source_type == "MOCK"
    assert MultiSportsbookProvider([live_book("a"), live_book("b")]).source_type == "LIVE"
    assert CachedProvider(MockSportsbookProvider()).source_type == "MOCK"
    assert CachedProvider(live_book()).source_type == "LIVE"


def test_mock_opportunities_are_provenanced_and_never_live():
    opportunities = discover_mock_opportunities(100)
    assert opportunities
    for opportunity in opportunities:
        assert opportunity.source_type == DataSource.MOCK.value
        assert opportunity.is_live is False
        assert opportunity.provenance is not None
        assert opportunity.provenance.source_type == "MOCK"
        assert opportunity.provenance.is_live is False


def ready_promotion(**overrides):
    base = dict(
        id=1,
        sportsbook="Book A",
        name="$10 -> 2x$25",
        offer_type="BET_GET",
        qualifying_stake=Decimal("10"),
        qualifying_min_odds=Decimal("1.4"),
        reward_amount=Decimal("50"),
        reward_type="FREE_BET_SNR",
        reward_count=2,
        status=PromotionStatus.VERIFIED.value,
        eligibility=EligibilityStatus.ELIGIBLE.value,
        confidence=1.0,
    )
    base.update(overrides)
    return Promotion(**base)


def make_live(pool, now=NOW):
    provenance = Provenance(
        source_type="LIVE",
        sportsbook_provider="theoddsapi:betano_ca_on",
        exchange_provider="stx",
        event_id="e1",
        market_id="moneyline",
        selection_id="x",
        sportsbook_quote_timestamp=now,
        exchange_book_timestamp=now,
        retrieved_at=now,
        event_match_confidence=1.0,
        market_match_confidence=1.0,
        liquidity_snapshot_id="snap",
        is_live=True,
    )
    return [
        replace(o, source_type="LIVE", is_live=True, fully_hedged=True,
                provenance=provenance, timestamp=now)
        for o in pool
    ]


def test_mock_pool_yields_demo_not_live_candidate():
    evaluation = evaluate_promotion(
        ready_promotion(),
        discover_mock_opportunities(10),
        free_capital=Decimal("500"),
        now=NOW,
    )
    assert evaluation.status == CandidateStatus.DEMO.value
    assert evaluation.status != CandidateStatus.LIVE_CANDIDATE.value
    assert evaluation.is_live is False


def test_live_pool_yields_live_candidate():
    evaluation = evaluate_promotion(
        ready_promotion(),
        make_live(discover_mock_opportunities(10)),
        free_capital=Decimal("500"),
        now=NOW,
    )
    assert evaluation.status == CandidateStatus.LIVE_CANDIDATE.value
    assert evaluation.is_live is True


def test_stale_live_pool_is_stale():
    pool = make_live(discover_mock_opportunities(10), now=NOW - timedelta(seconds=90))
    evaluation = evaluate_promotion(
        ready_promotion(), pool, free_capital=Decimal("500"), now=NOW,
        exchange_max_age_seconds=30,
    )
    assert evaluation.status == CandidateStatus.STALE.value


def test_not_eligible_is_review():
    evaluation = evaluate_promotion(
        ready_promotion(eligibility=EligibilityStatus.UNKNOWN.value),
        make_live(discover_mock_opportunities(10)),
        now=NOW,
    )
    assert evaluation.status == CandidateStatus.REVIEW.value


def test_expired_promotion_not_ready():
    evaluation = evaluate_promotion(
        ready_promotion(expires_at=NOW - timedelta(days=1)),
        make_live(discover_mock_opportunities(10)),
        now=NOW,
    )
    assert evaluation.status == CandidateStatus.NOT_READY.value


def test_insufficient_capital_not_ready():
    evaluation = evaluate_promotion(
        ready_promotion(),
        make_live(discover_mock_opportunities(10)),
        free_capital=Decimal("1"),
        now=NOW,
    )
    assert evaluation.status == CandidateStatus.NOT_READY.value


def test_min_odds_filters_candidates():
    evaluation = evaluate_promotion(
        ready_promotion(qualifying_min_odds=Decimal("99")),
        make_live(discover_mock_opportunities(10)),
        now=NOW,
    )
    assert evaluation.qualifying is None
    assert evaluation.status == CandidateStatus.NOT_READY.value


def test_excluded_market_filtered():
    evaluation = evaluate_promotion(
        ready_promotion(excluded_markets=("moneyline", "match_winner")),
        make_live(discover_mock_opportunities(10)),
        now=NOW,
    )
    assert evaluation.qualifying is None


def test_quota_tracker_records_and_flags_exhaustion():
    reset_usage_tracker()
    tracker = get_usage_tracker()
    tracker.record({"x-requests-used": "5", "x-requests-remaining": "495"}, 200)
    assert tracker.snapshot.requests_used == 5
    assert tracker.snapshot.requests_remaining == 495
    assert tracker.snapshot.exhausted is False
    tracker.record({}, 429)
    assert tracker.snapshot.exhausted is True
