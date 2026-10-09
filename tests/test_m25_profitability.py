from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from calculators import calculate_free_bet_snr, calculate_qualifying
from database import (
    create_reward_token,
    get_promotion,
    init_db,
    list_promotion_actions,
    set_promotion_eligibility,
)
from liquidity import build_order_book
from models import ExchangeOdds, NormalizedOdds, Side
from opportunities import BetKind, build_opportunity, discover_mock_opportunities
from promotions import EligibilityStatus, Promotion, RewardToken, promotion_from_row
from promotions.reward_tokens import effective_status, outstanding_face_value
from services import (
    ProfitabilityStatus,
    bankroll_requirement,
    conversion_support,
    discover_profitability,
    evaluate_profitability,
    rank_profitabilities,
    stage_from_recommendation,
)
from services.promotion_workflow import (
    _recommendation,
    confirm_qualifying_settlement,
    confirm_reward_received,
    parse_promotion,
    rank_conversion_bets,
    rank_qualifying_bets,
    record_conversion_placed,
    record_qualifying_placed,
    save_promotion,
)
from settlement import ActualSettlement, prediction_from_opportunity, reconcile

NOW = datetime.now(timezone.utc)
START = NOW + timedelta(hours=2)


def _q(value, places="0.01"):
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _promotion(**overrides):
    base = dict(
        id=1, sportsbook="Book A", name="offer", jurisdiction="Ontario",
        offer_type="BET_GET", new_customer_only=True,
        qualifying_stake=Decimal("10"), qualifying_min_odds=Decimal("1.5"),
        reward_amount=Decimal("20"), reward_type="FREE_BET_SNR", reward_count=1,
        stake_returned=False, status="TERMS_VERIFIED",
        lifecycle_status="TERMS_VERIFIED", eligibility=EligibilityStatus.ELIGIBLE.value,
    )
    base.update(overrides)
    return Promotion(**base)


def _book_quote(odds="2.0", provider="theoddsapi:test"):
    return NormalizedOdds(
        provider=provider, event_id="e1", sport="soccer", league="L",
        home_team="A", away_team="B", start_time=START, market="moneyline",
        selection="A", side=Side.BACK, decimal_odds=Decimal(odds), timestamp=NOW,
    )


def _exchange_quote(odds="2.1", commission="0", available="10000", fee_model="commission", fee_factor=None):
    return ExchangeOdds(
        provider="stx", event_id="e1", sport="soccer", league="L",
        home_team="A", away_team="B", start_time=START, market="moneyline",
        selection="A", side=Side.LAY, decimal_odds=Decimal(odds), timestamp=NOW,
        available_size=Decimal(available), commission=Decimal(commission),
        fee_model=fee_model, fee_factor=Decimal(fee_factor) if fee_factor else None,
        max_price=Decimal("1"),
    )


def _qual_rec(stake="10", back="2.0", lay="2.1", commission="0", **kw):
    opp = build_opportunity(
        _book_quote(back), _exchange_quote(lay, commission=commission), Decimal(stake),
        kind=BetKind.QUALIFYING, **kw,
    )
    return _recommendation(opp)


def _conv_rec(free="20", back="2.0", lay="2.1", commission="0", **kw):
    opp = build_opportunity(
        _book_quote(back), _exchange_quote(lay, commission=commission), Decimal(free),
        kind=BetKind.FREE_BET_SNR, **kw,
    )
    return _recommendation(opp)


# --- Phase 2: qualifying and conversion stage maths --------------------------

def test_qualifying_stage_matches_independent_calculator():
    expected = calculate_qualifying(10, 2.0, 2.1, 0)
    evaluation = evaluate_profitability(_promotion(), _qual_rec(), None)
    assert evaluation.qualifying.found
    assert _q(evaluation.qualifying.lay_stake) == _q(expected.lay_stake)
    assert _q(evaluation.qualifying.lay_liability) == _q(expected.liability)
    assert _q(evaluation.qualifying.worst_case) == _q(expected.qualifying_loss) * -1
    assert evaluation.qualifying.sportsbook_capital == Decimal("10")
    assert _q(evaluation.qualifying.exchange_capital) == _q(expected.liability)
    # Qualifying alone is never shown as a profitable promotion.
    assert evaluation.worst_case_total is None
    assert evaluation.projected_profitable is False


