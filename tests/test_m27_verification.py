import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from liquidity import build_order_book
from models import ExchangeOdds, NormalizedOdds, Side
from opportunities import BetKind, build_opportunity
from services.verification import (
    FeeSchedule,
    Tolerances,
    VerificationStatus,
    required_contracts,
    reconcile_execution,
    verify_market_equivalence,
    verify_opportunity,
    walk_levels,
)

NOW = datetime.now(timezone.utc)
START = NOW + timedelta(hours=2)
FIXTURE = Path(__file__).resolve().parent.parent / "data/fixtures/rams_proline_stx.json"


def _book_quote(odds="1.10", home="Los Angeles Rams", away="Arizona Cardinals", selection="Los Angeles Rams"):
    return NormalizedOdds(
        provider="theoddsapi:proline_ca_on", event_id="e1", sport="american_football",
        league="NFL", home_team=home, away_team=away, start_time=START, market="moneyline",
        selection=selection, side=Side.BACK, decimal_odds=Decimal(odds), timestamp=NOW,
    )


def _exchange_quote(odds="1.190476", home="Los Angeles Rams", away="Arizona Cardinals", selection="Los Angeles Rams"):
    return ExchangeOdds(
        provider="stx", event_id="e1", sport="american_football", league="NFL",
        home_team=home, away_team=away, start_time=START, market="moneyline",
        selection=selection, side=Side.LAY, decimal_odds=Decimal(odds), timestamp=NOW,
        available_size=Decimal("100000"), commission=Decimal("0"), fee_model="per_wager",
        fee_factor=Decimal("0.10"), max_price=Decimal("1"),
    )


def _opportunity(bids, *, kind=BetKind.QUALIFYING, stake="10", odds="1.10",
                 completeness="COMPLETE", timestamp=NOW, book_quote=None, exchange_quote=None):
    book = build_order_book(bids, "1", timestamp, completeness=completeness)
    return build_opportunity(
        book_quote or _book_quote(odds), exchange_quote or _exchange_quote(),
        Decimal(stake), kind=kind, order_book=book, now=timestamp,
    )


RAMS_BIDS = [{"price": "0.84", "quantity": "3401"}]


# --- contract conversion -----------------------------------------------------

def test_required_contracts_qualifying_and_free_bet():
    assert required_contracts("qualifying", Decimal("10"), Decimal("1.10"), Decimal("1")) == Decimal("11")
    assert required_contracts("free_bet_snr", Decimal("20"), Decimal("2.10"), Decimal("1")) == Decimal("22")
    assert required_contracts("qualifying", Decimal("10"), Decimal("2.00"), Decimal("100")) == Decimal("0.2")


def test_rams_fixture_reconstruction():
    fixture = json.loads(FIXTURE.read_text())
    opp = _opportunity(RAMS_BIDS, stake=fixture["back_stake"], odds=fixture["back_odds"])
    result = verify_opportunity(
        opp, fee_schedule=FeeSchedule(status="VERIFIED", tier=fixture["stx"]["tier"]),
        settlement_rules_confirmed=True, now=NOW,
    )
    expected = fixture["expected"]
    assert result.status == VerificationStatus.EXECUTION_VERIFIED.value
    assert result.independent["contracts"] == Decimal(expected["required_contracts"])
    assert result.independent["lay_stake"] == Decimal(expected["lay_stake"])
    assert result.independent["liability"] == Decimal(expected["liability"])
    assert result.independent["fee"] == Decimal(expected["fee"])
    assert result.independent["worst_case"] == Decimal(expected["worst_case"])
    assert result.independent["effective_lay_odds"].quantize(Decimal("0.001")) == Decimal(expected["effective_lay_odds"])
    assert result.discrepancies == ()


# --- multiple levels / fills / fee aggregation -------------------------------

def test_multiple_price_levels_weighted_average():
    bids = [{"price": "0.83", "quantity": "6"}, {"price": "0.85", "quantity": "5"}]
    opp = _opportunity(bids)
    result = verify_opportunity(opp, now=NOW)
    ind = result.independent
    assert ind["contracts"] == Decimal("11")
    assert ind["lay_stake"] == Decimal("9.23")          # 5*0.85 + 6*0.83
    assert ind["liability"] == Decimal("1.77")          # 5*0.15 + 6*0.17
    assert ind["fee"] == Decimal("0.15")
    assert ind["average_price"] == Decimal("9.23") / Decimal("11")
    assert result.discrepancies == ()


def test_multi_fill_fee_is_aggregated_then_rounded_once():
    # Two fills of ~5.5 contracts; ceiling once at order level, not per fill.
    levels = [type("L", (), {"price": Decimal("0.84"), "contracts": Decimal("5")})(),
              type("L", (), {"price": Decimal("0.84"), "contracts": Decimal("6")})()]
    walk = walk_levels(levels, Decimal("11"), Decimal("1"))
    schedule = FeeSchedule()
    assert schedule.order_fee(walk["fills"], Decimal("1")) == Decimal("0.15")


def test_partial_liquidity_is_blocked():
    opp = _opportunity([{"price": "0.84", "quantity": "4"}])
    result = verify_opportunity(opp, now=NOW)
    assert result.status == VerificationStatus.BLOCKED.value
    assert "insufficient executable depth" in result.reasons[0]


