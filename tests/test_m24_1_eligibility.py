from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from database import init_db
from opportunities import discover_mock_opportunities
from promotions import (
    EligibilityState,
    Promotion,
    evaluate_eligibility,
)
from providers import MockExchangeProvider, MockSportsbookProvider, SportRoute
from services import projected_profit, locked_in_range, realized_profit
from services.benchmark import benchmark, run_promotion_benchmark
from services.promotion_workflow import _recommendation

NOW = datetime.now(timezone.utc)
MOCK_ROUTE = SportRoute("basketball", "Basketball", ("basketball",), "basketball")


def promotion(**overrides):
    base = dict(
        id=1, sportsbook="BetMGM Ontario", name="offer", jurisdiction="Ontario",
        offer_type="BET_GET", new_customer_only=True,
        qualifying_stake=Decimal("10"), qualifying_min_odds=Decimal("1.2"),
        reward_amount=Decimal("20"), reward_type="FREE_BET_SNR", reward_count=1,
        stake_returned=False, status="TERMS_VERIFIED",
        lifecycle_status="TERMS_VERIFIED", eligibility="Eligible",
    )
    base.update(overrides)
    return Promotion(**base)


def live_opportunity(**overrides):
    base = discover_mock_opportunities(10)[0]
    base = replace(
        base, source_type="LIVE", is_live=True,
        book_provider="theoddsapi:betmgm_ca_on",
    )
    return replace(base, **overrides) if overrides else base


# --- eligibility states ------------------------------------------------------

def test_verified_eligible():
    result = evaluate_eligibility(promotion(), live_opportunity(), now=NOW)
    assert result.state == EligibilityState.VERIFIED_ELIGIBLE.value
    assert result.eligible


def test_user_confirmed_when_terms_incomplete():
    result = evaluate_eligibility(promotion(qualifying_min_odds=None), live_opportunity(), now=NOW)
    assert result.state == EligibilityState.USER_CONFIRMED_ELIGIBLE.value


def test_eligibility_unknown():
    result = evaluate_eligibility(promotion(eligibility="Unknown"), live_opportunity(), now=NOW)
    assert result.state == EligibilityState.ELIGIBILITY_UNKNOWN.value
    assert not result.eligible


# --- eligibility rules -------------------------------------------------------

def test_sportsbook_mismatch():
    opp = replace(live_opportunity(), book_provider="theoddsapi:playnow_ca")
    result = evaluate_eligibility(promotion(), opp, now=NOW)
    assert result.state == EligibilityState.INELIGIBLE.value
    assert "sportsbook mismatch" in result.reasons


def test_min_stake_and_odds():
    assert not evaluate_eligibility(promotion(qualifying_stake=Decimal("50")), live_opportunity(), now=NOW).eligible
    assert not evaluate_eligibility(promotion(qualifying_min_odds=Decimal("9.0")), live_opportunity(), now=NOW).eligible
    assert not evaluate_eligibility(promotion(qualifying_max_odds=Decimal("1.01")), live_opportunity(), now=NOW).eligible


def test_sport_and_market_rules():
    assert not evaluate_eligibility(promotion(eligible_sports=("soccer",)), live_opportunity(), now=NOW).eligible
    assert not evaluate_eligibility(promotion(eligible_markets=("match_winner",)), live_opportunity(), now=NOW).eligible
    opp = live_opportunity()
    assert not evaluate_eligibility(promotion(excluded_markets=(opp.market,)), opp, now=NOW).eligible


def test_expiry_and_prematch():
    assert not evaluate_eligibility(
        promotion(expires_at=NOW - timedelta(days=1)), live_opportunity(), now=NOW
    ).eligible
    past = replace(live_opportunity(), start_time=NOW - timedelta(hours=1))
    assert not evaluate_eligibility(promotion(pre_match_only=True), past, now=NOW).eligible


def test_unsupported_structures():
    assert not evaluate_eligibility(promotion(offer_type="PARLAY"), live_opportunity(), now=NOW).eligible
    assert not evaluate_eligibility(promotion(reward_type="CASH"), live_opportunity(), now=NOW).eligible


# --- provenance --------------------------------------------------------------

def test_recommendation_provenance():
    rec = _recommendation(live_opportunity(), promotion())
    assert rec.eligibility in (
        EligibilityState.VERIFIED_ELIGIBLE.value,
        EligibilityState.USER_CONFIRMED_ELIGIBLE.value,
    )
    assert "source_mode" in rec.provenance
    assert "quote_timestamp" in rec.provenance
    # Fee-model provenance is present when a depth execution exists.
    if "depth_completeness" in rec.provenance:
        assert rec.provenance["fee_model_status"] == "EXTERNALLY_UNVERIFIED"


# --- profit separation -------------------------------------------------------

def test_profit_separation(tmp_path):
    db = tmp_path / "p.db"
    init_db(db)
    opp = live_opportunity()
    assert projected_profit(opp) == min(opp.profit_if_back, opp.profit_if_lay)
    back_win, lay_win = locked_in_range(opp)
    assert back_win == opp.profit_if_back and lay_win == opp.profit_if_lay
    net = realized_profit(99, db_path=db)  # no bets
    assert net["realized"] == Decimal("0")


# --- benchmark provenance ----------------------------------------------------

def test_benchmark_reports_provenance():
    result = benchmark((10,), MockSportsbookProvider(), MockExchangeProvider(), now=NOW)
    assert result["provider_mode"] == "DEMO"
    row = result["stakes"][0]
    assert "insufficient_depth" in row
    assert "verified_depth" in row
    assert "max_slippage_pct" in row


def test_promotion_benchmark_uses_pipeline(monkeypatch):
    import services.discovery as discovery

    monkeypatch.setattr(discovery, "build_bookmaker_provider", lambda *a, **k: MockSportsbookProvider())
    result = run_promotion_benchmark(
        promotion(), "DEMO", book=MockSportsbookProvider(), exchange=MockExchangeProvider(),
        routes=[MOCK_ROUTE], save=False,
    )
    assert result["sportsbook"] == "BetMGM Ontario"
    assert result["recommendations"]
    assert all("eligibility" in r for r in result["recommendations"])
