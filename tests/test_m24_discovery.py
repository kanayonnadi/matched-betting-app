from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from opportunities import discover_mock_opportunities
from promotions import EligibilityStatus, Promotion
from providers import MockExchangeProvider, MockSportsbookProvider, SportRoute, resolve_bookmaker

MOCK_ROUTE = SportRoute("basketball", "Basketball", ("basketball",), "basketball")
MOCK_ROUTES = [MOCK_ROUTE]
from services import (
    DiagnosticCode,
    OpportunityStatus,
    classify_recommendation,
    discover_conversion,
    discover_qualifying,
)
from services.promotion_workflow import _recommendation
import services.discovery as discovery

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def promotion(**overrides):
    base = dict(
        id=1, sportsbook="BetMGM Ontario", name="offer", jurisdiction="Ontario",
        offer_type="BET_GET", new_customer_only=True,
        qualifying_stake=Decimal("10"), qualifying_min_odds=Decimal("1.2"),
        reward_amount=Decimal("20"), reward_type="FREE_BET_SNR", reward_count=1,
        stake_returned=False, status="TERMS_VERIFIED",
        lifecycle_status="TERMS_VERIFIED", eligibility=EligibilityStatus.ELIGIBLE.value,
    )
    base.update(overrides)
    return Promotion(**base)


# --- bookmaker mapping -------------------------------------------------------

def test_resolve_bookmaker_supported():
    r = resolve_bookmaker("BetMGM Ontario")
    assert r.supported and r.key == "betmgm_ca_on"
    assert resolve_bookmaker("Sports Interaction").key == "sportsinteraction_ca_on"


def test_resolve_bookmaker_unsupported_and_unknown():
    assert resolve_bookmaker("Bet365").supported is False
    assert resolve_bookmaker("Bet365").key == "bet365"
    unknown = resolve_bookmaker("MysteryBook")
    assert unknown.key is None and unknown.supported is False


# --- scoped discovery --------------------------------------------------------

def test_discovery_is_scoped_to_promotion_bookmaker(monkeypatch):
    captured = []

    def fake(key, config=None):
        captured.append(key)
        return MockSportsbookProvider()

    monkeypatch.setattr(discovery, "build_bookmaker_provider", fake)
    result = discover_qualifying(
        promotion(), "LIVE", exchange=MockExchangeProvider(), routes=MOCK_ROUTES
    )
    assert captured == ["betmgm_ca_on"]  # never another bookmaker
    assert result.bookmaker_key == "betmgm_ca_on"
    assert result.recommendations
    # Mock providers => SIMULATED, never LIVE VERIFIED.
    assert result.status == OpportunityStatus.SIMULATED.value


def test_unsupported_sportsbook_is_clear():
    result = discover_qualifying(promotion(sportsbook="Bet365"), "LIVE", exchange=MockExchangeProvider())
    assert DiagnosticCode.SPORTSBOOK_UNSUPPORTED.value in result.diagnostics
    assert result.status == OpportunityStatus.UNAVAILABLE.value
    assert not result.recommendations


def test_unknown_sportsbook_is_clear():
    result = discover_qualifying(promotion(sportsbook="MysteryBook"), "LIVE", exchange=MockExchangeProvider())
    assert DiagnosticCode.SPORTSBOOK_UNKNOWN.value in result.diagnostics


def test_no_eligible_markets_diagnostic(monkeypatch):
    monkeypatch.setattr(discovery, "build_bookmaker_provider", lambda *a, **k: MockSportsbookProvider())
    result = discover_qualifying(
        promotion(excluded_markets=("moneyline", "match_winner")), "LIVE",
        exchange=MockExchangeProvider(), routes=MOCK_ROUTES,
    )
    assert DiagnosticCode.NO_ELIGIBLE_MARKETS.value in result.diagnostics


def test_insufficient_liquidity_diagnostic(monkeypatch):
    monkeypatch.setattr(discovery, "build_bookmaker_provider", lambda *a, **k: MockSportsbookProvider())
    result = discover_qualifying(
        promotion(), "LIVE", exchange=MockExchangeProvider(liquidity=Decimal("1")),
        routes=MOCK_ROUTES,
    )
    assert DiagnosticCode.INSUFFICIENT_LIQUIDITY.value in result.diagnostics
    assert not result.recommendations


def test_provider_unavailable_when_exchange_missing(monkeypatch):
    monkeypatch.setattr(discovery, "build_bookmaker_provider", lambda *a, **k: MockSportsbookProvider())
    result = discover_qualifying(promotion(), "LIVE", exchange=None)
    assert DiagnosticCode.PROVIDER_UNAVAILABLE.value in result.diagnostics


# --- status classification ---------------------------------------------------

def test_classify_recommendation():
    opp = discover_mock_opportunities(10)[0]
    now = datetime.now(timezone.utc)
    simulated = _recommendation(opp)
    assert classify_recommendation(simulated, "DEMO") == OpportunityStatus.SIMULATED.value

    fresh = _recommendation(
        replace(opp, source_type="LIVE", is_live=True, fully_hedged=True, timestamp=now)
    )
    assert classify_recommendation(fresh, "LIVE") == OpportunityStatus.LIVE_VERIFIED.value

    stale = _recommendation(
        replace(opp, source_type="LIVE", is_live=True, fully_hedged=True,
                timestamp=now - timedelta(seconds=300))
    )
    assert classify_recommendation(stale, "LIVE") == OpportunityStatus.LIVE_STALE.value

    incomplete = _recommendation(
        replace(opp, source_type="LIVE", is_live=True, fully_hedged=False, timestamp=now)
    )
    assert classify_recommendation(incomplete, "LIVE") == OpportunityStatus.HEDGE_INCOMPLETE.value


# --- conversion discovery ----------------------------------------------------

def test_conversion_discovery(monkeypatch):
    monkeypatch.setattr(discovery, "build_bookmaker_provider", lambda *a, **k: MockSportsbookProvider())
    result = discover_conversion(
        promotion(), Decimal("20"), "LIVE", exchange=MockExchangeProvider(), routes=MOCK_ROUTES
    )
    assert result.recommendations
    assert all(r.opportunity.kind.value == "free_bet_snr" for r in result.recommendations)


# --- benchmark ---------------------------------------------------------------

def test_benchmark_with_mock_providers():
    from services.benchmark import benchmark

    result = benchmark(
        (10, 50), MockSportsbookProvider(), MockExchangeProvider(),
        now=NOW,
    )
    assert result["generated_at"] == NOW.isoformat()
    assert [row["stake"] for row in result["stakes"]] == ["10", "50"]
    assert all("fully_hedgeable" in row for row in result["stakes"])
