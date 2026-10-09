from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from database import (
    create_reward_token,
    init_db,
    list_bets,
    list_promotion_actions,
    list_reservations,
    set_promotion_eligibility,
)
from opportunities import BetKind, discover_mock_opportunities
from promotions import (
    EligibilityStatus,
    Promotion,
    PromotionFacts,
    PromotionTerms,
    RewardToken,
)
from services import (
    WorkflowState,
    calculate_net_profit,
    confirm_qualifying_settlement,
    confirm_reward_received,
    derive_state,
    parse_promotion,
    rank_conversion_bets,
    rank_qualifying_bets,
    record_conversion_placed,
    record_qualifying_placed,
    save_promotion,
    sync_workflow,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)

EXAMPLE = "Bet $10, get a $20 free bet. Minimum odds 1.50. Stake not returned."


def _promotion(**overrides):
    base = dict(
        id=1, sportsbook="Book A", name="offer", jurisdiction="Ontario",
        offer_type="BET_GET", new_customer_only=True,
        qualifying_stake=Decimal("10"), qualifying_min_odds=Decimal("1.4"),
        reward_amount=Decimal("20"), reward_type="FREE_BET_SNR", reward_count=1,
        stake_returned=False, status="TERMS_VERIFIED",
        lifecycle_status="TERMS_VERIFIED", eligibility=EligibilityStatus.ELIGIBLE.value,
    )
    base.update(overrides)
    return Promotion(**base)


# --- parsing -----------------------------------------------------------------

def test_parse_promotion_extracts_and_flags_unknowns():
    terms = parse_promotion(EXAMPLE)
    assert terms.qualifying_stake == Decimal("10")
    assert terms.reward_amount == Decimal("20")
    assert terms.qualifying_min_odds == Decimal("1.50")
    assert terms.stake_returned is False


def test_parse_promotion_fallback_overrides():
    terms = parse_promotion("Get a bonus", {"reward_amount": Decimal("50"), "qualifying_stake": Decimal("5")})
    assert terms.reward_amount == Decimal("50")
    assert terms.qualifying_stake == Decimal("5")


# --- workflow states ---------------------------------------------------------

def test_derive_state_progression():
    promotion = _promotion()
    assert derive_state(promotion, PromotionFacts(), now=NOW) == WorkflowState.DRAFT  # no qualifying yet
    assert derive_state(promotion, PromotionFacts(), qualifying_found=True, now=NOW) == WorkflowState.QUALIFYING_FOUND
    assert derive_state(promotion, PromotionFacts(has_qualifying_bet=True), now=NOW) == WorkflowState.QUALIFYING_PLACED
    assert derive_state(
        promotion, PromotionFacts(True, True), now=NOW
    ) == WorkflowState.QUALIFYING_SETTLED
    assert derive_state(
        promotion, PromotionFacts(True, True, tokens_total=1, tokens_received=1), now=NOW
    ) == WorkflowState.REWARD_RECEIVED
    assert derive_state(
        promotion, PromotionFacts(True, True, 1, 1, 1, 0), now=NOW
    ) == WorkflowState.CONVERSION_PLACED
    assert derive_state(
        promotion, PromotionFacts(True, True, 1, 1, 1, 1), now=NOW
    ) == WorkflowState.COMPLETED


def test_derive_state_flags_incomplete_terms():
    assert derive_state(_promotion(qualifying_min_odds=None), PromotionFacts(), now=NOW) == WorkflowState.DRAFT


# --- ranking -----------------------------------------------------------------

def test_rank_qualifying_rejects_insufficient_liquidity():
    promotion = _promotion()
    opportunities = discover_mock_opportunities(10)
    illiquid = [replace(opportunities[0], fully_hedged=False)] + opportunities[1:]
    ranked = rank_qualifying_bets(promotion, illiquid)
    assert ranked
    assert all(rec.fully_hedged for rec in ranked)
    losses = [rec.opportunity.qualifying_loss for rec in ranked]
    assert losses == sorted(losses)


