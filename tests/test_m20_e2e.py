from datetime import datetime, timezone
from decimal import Decimal

from analytics import expected_profit
from database import (
    add_promotion_source,
    create_promotion,
    create_reward_token,
    get_connection,
    get_promotion,
    init_db,
    insert_bet,
    list_bets,
    list_lifecycle_history,
    list_promotion_actions,
    list_reward_tokens,
    record_promotion_action,
    save_prediction_snapshot,
    set_promotion_eligibility,
    set_promotion_lifecycle,
    update_bet_status,
    update_reward_token,
)
from opportunities import BetKind, discover_mock_opportunities
from promotions import (
    EligibilityStatus,
    Promotion,
    PromotionStatus,
    RewardToken,
    RewardTokenStatus,
    advance_lifecycle,
    best_conversion,
    best_qualifying,
    outstanding_face_value,
    promotion_from_row,
    realized_value,
    terms_hash,
    validate_ontario,
)
from settlement import (
    ActualSettlement,
    SettledOutcome,
    prediction_from_opportunity,
    reconcile,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def _advance(db_path, promotion_id, current, target):
    advance_lifecycle(current, target)
    set_promotion_lifecycle(promotion_id, target, db_path=db_path)
    return target


def test_full_promotion_lifecycle_end_to_end(tmp_path):
    db = tmp_path / "e2e.db"
    init_db(db)

    # 1. Manual promotion entry (Ontario).
    promotion = Promotion(
        sportsbook="Book A", name="$10 -> 2x$25", jurisdiction="Ontario",
        offer_type="BET_GET", new_customer_only=True,
        qualifying_stake=Decimal("10"), qualifying_min_odds=Decimal("1.4"),
        reward_amount=Decimal("50"), reward_type="FREE_BET_SNR", reward_count=2,
        stake_returned=False, status=PromotionStatus.DISCOVERED.value,
        lifecycle_status=PromotionStatus.DISCOVERED.value, confidence=0.9,
    )
    promotion_id = create_promotion(promotion, db_path=db)
    add_promotion_source(
        promotion_id, url="https://book.example/promo", text="Bet $10 receive $50 in bonus bets",
        source_type="OFFICIAL", jurisdiction="Ontario", version_hash=terms_hash("terms v1"),
        retrieved_at=NOW, db_path=db,
    )

    # 2. Lifecycle: discovery -> terms -> eligibility.
    current = PromotionStatus.DISCOVERED.value
    current = _advance(db, promotion_id, current, PromotionStatus.TERMS_RETRIEVED.value)
    current = _advance(db, promotion_id, current, PromotionStatus.TERMS_VERIFIED.value)
    set_promotion_eligibility(promotion_id, EligibilityStatus.ELIGIBLE.value, note="confirmed", db_path=db)
    current = _advance(db, promotion_id, current, PromotionStatus.ELIGIBILITY_CONFIRMED.value)

    # 3. Ontario verification is actionable.
    stored = promotion_from_row(get_promotion(promotion_id, db_path=db))
    report = validate_ontario(stored, now=NOW)
    assert report.actionable is True

    # 4. Qualifying opportunity from fixtures -> log bet -> link to promotion.
    qualifying_pool = discover_mock_opportunities(10)
    candidate = best_qualifying(stored, qualifying_pool, limit=1)[0]
    opp = candidate.opportunity
    bet_id = insert_bet(
        event=opp.event_label, bookmaker=opp.book_provider, exchange=opp.exchange_provider,
        market=opp.market, selection=opp.selection, sport=opp.sport, bet_type="Qualifying bet",
        back_stake=float(opp.stake), back_odds=float(opp.back_odds), lay_odds=float(opp.lay_odds),
        commission=float(opp.commission * 100), lay_stake=float(opp.lay_stake),
        liability=float(opp.lay_liability), profit_if_back=float(opp.profit_if_back),
        profit_if_lay=float(opp.profit_if_lay), source="promotion", db_path=db,
    )
    save_prediction_snapshot(prediction_from_opportunity(opp, bet_id), db_path=db)
    record_promotion_action(promotion_id, "qualifying_placed", bet_id=bet_id, db_path=db)
    current = _advance(db, promotion_id, current, PromotionStatus.QUALIFICATION_PENDING.value)

    # 5. Settle the qualifying bet and reconcile.
    prediction = prediction_from_opportunity(opp, bet_id)
    actual = ActualSettlement(
        bet_id=bet_id, outcome=SettledOutcome.BACK_WON.value,
        actual_exchange_fee=opp.lay_fee or Decimal("0"), actual_liability=opp.lay_liability,
        actual_total_pnl=opp.profit_if_back, settlement_key="q1", settled_at=NOW,
    )
    result = reconcile(prediction, actual)
    assert result.status in ("EXACT_MATCH", "WITHIN_TOLERANCE")
    update_bet_status(bet_id, "Won at bookmaker", db_path=db)
    current = _advance(db, promotion_id, current, PromotionStatus.QUALIFICATION_SETTLED.value)
    current = _advance(db, promotion_id, current, PromotionStatus.REWARD_PENDING.value)

    # 6. Reward tokens issued (explicitly), then received.
    token_ids = []
    for index in range(2):
        token_ids.append(
            create_reward_token(
                RewardToken(promotion_id=promotion_id, token_key=f"tok-{index}",
                            face_value=Decimal("25"), reward_type="FREE_BET_SNR"),
                db_path=db,
            )
        )
    for token_id in token_ids:
        update_reward_token(token_id, status=RewardTokenStatus.RECEIVED.value, db_path=db)
    current = _advance(db, promotion_id, current, PromotionStatus.REWARD_RECEIVED.value)

    tokens = list_reward_tokens(promotion_id, db_path=db)
    assert len(tokens) == 2
    assert outstanding_face_value([RewardToken(face_value=Decimal("25"),
                                               status="RECEIVED") for _ in tokens]) == Decimal("50")

    # 7. Convert one token via a fixture conversion opportunity.
    conversion_pool = discover_mock_opportunities(25, kind=BetKind.FREE_BET_SNR)
    conversion = best_conversion(Decimal("25"), conversion_pool, limit=1)[0].opportunity
    conversion_bet_id = insert_bet(
        event=conversion.event_label, bookmaker=conversion.book_provider,
        exchange=conversion.exchange_provider, market=conversion.market,
        selection=conversion.selection, sport=conversion.sport,
        bet_type="Free bet (stake not returned)", back_stake=float(conversion.stake),
        back_odds=float(conversion.back_odds), lay_odds=float(conversion.lay_odds),
        commission=float(conversion.commission * 100), lay_stake=float(conversion.lay_stake),
        liability=float(conversion.lay_liability), profit_if_back=float(conversion.profit_if_back),
        profit_if_lay=float(conversion.profit_if_lay), source="promotion", db_path=db,
    )
    save_prediction_snapshot(prediction_from_opportunity(conversion, conversion_bet_id), db_path=db)
    record_promotion_action(promotion_id, "conversion_placed", bet_id=conversion_bet_id, db_path=db)
    update_reward_token(token_ids[0], linked_conversion_bet_id=conversion_bet_id,
                        status=RewardTokenStatus.RECEIVED.value, db_path=db)

    # 8. Settle the conversion; mark token used with realized value.
    update_bet_status(conversion_bet_id, "Won at exchange", db_path=db)
    update_reward_token(
        token_ids[0], status=RewardTokenStatus.USED.value,
        realized_value=conversion.expected_profit, db_path=db,
    )
    current = _advance(db, promotion_id, current, PromotionStatus.REWARD_USED.value)
    current = _advance(db, promotion_id, current, PromotionStatus.COMPLETED.value)

    # 9. Realized profit from settled linked bets (single count).
    bets_by_id = {int(b["id"]): b for b in list_bets(db_path=db)}
    actions = list_promotion_actions(promotion_id, db_path=db)
    qualifying = [bets_by_id[a["bet_id"]] for a in actions if a["action"] == "qualifying_placed"]
    conversions = [bets_by_id[a["bet_id"]] for a in actions if a["action"] == "conversion_placed"]
    realized = sum((expected_profit(b) for b in qualifying + conversions), Decimal("0"))
    # Qualifying is a small loss; the converted $25 token is profit; net is positive.
    assert expected_profit(qualifying[0]) < 0
    assert expected_profit(conversions[0]) > 0
    assert realized > 0

    token_after = [t for t in list_reward_tokens(promotion_id, db_path=db) if t["id"] == token_ids[0]][0]
    assert token_after["status"] == RewardTokenStatus.USED.value

    history = list_lifecycle_history(promotion_id, db_path=db)
    assert history[-1]["new_status"] == PromotionStatus.COMPLETED.value