def test_incorrect_order_book_side_changes_result():
    # Bids (lay side) give 0.84; offers would be lower and produce a worse hedge.
    bids = walk_levels([type("L", (), {"price": Decimal("0.84"), "contracts": Decimal("100")})()], Decimal("11"), Decimal("1"))
    offers = walk_levels([type("L", (), {"price": Decimal("0.80"), "contracts": Decimal("100")})()], Decimal("11"), Decimal("1"))
    assert bids["lay_stake"] > offers["lay_stake"]
    assert bids["liability"] < offers["liability"]


# --- fees --------------------------------------------------------------------

def test_maker_vs_taker_fee():
    levels = [type("L", (), {"price": Decimal("0.84"), "contracts": Decimal("11")})()]
    fills = walk_levels(levels, Decimal("11"), Decimal("1"))["fills"]
    assert FeeSchedule(side="taker", taker_factor=Decimal("0.10")).order_fee(fills, Decimal("1")) == Decimal("0.15")
    assert FeeSchedule(side="maker", maker_factor=Decimal("0.00")).order_fee(fills, Decimal("1")) == Decimal("0")


def test_veteran_tier_lower_fee():
    levels = [type("L", (), {"price": Decimal("0.50"), "contracts": Decimal("100")})()]
    fills = walk_levels(levels, Decimal("100"), Decimal("1"))["fills"]
    rookie = FeeSchedule(taker_factor=Decimal("0.10")).order_fee(fills, Decimal("1"))
    veteran = FeeSchedule(taker_factor=Decimal("0.08")).order_fee(fills, Decimal("1"))
    assert rookie == Decimal("2.50")
    assert veteran == Decimal("2.00")


def test_estimated_fee_prevents_execution_verified():
    opp = _opportunity(RAMS_BIDS)
    result = verify_opportunity(opp, now=NOW)  # default schedule is ESTIMATED
    assert result.status == VerificationStatus.PARTIALLY_VERIFIED.value
    assert any("fee" in r.lower() for r in result.reasons)


def test_unverified_fee_model_never_execution_verified():
    opp = _opportunity(RAMS_BIDS)
    result = verify_opportunity(
        opp, fee_schedule=FeeSchedule(status="UNVERIFIED"), now=NOW
    )
    assert result.status != VerificationStatus.EXECUTION_VERIFIED.value


# --- freshness ---------------------------------------------------------------

def test_stale_order_book_is_blocked():
    old = NOW - timedelta(seconds=200)
    opp = _opportunity(RAMS_BIDS, timestamp=old)
    result = verify_opportunity(opp, now=NOW, max_book_age_seconds=30)
    assert result.status == VerificationStatus.BLOCKED.value


# --- market equivalence ------------------------------------------------------

def test_equivalence_detects_different_event():
    result = verify_market_equivalence(_book_quote(), _exchange_quote(away="Dallas Cowboys"))
    assert result.equivalent is False
    opp = _opportunity(RAMS_BIDS, exchange_quote=_exchange_quote(away="Dallas Cowboys"))
    blocked = verify_opportunity(opp, now=NOW)
    assert blocked.status == VerificationStatus.BLOCKED.value
    assert "equivalence" in blocked.reasons[0].lower()


def test_different_settlement_rules_partial_not_verified():
    opp = _opportunity(RAMS_BIDS)
    result = verify_opportunity(opp, fee_schedule=FeeSchedule(status="VERIFIED"),
                                settlement_rules_confirmed=False, now=NOW)
    assert result.status == VerificationStatus.PARTIALLY_VERIFIED.value
    assert result.settlement_rules_known is False


# --- optimizer disagreement --------------------------------------------------

def test_tampered_optimizer_output_is_blocked():
    opp = _opportunity(RAMS_BIDS)
    tampered = replace(opp, lay_liability=Decimal("2.50"))
    result = verify_opportunity(tampered, now=NOW)
    assert result.status == VerificationStatus.BLOCKED.value
    assert any("liability" in d for d in result.discrepancies)


# --- promotional (free bet) bets --------------------------------------------

def test_free_bet_outcomes_and_verification():
    bids = [{"price": "0.60", "quantity": "1000"}]
    opp = _opportunity(bids, kind=BetKind.FREE_BET_SNR, stake="20", odds="2.10")
    result = verify_opportunity(opp, fee_schedule=FeeSchedule(status="VERIFIED"),
                                settlement_rules_confirmed=True, now=NOW)
    ind = result.independent
    # Q = 20*(2.10-1)/1 = 22; lay_stake = 22*0.60 = 13.20; liability = 22*0.40 = 8.80
    assert ind["contracts"] == Decimal("22")
    assert ind["lay_stake"] == Decimal("13.20")
    assert ind["liability"] == Decimal("8.80")
    assert result.discrepancies == ()


# --- post-execution reconciliation -------------------------------------------

def test_reconcile_full_hedge():
    opp = _opportunity(RAMS_BIDS)
    rec = reconcile_execution(opp, [(Decimal("0.84"), Decimal("11"))], fee_schedule=FeeSchedule(status="VERIFIED"))
    assert rec.status == "HEDGED"
    assert rec.partial is False
    assert rec.actual_contracts == Decimal("11")


def test_reconcile_partial_hedge_exposes_risk():
    opp = _opportunity(RAMS_BIDS)
    rec = reconcile_execution(opp, [(Decimal("0.84"), Decimal("5"))], fee_schedule=FeeSchedule(status="VERIFIED"))
    assert rec.status == "PARTIAL_HEDGE"
    assert rec.partial is True
    assert rec.exposure > Decimal("0")


def test_no_actual_fills_is_unverified():
    opp = _opportunity(RAMS_BIDS)
    rec = reconcile_execution(opp, [])
    assert rec.status == "UNVERIFIED"
