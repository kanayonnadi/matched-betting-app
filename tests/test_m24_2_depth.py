from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from liquidity import build_order_book, simulate_execution
from liquidity.models import DepthCompleteness, DepthStatus
from models import ExchangeOdds, NormalizedOdds, Side
from opportunities import build_opportunity

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
START = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)


def book(bids, completeness="UNKNOWN", timestamp=NOW):
    return build_order_book(bids, "1", timestamp, completeness=completeness)


# --- available depth vs hedge quantity ---------------------------------------

def test_available_depth_is_independent_of_hedge_quantity():
    bids = [
        {"price": "0.63", "quantity": "50"},
        {"price": "0.61", "quantity": "35"},
        {"price": "0.60", "quantity": "40"},
    ]
    execution = simulate_execution(book(bids), Decimal("64"), Decimal("0.10"), now=NOW)
    # Required hedge is 64; independently available depth is 125.
    assert execution.requested_contracts == Decimal("64")
    assert execution.fillable_contracts == Decimal("64")
    assert execution.total_depth_contracts == Decimal("125")
    assert execution.depth_status == DepthStatus.VERIFIED.value


def test_unknown_completeness_with_shortfall_is_unknown_not_insufficient():
    bids = [{"price": "0.62", "quantity": "10"}]
    execution = simulate_execution(book(bids, completeness="UNKNOWN"), Decimal("100"), Decimal("0.10"), now=NOW)
    assert execution.depth_status == DepthStatus.UNKNOWN.value


def test_complete_shortfall_is_insufficient():
    bids = [{"price": "0.62", "quantity": "10"}]
    execution = simulate_execution(book(bids, completeness="COMPLETE"), Decimal("100"), Decimal("0.10"), now=NOW)
    assert execution.depth_status == DepthStatus.INSUFFICIENT.value


def test_empty_complete_book_is_insufficient():
    execution = simulate_execution(book([], completeness="COMPLETE"), Decimal("10"), Decimal("0.10"), now=NOW)
    assert execution.depth_status == DepthStatus.INSUFFICIENT.value


def test_empty_unknown_book_is_unknown():
    execution = simulate_execution(book([]), Decimal("10"), Decimal("0.10"), now=NOW)
    assert execution.depth_status == DepthStatus.UNKNOWN.value


def test_malformed_depth_is_invalid():
    bids = [{"price": "0", "quantity": "10"}, {"price": "0.5", "quantity": "10"}]
    ob = book(bids, completeness="COMPLETE")
    execution = simulate_execution(ob, Decimal("5"), Decimal("0.10"), now=NOW)
    assert ob.malformed_levels == 1
    assert execution.depth_status == DepthStatus.INVALID.value


def test_stale_depth():
    old = NOW - timedelta(seconds=120)
    execution = simulate_execution(
        book([{"price": "0.62", "quantity": "100"}], timestamp=old),
        Decimal("10"), Decimal("0.10"), now=NOW, max_book_age_seconds=30,
    )
    assert execution.depth_status == DepthStatus.STALE.value


# --- multi-level fills -------------------------------------------------------

def test_multi_level_fill_best_first():
    bids = [
        {"price": "0.60", "quantity": "40"},
        {"price": "0.61", "quantity": "35"},
        {"price": "0.63", "quantity": "50"},
    ]
    execution = simulate_execution(book(bids, completeness="COMPLETE"), Decimal("100"), Decimal("0.10"), now=NOW)
    # Best-first = highest price (best lay) first: 0.63, then 0.61, then 0.60.
    assert [(str(f.price), str(f.contracts)) for f in execution.fills] == [
        ("0.63", "50"), ("0.61", "35"), ("0.60", "15"),
    ]
    assert execution.lay_stake == Decimal("61.85")          # 50*0.63 + 35*0.61 + 15*0.60
    assert execution.liability == Decimal("38.15")          # 100 - 61.85
    assert execution.estimated_fee == Decimal("2.36")       # ceil(2.35815)
    assert execution.slippage_pct > 0
    assert execution.depth_status == DepthStatus.VERIFIED.value


def test_fractional_contracts_supported():
    execution = simulate_execution(
        book([{"price": "0.60", "quantity": "10"}], completeness="COMPLETE"),
        Decimal("2.5"), Decimal("0.10"), now=NOW,
    )
    assert execution.executable_contracts if hasattr(execution, "executable_contracts") else True
    assert execution.fillable_contracts == Decimal("2.5")


# --- engine integration ------------------------------------------------------

def _opportunity(required_stake, bids, completeness="COMPLETE"):
    book_quote = NormalizedOdds(
        provider="theoddsapi:betmgm_ca_on", event_id="b1", sport="ice_hockey", league="NHL",
        home_team="A", away_team="B", start_time=START, market="moneyline", selection="A",
        side=Side.BACK, decimal_odds=Decimal("1.60"), timestamp=NOW,
    )
    exchange_quote = ExchangeOdds(
        provider="stx", event_id="s1", sport="ice_hockey", league="NHL",
        home_team="A", away_team="B", start_time=START, market="moneyline", selection="A",
        side=Side.LAY, decimal_odds=Decimal("1.6129"), timestamp=NOW,
        available_size=Decimal("285.82"), commission=Decimal("0"), fee_model="per_wager",
        fee_factor=Decimal("0.10"), max_price=Decimal("1"),
    )
    order_book = build_order_book(bids, "1", NOW, completeness=completeness)
    return build_opportunity(book_quote, exchange_quote, required_stake, order_book=order_book, now=NOW)


def test_engine_exposes_available_depth_and_status():
    opp = _opportunity(64, [{"price": "0.63", "quantity": "50"}, {"price": "0.61", "quantity": "75"}])
    assert opp.depth_status == "VERIFIED"
    assert opp.fully_hedged is True
    assert opp.available_contracts == Decimal("125")
    assert opp.requested_contracts > 0
    assert opp.executable_contracts == opp.requested_contracts
    assert opp.requested_contracts <= opp.available_contracts


def test_engine_unknown_depth_not_fully_hedged():
    opp = _opportunity(100, [{"price": "0.62", "quantity": "10"}], completeness="UNKNOWN")
    assert opp.depth_status == "UNKNOWN"
    assert opp.fully_hedged is False


# --- benchmark depth provenance ----------------------------------------------

def test_benchmark_reports_available_depth(monkeypatch):
    from services.benchmark import benchmark
    from providers import MockExchangeProvider, MockSportsbookProvider

    result = benchmark((10,), MockSportsbookProvider(), MockExchangeProvider(), now=NOW)
    row = result["stakes"][0]
    assert "verified_depth" in row
    assert "unknown_depth" in row
    assert "median_available_depth" in row
    assert result["provider_mode"] == "DEMO"
