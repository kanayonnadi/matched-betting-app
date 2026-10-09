import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from database import (
    get_promotion,
    init_db,
    list_reward_tokens,
    set_promotion_eligibility,
    update_reward_token,
)
from models import ExchangeOdds, NormalizedOdds, Side
from opportunities import BetKind, build_opportunity, discover_mock_opportunities
from promotions import (
    EligibilityStatus,
    Promotion,
    RewardCollection,
    RewardStatus,
    RewardToken,
    RewardUnit,
    apply_credit,
    collection_from_promotion,
    evaluate_restrictions,
    ingest_promotion,
    parse_terms,
    promotion_from_row,
    promotion_from_terms,
    reward_can_transition,
    reward_canonical_status,
)
from services import (
    ProfitabilityStatus,
    calculate_net_profit,
    confirm_qualifying_settlement,
    confirm_reward_received,
    evaluate_reward_collection_profitability,
    issue_reward_tokens,
    plan_reward_conversions,
    record_conversion_placed,
    save_promotion,
)
from services.profitability import RewardConversion
from services.promotion_workflow import _recommendation

FIXTURE = Path(__file__).resolve().parent.parent / "data/fixtures/olg_proline_bet10_get100.json"
NOW = datetime.now(timezone.utc)
START = NOW + timedelta(hours=2)
ZERO = Decimal("0")


# --- helpers -----------------------------------------------------------------

def _book_quote(odds="2.0"):
    return NormalizedOdds(
        provider="theoddsapi:olg", event_id="e1", sport="soccer", league="L",
        home_team="A", away_team="B", start_time=START, market="moneyline",
        selection="A", side=Side.BACK, decimal_odds=Decimal(odds), timestamp=NOW,
    )


def _exchange_quote(odds="2.1"):
    return ExchangeOdds(
        provider="stx", event_id="e1", sport="soccer", league="L", home_team="A",
        away_team="B", start_time=START, market="moneyline", selection="A",
        side=Side.LAY, decimal_odds=Decimal(odds), timestamp=NOW,
        available_size=Decimal("10000"), commission=ZERO,
    )


def _opportunity(amount, kind=BetKind.FREE_BET_SNR, **overrides):
    opp = build_opportunity(_book_quote(), _exchange_quote(), Decimal(amount), kind=kind)
    return replace(opp, **overrides) if overrides else opp


def _conv_rec(amount=20, available=100, executable=40, depth_status="VERIFIED",
              fully_hedged=True, market="moneyline", selection="A"):
    opp = replace(
        _opportunity(amount),
        available_contracts=Decimal(str(available)),
        executable_contracts=Decimal(str(executable)),
        depth_status=depth_status,
        fully_hedged=fully_hedged,
        market=market,
        selection=selection,
    )
    return _recommendation(opp)


def _qual_rec():
    return _recommendation(_opportunity(10, kind=BetKind.QUALIFYING))


def _promotion(**overrides):
    base = dict(
        id=1, sportsbook="OLG Proline", name="OLG", jurisdiction="Ontario",
        offer_type="BET_GET", qualifying_stake=Decimal("10"),
        qualifying_min_odds=Decimal("1.5"), reward_amount=Decimal("100"),
        reward_type="FREE_BET_SNR", reward_count=5, reward_unit_amount=Decimal("20"),
        stake_returned=False, status="TERMS_VERIFIED",
        lifecycle_status="TERMS_VERIFIED", eligibility=EligibilityStatus.ELIGIBLE.value,
        reward_expiry_hours=168, reward_issue_after_settlement=True, confidence=0.9,
    )
    base.update(overrides)
    return Promotion(**base)


def _collection(promotion):
    return collection_from_promotion(promotion)


# --- Phase 3: parsing --------------------------------------------------------

def test_parse_one_free_bet():
    terms = parse_terms("Bet $10, get a $20 free bet. Minimum odds 1.50.")
    assert terms.reward_count == 1
    assert terms.reward_unit_amount == Decimal("20")
    assert terms.reward_amount == Decimal("20")