def test_conversion_stage_matches_independent_calculator():
    expected = calculate_free_bet_snr(20, 2.0, 2.1, 0)
    evaluation = evaluate_profitability(_promotion(), _qual_rec(), _conv_rec())
    conv = evaluation.conversion
    assert conv.found
    assert _q(conv.lay_stake) == _q(expected.lay_stake)
    assert _q(conv.lay_liability) == _q(expected.liability)
    assert _q(conv.worst_case) == _q(expected.expected_profit)
    assert _q(conv.conversion_rate) == _q(expected.conversion_rate)
    assert conv.sportsbook_capital == Decimal("0")  # free bet needs no book stake


def test_combined_worst_best_and_capital():
    evaluation = evaluate_profitability(_promotion(), _qual_rec(), _conv_rec())
    q = evaluation.qualifying
    c = evaluation.conversion
    assert evaluation.worst_case_total == q.worst_case + c.worst_case
    assert evaluation.best_case_total == q.best_case + c.best_case
    assert evaluation.status == ProfitabilityStatus.PROFITABLE.value
    assert evaluation.projected_profitable is True
    # Peak = sportsbook stake + qualifying liability + conversion liability.
    assert evaluation.peak_capital == (
        Decimal("10") + q.exchange_capital + c.exchange_capital
    )
    assert evaluation.turnover == q.back_stake + q.lay_stake + c.lay_stake
    assert evaluation.roi_on_capital > 0


# --- expected value is gated behind a probability model ----------------------

def test_combined_result_is_not_labelled_expected_value_without_model():
    evaluation = evaluate_profitability(_promotion(), _qual_rec(), _conv_rec())
    assert evaluation.expected_value is None
    assert "not" in evaluation.expected_value_note.lower()
    assert evaluation.projected_total == evaluation.worst_case_total


def test_expected_value_requires_explicit_model():
    evaluation = evaluate_profitability(
        _promotion(), _qual_rec(), _conv_rec(),
        expected_value_model=lambda _opp: Decimal("1.00"),
    )
    assert evaluation.expected_value == Decimal("2.00")  # qualifying + 1 conversion
    assert "model" in evaluation.expected_value_note.lower()


# --- reward count / per-token conversion -------------------------------------

def test_reward_count_scales_conversion():
    promotion = _promotion(reward_amount=Decimal("40"), reward_count=2)
    assert promotion.reward_token_amount == Decimal("20")
    evaluation = evaluate_profitability(promotion, _qual_rec(), _conv_rec(free="20"))
    assert evaluation.reward_count == 2
    assert evaluation.worst_case_total == (
        evaluation.qualifying.worst_case + evaluation.conversion.worst_case * 2
    )
    assert evaluation.conversion_capital == evaluation.conversion.exchange_capital * 2


# --- reward type / restriction handling --------------------------------------

def test_stake_returned_reward_is_flagged_unsupported():
    promotion = _promotion(reward_type="FREE_BET_SR", stake_returned=True)
    supported, note = conversion_support(promotion)
    assert supported is False
    assert "stake-not-returned" in note
    evaluation = evaluate_profitability(promotion, _qual_rec(), _conv_rec())
    assert evaluation.conversion_supported is False
    assert evaluation.status == ProfitabilityStatus.CONVERSION_UNSUPPORTED.value
    assert evaluation.projected_profitable is False


def test_market_restriction_prevents_conversion():
    promotion = _promotion(eligible_markets=("correct_score",))
    supported, note = conversion_support(promotion)
    assert supported is False
    assert "markets" in note


def test_missing_conversion_candidate_is_reported():
    evaluation = evaluate_profitability(_promotion(), _qual_rec(), None)
    assert evaluation.conversion_supported is True
    assert evaluation.status == ProfitabilityStatus.CONVERSION_UNAVAILABLE.value
    assert evaluation.worst_case_total is None
    assert evaluation.projected_profitable is False


# --- depth / partial fills ---------------------------------------------------

def test_unverified_conversion_depth_blocks_profitability():
    weak = replace(_conv_rec(), fully_hedged=False, depth_status="INSUFFICIENT")
    evaluation = evaluate_profitability(_promotion(), _qual_rec(), weak)
    assert evaluation.status == ProfitabilityStatus.DEPTH_UNVERIFIED.value
    assert evaluation.projected_profitable is False


def test_partial_fill_conversion_is_not_hedgeable():
    book = build_order_book(
        [{"price": "0.60", "quantity": "1"}], "1", NOW, completeness="COMPLETE"
    )
    opp = build_opportunity(
        _book_quote("2.0"),
        _exchange_quote("2.1", fee_model="per_wager", fee_factor="0.10"),
        Decimal("20"), kind=BetKind.FREE_BET_SNR, order_book=book,
    )
    assert opp.fully_hedged is False
    rec = _recommendation(opp)
    evaluation = evaluate_profitability(_promotion(), _qual_rec(), rec)
    assert evaluation.status == ProfitabilityStatus.DEPTH_UNVERIFIED.value


