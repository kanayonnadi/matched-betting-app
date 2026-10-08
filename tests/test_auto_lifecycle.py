from datetime import datetime, timedelta, timezone
from decimal import Decimal

from database import (
    create_promotion,
    create_reward_token,
    get_promotion,
    init_db,
    insert_bet,
    record_promotion_action,
    set_promotion_eligibility,
    update_bet_status,
    update_reward_token,
)
from promotions import (
    EligibilityStatus,
    Promotion,
    PromotionFacts,
    PromotionStatus,
    determine_status,
    path_to,
    sync_promotion_lifecycle,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def promo(**overrides):
    base = dict(
        id=1, sportsbook="Book A", name="offer", jurisdiction="Ontario",
        offer_type="BET_GET", new_customer_only=True,
        qualifying_stake=Decimal("10"), qualifying_min_odds=Decimal("1.4"),
        reward_amount=Decimal("50"), reward_type="FREE_BET_SNR", reward_count=2,
        stake_returned=False, status=PromotionStatus.DISCOVERED.value,
        lifecycle_status=PromotionStatus.DISCOVERED.value,
    )
    base.update(overrides)
    return Promotion(**base)


def test_determine_status_progression():
    assert determine_status(promo(qualifying_stake=None), PromotionFacts(), now=NOW) == "NEEDS_REVIEW"
    assert determine_status(promo(), PromotionFacts(), now=NOW) == "TERMS_VERIFIED"
    eligible = promo(eligibility=EligibilityStatus.ELIGIBLE.value)
    assert determine_status(eligible, PromotionFacts(), now=NOW) == "ELIGIBILITY_CONFIRMED"
    assert determine_status(
        eligible, PromotionFacts(has_qualifying_bet=True), now=NOW
    ) == "QUALIFICATION_PENDING"
    settled = PromotionFacts(has_qualifying_bet=True, qualifying_settled=True)
    assert determine_status(eligible, settled, now=NOW) == "REWARD_PENDING"
    assert determine_status(
        eligible, PromotionFacts(True, True, tokens_total=2, tokens_received=2), now=NOW
    ) == "REWARD_RECEIVED"
    assert determine_status(
        eligible,
        PromotionFacts(True, True, tokens_total=2, tokens_received=2, tokens_used=1, used_conversions_settled=0),
        now=NOW,
    ) == "REWARD_USED"
    assert determine_status(
        eligible,
        PromotionFacts(True, True, tokens_total=2, tokens_received=2, tokens_used=1, used_conversions_settled=1),
        now=NOW,
    ) == "COMPLETED"


def test_determine_exception_states():
    assert determine_status(promo(), PromotionFacts(), now=NOW) == "TERMS_VERIFIED"
    assert determine_status(
        promo(eligibility=EligibilityStatus.ALREADY_USED.value), PromotionFacts(), now=NOW
    ) == "INELIGIBLE"
    assert determine_status(
        promo(expires_at=NOW - timedelta(days=1)), PromotionFacts(), now=NOW
    ) == "EXPIRED"


def test_path_to_is_valid_and_multi_step():
    assert path_to(PromotionStatus.DISCOVERED.value, PromotionStatus.ELIGIBILITY_CONFIRMED.value) == [
        "TERMS_RETRIEVED", "TERMS_VERIFIED", "ELIGIBILITY_CONFIRMED"
    ]
    assert path_to(PromotionStatus.DISCOVERED.value, "COMPLETED")
    # no route -> empty
    assert path_to("COMPLETED", "TERMS_RETRIEVED") == []


def test_sync_promotion_lifecycle_end_to_end(tmp_path):
    db = tmp_path / "auto.db"
    init_db(db)
    promotion_id = create_promotion(promo(), db_path=db)

    # Terms complete but eligibility unknown -> TERMS_VERIFIED.
    assert sync_promotion_lifecycle(promotion_id, db_path=db) == "TERMS_VERIFIED"

    set_promotion_eligibility(promotion_id, EligibilityStatus.ELIGIBLE.value, db_path=db)
    assert sync_promotion_lifecycle(promotion_id, db_path=db) == "ELIGIBILITY_CONFIRMED"

    bet_id = insert_bet(event="E", source="promotion", db_path=db)
    record_promotion_action(promotion_id, "qualifying_placed", bet_id=bet_id, db_path=db)
    assert sync_promotion_lifecycle(promotion_id, db_path=db) == "QUALIFICATION_PENDING"

    update_bet_status(bet_id, "Won at bookmaker", db_path=db)
    assert sync_promotion_lifecycle(promotion_id, db_path=db) == "REWARD_PENDING"

    token_id = create_reward_token(
        __import__("promotions").RewardToken(
            promotion_id=promotion_id, token_key="t1", face_value=Decimal("25")
        ),
        db_path=db,
    )
    update_reward_token(token_id, status="RECEIVED", db_path=db)
    assert sync_promotion_lifecycle(promotion_id, db_path=db) == "REWARD_RECEIVED"

    assert get_promotion(promotion_id, db_path=db)["lifecycle_status"] == "REWARD_RECEIVED"