def test_parse_five_twenty_free_bets():
    terms = parse_terms("Bet $10, get $100 in five $20 Sports Bonus Bets.")
    assert terms.reward_count == 5
    assert terms.reward_unit_amount == Decimal("20")
    assert terms.reward_amount == Decimal("100")
    assert terms.reward_denominations == tuple(Decimal("20") for _ in range(5))


def test_parse_three_unequal_free_bets():
    terms = parse_terms("Bet $10, get two $25 free bets and one $50 free bet.")
    assert terms.reward_count == 3
    assert terms.reward_unit_amount is None
    assert terms.reward_denominations == (Decimal("25"), Decimal("25"), Decimal("50"))
    assert terms.reward_amount == Decimal("100")


def test_parse_missing_denomination_is_flagged():
    terms = parse_terms("Bet $10, get $100 in Sports Bonus Bets.")
    assert "reward_denomination" in terms.unknown_fields


def test_parse_inconsistent_total_is_flagged():
    terms = parse_terms("Bet $10, get $100 in three $20 free bets.")
    assert "reward_total_mismatch" in terms.unknown_fields


def test_parse_olg_fixture_terms():
    data = json.loads(FIXTURE.read_text())
    terms = parse_terms(data["terms_text"])
    assert terms.reward_count == 5
    assert terms.reward_unit_amount == Decimal("20")
    assert terms.reward_amount == Decimal("100")
    assert terms.reward_expiry_hours == 168
    assert terms.reward_issue_after_settlement is True
    assert terms.stake_returned is False


def test_collection_from_promotion_equal_and_unequal():
    equal = _collection(_promotion())
    assert equal.count == 5
    assert equal.total_face_value == Decimal("100")
    assert equal.unit_amount == Decimal("20")
    unequal = _collection(
        _promotion(reward_count=3, reward_unit_amount=None,
                   reward_denominations=(Decimal("25"), Decimal("25"), Decimal("50")))
    )
    assert unequal.unit_amount is None
    assert unequal.distinct_amounts() == (Decimal("25"), Decimal("50"))


# --- Phase 2/6: reward lifecycle model --------------------------------------

def test_legacy_status_mapping_and_transitions():
    assert reward_canonical_status("RECEIVED") == "CREDITED"
    assert reward_canonical_status("USED") == "REDEEMED"
    assert reward_can_transition("CREDITED", "RESERVED")
    assert reward_can_transition("RESERVED", "REDEEMED")
    assert reward_can_transition("REDEEMED", "SETTLED")
    assert not reward_can_transition("SETTLED", "REDEEMED")
    assert reward_can_transition("CREDITED", "EXPIRED")


def test_apply_credit_sets_expiry():
    collection = RewardCollection(
        reward_type="FREE_BET_SNR",
        rewards=(RewardUnit(reward_id="reward_1", amount=Decimal("20")),),
        expiry_hours=168,
    )
    credited = apply_credit(collection, NOW)
    unit = credited.rewards[0]
    assert unit.canonical_status == RewardStatus.CREDITED.value
    assert unit.expires_at == NOW + timedelta(hours=168)
    assert credited.rewards_received == 1


def test_reward_expiry_is_respected():
    unit = RewardUnit(
        reward_id="reward_1", amount=Decimal("20"),
        status=RewardStatus.CREDITED.value, expires_at=NOW - timedelta(hours=1),
    )
    assert unit.effective_status(NOW) == RewardStatus.EXPIRED.value


# --- Phase 4/5: per-reward conversions and aggregate liquidity --------------

def test_five_independent_rewards_each_planned():
    promotion = _promotion()
    plan = plan_reward_conversions(_collection(promotion), {Decimal("20"): [_conv_rec()]}, mode="LIVE")
    assert len(plan) == 5
    assert all(rc.amount == Decimal("20") for rc in plan)