# --- eligibility / terms gates -----------------------------------------------

def test_ineligible_promotion_is_not_profitable():
    evaluation = evaluate_profitability(
        _promotion(eligibility=EligibilityStatus.NOT_ELIGIBLE.value),
        _qual_rec(), _conv_rec(),
    )
    assert evaluation.status == ProfitabilityStatus.INELIGIBLE.value
    assert evaluation.projected_profitable is False


def test_unverified_terms_are_not_profitable():
    evaluation = evaluate_profitability(
        _promotion(status="DISCOVERED", lifecycle_status="DISCOVERED"),
        _qual_rec(), _conv_rec(),
    )
    assert evaluation.status == ProfitabilityStatus.UNVERIFIED_TERMS.value


def test_negative_combined_result_is_not_profitable():
    evaluation = evaluate_profitability(
        _promotion(),
        _qual_rec(back="2.0", lay="3.0"),
        _conv_rec(free="1", back="1.05", lay="1.06"),
    )
    assert evaluation.worst_case_total < 0
    assert evaluation.status == ProfitabilityStatus.NOT_PROFITABLE.value
    assert evaluation.projected_profitable is False


# --- Phase 7: bankroll -------------------------------------------------------

def test_bankroll_requires_both_accounts_to_be_sufficient():
    evaluation = evaluate_profitability(
        _promotion(), _qual_rec(), _conv_rec(),
        balances={"Book A": Decimal("5"), "STX": Decimal("1000")},
    )
    assert evaluation.status == ProfitabilityStatus.INSUFFICIENT_CAPITAL.value
    requirement = bankroll_requirement(evaluation, {"Book A": Decimal("5"), "STX": Decimal("1000")})
    assert requirement.sufficient is False
    assert requirement.sportsbook_shortfall == Decimal("5")


def test_bankroll_sufficient_allows_profitable():
    evaluation = evaluate_profitability(
        _promotion(), _qual_rec(), _conv_rec(),
        balances={"Book A": Decimal("100"), "STX": Decimal("1000")},
    )
    assert evaluation.status == ProfitabilityStatus.PROFITABLE.value


def test_bankroll_unknown_accounts_are_not_treated_as_zero():
    evaluation = evaluate_profitability(
        _promotion(), _qual_rec(), _conv_rec(), balances={"Somewhere else": Decimal("0")}
    )
    requirement = bankroll_requirement(evaluation, {"Somewhere else": Decimal("0")})
    assert requirement.sufficient is None
    assert evaluation.status == ProfitabilityStatus.PROFITABLE.value


# --- quote changes between legs / preview semantics --------------------------

def test_quote_change_changes_projected_total():
    first = evaluate_profitability(_promotion(), _qual_rec(), _conv_rec(lay="2.1"))
    second = evaluate_profitability(_promotion(), _qual_rec(), _conv_rec(lay="2.05"))
    assert first.worst_case_total != second.worst_case_total
    assert first.conversion_preview is True
    assert first.reward_confirmed is False


def test_confirmed_reward_clears_preview_flag():
    evaluation = evaluate_profitability(
        _promotion(), _qual_rec(), _conv_rec(), reward_confirmed=True
    )
    assert evaluation.conversion_preview is False
    assert evaluation.reward_confirmed is True


def test_reward_token_expiry_reduces_outstanding_value():
    expired = RewardToken(
        promotion_id=1, token_key="t", face_value=Decimal("20"),
        reward_type="FREE_BET_SNR", status="RECEIVED",
        expires_at=NOW - timedelta(days=1),
    )
    live = RewardToken(
        promotion_id=1, token_key="t2", face_value=Decimal("20"),
        reward_type="FREE_BET_SNR", status="RECEIVED",
        expires_at=NOW + timedelta(days=1),
    )
    assert effective_status(expired, NOW) == "EXPIRED"
    assert outstanding_face_value([expired, live], NOW) == Decimal("20")


# --- Phase 4: ranking --------------------------------------------------------

def test_rank_orders_profitable_before_unprofitable():
    profitable = evaluate_profitability(_promotion(), _qual_rec(), _conv_rec())
    unprofitable = evaluate_profitability(
        _promotion(id=2), _qual_rec(), None
    )
    ranked = rank_profitabilities([unprofitable, profitable])
    assert ranked[0].promotion_id == 1
    assert ranked[0].projected_profitable is True