def test_rank_conversion_by_value():
    opportunities = discover_mock_opportunities(20, kind=BetKind.FREE_BET_SNR)
    ranked = rank_conversion_bets(Decimal("20"), opportunities)
    assert ranked
    values = [rec.opportunity.expected_profit for rec in ranked]
    assert values == sorted(values, reverse=True)


def test_stale_flag_is_set():
    opportunities = discover_mock_opportunities(10)
    old = datetime.now(timezone.utc) - timedelta(seconds=400)
    opp = opportunities[0]
    aged_prov = replace(
        opp.provenance,
        sportsbook_quote_timestamp=old,
        exchange_book_timestamp=old,
    )
    stale = [replace(opp, timestamp=old, provenance=aged_prov)]
    [rec] = rank_qualifying_bets(_promotion(), stale)
    assert rec.stale is True


# --- live unavailable --------------------------------------------------------

def test_live_mode_without_configuration_raises(monkeypatch):
    from providers import MockExchangeProvider, MockSportsbookProvider
    from providers.factory import ProviderSet
    import services.promotion_workflow as workflow

    monkeypatch.setattr(
        workflow, "build_providers",
        lambda *a, **k: ProviderSet(MockSportsbookProvider(), MockExchangeProvider(), live=False, exchange_live=False),
    )
    with pytest.raises(workflow.LiveDataUnavailable):
        workflow.find_qualifying_bets(_promotion(), "LIVE")


# --- end-to-end (fixtures) ---------------------------------------------------

def test_services_end_to_end(tmp_path):
    db = tmp_path / "m23.db"
    init_db(db)

    terms = parse_promotion(EXAMPLE)
    promotion_id = save_promotion(
        terms, "Book A", "offer", jurisdiction="Ontario", terms_text=EXAMPLE, db_path=db
    )
    set_promotion_eligibility(promotion_id, EligibilityStatus.ELIGIBLE.value, db_path=db)
    from database import get_promotion
    from promotions import promotion_from_row

    stored = promotion_from_row(get_promotion(promotion_id, db_path=db))
    assert sync_workflow(promotion_id, db_path=db) in ("QUALIFYING_FOUND", "DRAFT")

    # Qualifying.
    qualifying = [replace(o, fully_hedged=True) for o in discover_mock_opportunities(10)]
    ranked = rank_qualifying_bets(stored, qualifying)
    bet_id = record_qualifying_placed(promotion_id, ranked[0].opportunity, db_path=db)
    assert len(list_promotion_actions(promotion_id, db_path=db)) == 1
    assert list_reservations(db_path=db)  # capital reserved

    confirm_qualifying_settlement(
        bet_id, "BACK_WON", db_path=db
    )
    assert sync_workflow(promotion_id, db_path=db) in ("QUALIFYING_SETTLED", "REWARD_RECEIVED")

    # Reward.
    token_id = create_reward_token(
        RewardToken(promotion_id=promotion_id, token_key="t1", face_value=Decimal("20")), db_path=db
    )
    confirm_reward_received(promotion_id, [token_id], db_path=db)
    assert sync_workflow(promotion_id, db_path=db) == "REWARD_RECEIVED"

    # Conversion.
    conversion_pool = [replace(o, fully_hedged=True) for o in discover_mock_opportunities(20, kind=BetKind.FREE_BET_SNR)]
    conversion = rank_conversion_bets(Decimal("20"), conversion_pool)[0]
    conversion_bet_id = record_conversion_placed(promotion_id, token_id, conversion.opportunity, db_path=db)
    assert sync_workflow(promotion_id, db_path=db) == "CONVERSION_PLACED"

    confirm_qualifying_settlement(conversion_bet_id, "LAY_WON", db_path=db)
    assert sync_workflow(promotion_id, db_path=db) == "COMPLETED"

    net = calculate_net_profit(promotion_id, db_path=db)
    assert net["realized_net"] == net["qualifying_loss"] + net["realized_conversion"]
    # Only settlement records count; underlying wagers preserved.
    assert len(list_bets(db_path=db)) == 2