def test_aggregate_depth_limits_verified_rewards():
    promotion = _promotion()
    # available 100 contracts, each reward needs 40 -> only 2 fit.
    plan = plan_reward_conversions(
        _collection(promotion), {Decimal("20"): [_conv_rec(available=100, executable=40)]}, mode="LIVE"
    )
    assert sum(1 for rc in plan if rc.verified) == 2
    assert sum(1 for rc in plan if not rc.verified) == 3
    assert any("aggregate" in rc.reason for rc in plan if not rc.verified)


def test_shared_market_is_marked_reused():
    promotion = _promotion()
    plan = plan_reward_conversions(
        _collection(promotion), {Decimal("20"): [_conv_rec(available=1000, executable=10)]}, mode="LIVE"
    )
    assert plan[0].reused is False
    assert all(rc.reused for rc in plan[1:])


def test_partial_rewards_status_and_conditional_total():
    promotion = _promotion()
    plan_pools = {Decimal("20"): [_conv_rec(available=100, executable=40)]}
    evaluation = evaluate_reward_collection_profitability(
        promotion, _qual_rec(), _collection(promotion), plan_pools, mode="LIVE"
    )
    assert evaluation.status == ProfitabilityStatus.PARTIAL_REWARDS.value
    assert evaluation.worst_case_total is None  # not fully verified
    assert evaluation.verified_reward_count == 2
    assert evaluation.unverified_reward_count == 3
    assert evaluation.conditional_worst_case_total is not None
    assert evaluation.projected_profitable is False


def test_all_verified_gives_combined_total():
    promotion = _promotion(reward_count=2, reward_amount=Decimal("40"))
    pools = {Decimal("20"): [_conv_rec(available=1000, executable=10)]}
    evaluation = evaluate_reward_collection_profitability(
        promotion, _qual_rec(), _collection(promotion), pools, mode="LIVE"
    )
    assert evaluation.status == ProfitabilityStatus.PROFITABLE.value
    assert evaluation.worst_case_total == (
        evaluation.qualifying.worst_case + evaluation.verified_conversion_total
    )
    assert evaluation.verified_reward_count == 2


def test_unconvertible_reward_value_not_counted_as_profit():
    promotion = _promotion(reward_count=2, reward_amount=Decimal("40"))
    evaluation = evaluate_reward_collection_profitability(
        promotion, _qual_rec(), _collection(promotion), {}, mode="LIVE"
    )
    assert evaluation.status == ProfitabilityStatus.CONVERSION_UNAVAILABLE.value
    assert evaluation.unconverted_face_value == Decimal("40")
    assert evaluation.worst_case_total is None


def test_aggregate_bankroll_requirements():
    promotion = _promotion()
    pools = {Decimal("20"): [_conv_rec(available=10000, executable=10)]}
    evaluation = evaluate_reward_collection_profitability(
        promotion, _qual_rec(), _collection(promotion), pools, mode="LIVE"
    )
    expected_conversion_capital = evaluation.conversion.exchange_capital * 5
    assert evaluation.conversion_capital == expected_conversion_capital
    assert evaluation.peak_capital >= evaluation.conversion_capital


def test_single_reward_collection_backward_compatible():
    promotion = _promotion(reward_count=1, reward_amount=Decimal("20"), reward_unit_amount=Decimal("20"))
    collection = _collection(promotion)
    assert collection.count == 1
    assert collection.total_face_value == Decimal("20")
    assert collection.unit_amount == Decimal("20")


# --- Phase 8: operator restrictions -----------------------------------------

def test_restricted_operator_promotion_is_flagged():
    promotion = _promotion(
        terms_text="General terms: minimum risk or matched betting is prohibited."
    )
    check = evaluate_restrictions(promotion)
    assert check.restricted
    evaluation = evaluate_reward_collection_profitability(
        promotion, _qual_rec(), _collection(promotion),
        {Decimal("20"): [_conv_rec(available=10000, executable=10)]}, mode="LIVE",
    )
    assert evaluation.status == ProfitabilityStatus.STRATEGY_RESTRICTED.value
    assert evaluation.projected_profitable is False
    assert evaluation.conditional_worst_case_total is not None  # hypothetical only
    assert "MATCHED_BETTING_RESTRICTED" in evaluation.restriction_flags