def test_stage_capital_flags():
    q = stage_from_recommendation(_qual_rec(), sportsbook_capital=True)
    c = stage_from_recommendation(_conv_rec(), sportsbook_capital=False)
    assert q.sportsbook_capital == Decimal("10")
    assert c.sportsbook_capital == Decimal("0")
    assert q.worst_case <= q.best_case


# --- settlement discrepancy (Phase 9) ----------------------------------------

def test_settlement_discrepancy_is_detected():
    opp = _qual_rec().opportunity
    prediction = prediction_from_opportunity(opp, bet_id=1)
    actual = ActualSettlement(
        bet_id=1, outcome="BACK_WON",
        actual_total_pnl=prediction.predicted_back_win_pnl + Decimal("5"),
        actual_exchange_fee=prediction.predicted_exchange_fee,
        actual_liability=prediction.predicted_liability,
    )
    result = reconcile(prediction, actual)
    assert result.status == "DISCREPANCY"


# --- Phase 3: discovery wrapper (DEMO) ---------------------------------------

def test_discover_profitability_demo_returns_preview():
    promotion = _promotion(sportsbook="Mock Book")
    evaluation, q_result, c_result = discover_profitability(promotion, "DEMO")
    assert q_result.recommendations
    assert c_result.recommendations
    assert evaluation.conversion_preview is True
    assert evaluation.worst_case_total is not None


# --- Phase 6: benchmark ------------------------------------------------------

def test_profitability_benchmark_demo_saves_json(tmp_path):
    import json
    from services.benchmark import run_profitability_benchmark

    result = run_profitability_benchmark(
        _promotion(sportsbook="Mock Book"), mode="DEMO", output_dir=str(tmp_path), save=True
    )
    assert result["status"] == "OK"
    payload = json.loads(open(result["path"]).read())
    assert payload["mode"] == "DEMO"
    assert payload["promotions"][0]["evaluation"]["status"]


def test_profitability_to_dict_is_serialisable():
    import json

    evaluation = evaluate_profitability(_promotion(), _qual_rec(), _conv_rec())
    json.dumps(evaluation.to_dict())


# --- Phase 8: full lifecycle -------------------------------------------------

def test_full_lifecycle_projected_then_realized(tmp_path):
    db = tmp_path / "m25.db"
    init_db(db)
    example = "Bet $10, get a $20 free bet. Minimum odds 1.50. Stake not returned."
    terms = parse_promotion(example)
    promotion_id = save_promotion(terms, "Book A", "offer", jurisdiction="Ontario", terms_text=example, db_path=db)
    set_promotion_eligibility(promotion_id, EligibilityStatus.ELIGIBLE.value, db_path=db)
    stored = promotion_from_row(get_promotion(promotion_id, db_path=db))

    qualifying_pool = [replace(o, fully_hedged=True) for o in discover_mock_opportunities(10)]
    q_rec = rank_qualifying_bets(stored, qualifying_pool)[0]

    # Projected (preview) evaluation before any wager is placed.
    conversion_pool = [replace(o, fully_hedged=True) for o in discover_mock_opportunities(20, kind=BetKind.FREE_BET_SNR)]
    c_rec = rank_conversion_bets(Decimal("20"), conversion_pool)[0]
    projected = evaluate_profitability(
        stored, _recommendation(q_rec.opportunity), _recommendation(c_rec.opportunity)
    )
    assert projected.worst_case_total is not None

    bet_id = record_qualifying_placed(promotion_id, q_rec.opportunity, db_path=db)
    assert list_promotion_actions(promotion_id, db_path=db)
    confirm_qualifying_settlement(bet_id, "BACK_WON", db_path=db)

    token_id = create_reward_token(
        RewardToken(promotion_id=promotion_id, token_key="t1", face_value=Decimal("20")), db_path=db
    )
    confirm_reward_received(promotion_id, [token_id], db_path=db)

    record_conversion_placed(promotion_id, token_id, c_rec.opportunity, db_path=db)

    from services import calculate_net_profit

    # Until the conversion settles, realized conversion is zero.
    pending = calculate_net_profit(promotion_id, db_path=db)
    assert pending["realized_conversion"] == Decimal("0")

    confirm_qualifying_settlement(
        list_promotion_actions(promotion_id, db_path=db)[-1]["bet_id"], "LAY_WON", db_path=db
    )
    realized = calculate_net_profit(promotion_id, db_path=db)
    assert realized["realized_conversion"] != Decimal("0")
    assert realized["realized_net"] == realized["qualifying_loss"] + realized["realized_conversion"]
