import pytest

from database import (
    add_promotion_reward,
    add_promotion_source,
    add_promotion_terms,
    create_promotion,
    get_connection,
    get_promotion,
    init_db,
    link_promotion_match,
    list_promotions,
    record_promotion_action,
    set_promotion_eligibility,
    update_promotion_status,
)
from promotions import EligibilityStatus, Promotion, PromotionStatus, promotion_from_row

PROMO_TABLES = {
    "promotions",
    "promotion_sources",
    "promotion_terms",
    "promotion_eligibility",
    "promotion_actions",
    "promotion_rewards",
    "promotion_matches",
}


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "promos.db"
    init_db(path)
    return path


def sample_promotion():
    return Promotion(
        sportsbook="Book A",
        name="$10 -> 2x$25",
        jurisdiction="Ontario",
        offer_type="BET_GET",
        new_customer_only=True,
        qualifying_stake=10,
        qualifying_min_odds="1.40",
        reward_amount=50,
        reward_type="FREE_BET_SNR",
        reward_count=2,
        stake_returned=False,
        reward_expiry_days=7,
        status=PromotionStatus.VERIFIED.value,
        confidence=1.0,
    )


def test_promotion_tables_created(db_path):
    with get_connection(db_path) as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert PROMO_TABLES <= names


def test_create_and_read_promotion(db_path):
    promotion_id = create_promotion(sample_promotion(), db_path=db_path)
    row = get_promotion(promotion_id, db_path=db_path)
    assert row["sportsbook"] == "Book A"
    assert row["reward_count"] == 2
    assert row["stake_returned"] == 0
    assert row["new_customer_only"] == 1

    promotion = promotion_from_row(row)
    assert promotion.qualifying_stake == 10
    assert promotion.qualifying_min_odds == __import__("decimal").Decimal("1.40")
    assert promotion.reward_token_amount == 25
    assert promotion.stake_returned is False


def test_status_eligibility_and_history(db_path):
    promotion_id = create_promotion(sample_promotion(), db_path=db_path)
    update_promotion_status(promotion_id, PromotionStatus.ACTIVE.value, db_path=db_path)
    set_promotion_eligibility(
        promotion_id, EligibilityStatus.ELIGIBLE.value, note="confirmed", db_path=db_path
    )
    row = get_promotion(promotion_id, db_path=db_path)
    assert row["status"] == PromotionStatus.ACTIVE.value
    assert row["eligibility"] == EligibilityStatus.ELIGIBLE.value
    with get_connection(db_path) as connection:
        history = connection.execute(
            "SELECT COUNT(*) FROM promotion_eligibility WHERE promotion_id=?", (promotion_id,)
        ).fetchone()[0]
    assert history == 1


def test_sources_terms_actions_rewards_matches(db_path):
    promotion_id = create_promotion(sample_promotion(), db_path=db_path)
    assert add_promotion_source(promotion_id, text="terms", db_path=db_path)
    assert add_promotion_terms(promotion_id, source="manual", structured="{}", db_path=db_path)
    assert record_promotion_action(promotion_id, "qualifying_placed", bet_id=1, db_path=db_path)
    assert add_promotion_reward(promotion_id, 50, "FREE_BET_SNR", "received", db_path=db_path)
    assert link_promotion_match(promotion_id, event_id="e1", selection="Team", db_path=db_path)
    with get_connection(db_path) as connection:
        for table in ("promotion_sources", "promotion_terms", "promotion_actions",
                      "promotion_rewards", "promotion_matches"):
            count = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE promotion_id=?", (promotion_id,)
            ).fetchone()[0]
            assert count == 1


def test_list_promotions(db_path):
    create_promotion(sample_promotion(), db_path=db_path)
    create_promotion(sample_promotion(), db_path=db_path)
    assert len(list_promotions(db_path)) == 2