def test_absence_of_restriction_is_compliant():
    check = evaluate_restrictions(_promotion(terms_text="Bet $10 get $100. Ontario only."))
    assert check.compliant


# --- Phase 10: DB persistence and lifecycle ---------------------------------

def _saved_promotion(tmp_path, **overrides):
    db = tmp_path / "m25_3.db"
    init_db(db)
    terms = parse_terms(
        "Bet $10, get $100 in five $20 Sports Bonus Bets. "
        "Bonus bets expire 168 hours after credit. Stake not returned. Ontario only."
    )
    promotion_id = save_promotion(
        terms, "OLG Proline", "OLG", jurisdiction="Ontario", db_path=db
    )
    set_promotion_eligibility(promotion_id, EligibilityStatus.ELIGIBLE.value, db_path=db)
    promotion = promotion_from_row(get_promotion(promotion_id, db_path=db))
    return db, promotion_id, promotion


def test_issue_tokens_idempotent_and_persistent(tmp_path):
    db, promotion_id, promotion = _saved_promotion(tmp_path)
    ids = issue_reward_tokens(promotion, db_path=db)
    assert len(ids) == 5
    again = issue_reward_tokens(promotion, db_path=db)
    assert again == ids  # no duplicates after a repeat / restart
    tokens = list_reward_tokens(promotion_id, db_path=db)
    assert len(tokens) == 5
    assert {t["token_key"] for t in tokens} == {
        f"promotion:{promotion_id}:reward_{i}" for i in range(1, 6)
    }


def test_reward_credit_redeem_settle_lifecycle(tmp_path):
    db, promotion_id, promotion = _saved_promotion(tmp_path)
    ids = issue_reward_tokens(promotion, db_path=db)

    confirm_reward_received(promotion_id, ids, expiry_hours=168, db_path=db)
    tokens = list_reward_tokens(promotion_id, db_path=db)
    assert all(reward_canonical_status(t["status"]) == "CREDITED" for t in tokens)
    assert all(t["expires_at"] for t in tokens)

    conversion = _opportunity(20)
    bet_id = record_conversion_placed(promotion_id, ids[0], conversion, db_path=db)
    tokens = list_reward_tokens(promotion_id, db_path=db)
    first = next(t for t in tokens if t["id"] == ids[0])
    assert reward_canonical_status(first["status"]) == "REDEEMED"

    # Duplicate redemption is rejected.
    with pytest.raises(ValueError):
        record_conversion_placed(promotion_id, ids[0], conversion, db_path=db)

    confirm_qualifying_settlement(bet_id, "LAY_WON", db_path=db)
    first = next(t for t in list_reward_tokens(promotion_id, db_path=db) if t["id"] == ids[0])
    assert reward_canonical_status(first["status"]) == "SETTLED"
    assert first["realized_pnl"] is not None

    # Partial completion: the other four remain credited.
    others = [t for t in list_reward_tokens(promotion_id, db_path=db) if t["id"] != ids[0]]
    assert all(reward_canonical_status(t["status"]) == "CREDITED" for t in others)


def test_reward_expiry_persisted(tmp_path):
    db, promotion_id, promotion = _saved_promotion(tmp_path)
    ids = issue_reward_tokens(promotion, db_path=db)
    confirm_reward_received(promotion_id, ids, expiry_hours=168, db_path=db)
    past = (NOW - timedelta(hours=1)).isoformat()
    update_reward_token(ids[0], expires_at=past, db_path=db)
    tokens = {t["id"]: t for t in list_reward_tokens(promotion_id, db_path=db)}
    assert tokens[ids[0]]["expires_at"].startswith((NOW - timedelta(hours=1)).strftime("%Y-%m-%d"))


# --- Phase 10: ingestion + UI ------------------------------------------------

def test_ingest_pasted_olg_terms():
    data = json.loads(FIXTURE.read_text())
    result = ingest_promotion(data["terms_text"])
    assert result.terms.reward_count == 5
    assert result.terms.reward_unit_amount == Decimal("20")
    assert result.terms.reward_expiry_hours == 168


def test_fixture_is_labeled_as_real_documented_terms():
    data = json.loads(FIXTURE.read_text())
    assert "NOT evidence" in data["label"]
    assert data["expected"]["reward_count"] == 5
    assert data["expected"]["strategy_compliance"] == "RESTRICTED"


def test_split_evaluation_over_fixture_marks_restricted():
    data = json.loads(FIXTURE.read_text())
    promotion = _promotion(terms_text=data["general_terms"])
    evaluation = evaluate_reward_collection_profitability(
        promotion, _qual_rec(), _collection(promotion),
        {Decimal("20"): [_conv_rec(available=10000, executable=10)]}, mode="LIVE",
    )
    assert evaluation.reward_count == 5
    assert evaluation.strategy_compliance == "RESTRICTED"
    assert evaluation.projected_profitable is False


# --- Phase 10: Streamlit UI rendering ----------------------------------------

def test_ui_renders_split_reward_table(monkeypatch):
    from streamlit.testing.v1 import AppTest

    import ui.promotion_finder as pf
    from services.discovery import DiscoveryResult, OpportunityStatus

    promotion = _promotion(id=1, sportsbook="OLG Proline")
    pools = {Decimal("20"): [_conv_rec(available=10000, executable=10)]}
    evaluation = evaluate_reward_collection_profitability(
        promotion, _qual_rec(), _collection(promotion), pools, mode="DEMO"
    )

    row = {
        "id": 1, "sportsbook": "OLG Proline", "name": "OLG", "jurisdiction": "Ontario",
        "offer_type": "BET_GET", "new_customer_only": True,
        "qualifying_stake": Decimal("10"), "qualifying_min_odds": Decimal("1.5"),
        "qualifying_max_odds": None, "reward_amount": Decimal("100"),
        "reward_type": "FREE_BET_SNR", "reward_count": 5,
        "stake_returned": False, "reward_expiry_days": None, "source_url": None,
        "terms_text": None, "status": "TERMS_VERIFIED", "confidence": 0.9,
        "eligibility": "Eligible",
    }
    monkeypatch.setattr(pf, "list_promotions", lambda *a, **k: [row])
    monkeypatch.setattr(pf, "get_promotion", lambda *a, **k: row)
    monkeypatch.setattr(pf, "sync_workflow", lambda *a, **k: "DRAFT")
    monkeypatch.setattr(pf, "list_reward_tokens", lambda *a, **k: [])
    monkeypatch.setattr(pf, "_bankroll_available", lambda: None)
    monkeypatch.setattr(
        pf, "calculate_net_profit",
        lambda *a, **k: {"qualifying_loss": ZERO, "pending_conversion": ZERO,
                         "realized_net": ZERO, "outstanding_liability": ZERO},
    )

    def fake_discover(promotion, mode="LIVE", **kwargs):
        discovery = DiscoveryResult((), (), OpportunityStatus.SIMULATED.value, "OK", mode)
        return evaluation, discovery, discovery

    monkeypatch.setattr(pf, "discover_profitability", fake_discover)

    app = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=90)
    app.run()
    next(b for b in app.button if b.key == "pf_prof_evaluate").click().run()
    assert not app.exception, [str(e) for e in app.exception]
    # The per-reward table rendered with five rows.
    reward_tables = [df.value for df in app.dataframe if "Face" in getattr(df.value, "columns", [])]
    assert any(len(df) == 5 for df in reward_tables)


def test_pipeline_flags_restricted_promotion_for_review():
    from promotions.pipeline import CandidateStatus, evaluate_promotion

    promotion = _promotion(
        terms_text="General terms: minimum risk betting is prohibited.",
    )
    evaluation = evaluate_promotion(promotion, [])
    assert evaluation.status == CandidateStatus.REVIEW.value
    assert any("minimal-risk" in w or "restrict" in w for w in evaluation.warnings)
