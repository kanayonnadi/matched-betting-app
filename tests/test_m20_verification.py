from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from database import (
    add_promotion_source,
    create_promotion,
    create_reward_token,
    get_connection,
    get_promotion,
    init_db,
    list_lifecycle_history,
    list_promotion_sources,
    list_reward_tokens,
    set_promotion_lifecycle,
    update_reward_token,
)
from promotions import (
    EligibilityStatus,
    Promotion,
    PromotionStatus,
    RewardToken,
    RewardTokenStatus,
    advance_lifecycle,
    effective_status,
    is_actionable,
    is_terminal,
    is_terms_verified,
    outstanding_face_value,
    promotion_from_row,
    realized_value,
    terms_hash,
    validate_ontario,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def ontario_promotion(**overrides):
    base = dict(
        id=1,
        sportsbook="Book A",
        name="$10 -> 2x$25",
        jurisdiction="Ontario",
        offer_type="BET_GET",
        new_customer_only=True,
        qualifying_stake=Decimal("10"),
        qualifying_min_odds=Decimal("1.4"),
        reward_amount=Decimal("50"),
        reward_type="FREE_BET_SNR",
        reward_count=2,
        stake_returned=False,
        status=PromotionStatus.TERMS_VERIFIED.value,
        eligibility=EligibilityStatus.ELIGIBLE.value,
        confidence=1.0,
    )
    base.update(overrides)
    return Promotion(**base)


# --- lifecycle ---------------------------------------------------------------

def test_lifecycle_valid_and_invalid_transitions():
    assert advance_lifecycle(PromotionStatus.DISCOVERED.value, PromotionStatus.TERMS_RETRIEVED.value) == "TERMS_RETRIEVED"
    with pytest.raises(ValueError):
        advance_lifecycle(PromotionStatus.DISCOVERED.value, PromotionStatus.COMPLETED.value)
    with pytest.raises(ValueError):
        advance_lifecycle(PromotionStatus.DISCOVERED.value, "BOGUS")


def test_lifecycle_helpers():
    assert is_terms_verified(PromotionStatus.TERMS_VERIFIED.value)
    assert is_terms_verified(PromotionStatus.ELIGIBILITY_CONFIRMED.value)
    assert not is_terms_verified(PromotionStatus.DISCOVERED.value)
    assert is_actionable(PromotionStatus.ELIGIBILITY_CONFIRMED.value)
    assert not is_actionable(PromotionStatus.TERMS_VERIFIED.value)
    assert is_terminal(PromotionStatus.COMPLETED.value)


# --- Ontario verification ----------------------------------------------------

def test_ontario_promotion_is_actionable():
    report = validate_ontario(ontario_promotion(), now=NOW)
    assert report.ontario_ok is True
    assert report.terms_verified is True
    assert report.actionable is True


def test_missing_critical_terms_block_actionable():
    report = validate_ontario(ontario_promotion(qualifying_min_odds=None), now=NOW)
    assert report.actionable is False
    assert "qualifying_min_odds" in report.missing_terms


def test_non_ontario_is_not_actionable():
    report = validate_ontario(ontario_promotion(jurisdiction="United Kingdom"), now=NOW)
    assert report.ontario_ok is False
    assert report.actionable is False


def test_expired_promotion_not_actionable():
    report = validate_ontario(ontario_promotion(expires_at=NOW - timedelta(days=1)), now=NOW)
    assert report.actionable is False


def test_account_eligibility_required_when_unknown():
    report = validate_ontario(
        ontario_promotion(eligibility=EligibilityStatus.UNKNOWN.value), now=NOW
    )
    assert report.account_eligibility_required is True
    assert report.actionable is False


def test_sr_vs_snr_requires_stake_return_rule():
    report = validate_ontario(ontario_promotion(stake_returned=None), now=NOW)
    assert "stake_returned" in report.missing_terms
    assert report.actionable is False


def test_terms_hash_changes_with_text():
    assert terms_hash("terms v1") != terms_hash("terms v2")
    assert terms_hash("same") == terms_hash("same")


# --- reward tokens -----------------------------------------------------------

def test_reward_token_outstanding_and_realized():
    received = RewardToken(face_value=Decimal("25"), status=RewardTokenStatus.RECEIVED.value,
                           expires_at=NOW + timedelta(days=2))
    expired = RewardToken(face_value=Decimal("25"), status=RewardTokenStatus.RECEIVED.value,
                          expires_at=NOW - timedelta(days=1))
    used = RewardToken(face_value=Decimal("25"), status=RewardTokenStatus.USED.value,
                       linked_conversion_bet_id=5)
    assert outstanding_face_value([received, expired, used], now=NOW) == Decimal("25")
    settled = {5: {"profit_if_back": 18, "profit_if_lay": 18, "status": "Won at exchange"}}
    pending = {5: {"profit_if_back": 18, "profit_if_lay": 18, "status": "Open"}}
    assert realized_value([received, expired, used], settled) == Decimal("18")
    # placed but not settled => not realized
    assert realized_value([used], pending) == Decimal("0")


def test_reward_token_expiry_is_computed_not_assumed():
    token = RewardToken(status=RewardTokenStatus.RECEIVED.value, expires_at=NOW - timedelta(seconds=1))
    assert effective_status(token, now=NOW) == RewardTokenStatus.EXPIRED.value


# --- repository --------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "m20.db"
    init_db(path)
    return path


def test_verification_columns_exist(db_path):
    with get_connection(db_path) as connection:
        promotion_columns = {row[1] for row in connection.execute("PRAGMA table_info(promotions)")}
        source_columns = {row[1] for row in connection.execute("PRAGMA table_info(promotion_sources)")}
    assert {"lifecycle_status", "official_source_url", "terms_verified_at"} <= promotion_columns
    assert {"retrieved_at", "source_type", "version_hash"} <= source_columns


def test_create_promotion_roundtrip_with_verification_fields(db_path):
    promotion = ontario_promotion()
    promotion = promotion.__class__(
        **{**promotion.__dict__,
           "official_source_url": "https://book.example/promo",
           "lifecycle_status": PromotionStatus.TERMS_VERIFIED.value}
    )
    promotion_id = create_promotion(promotion, db_path=db_path)
    row = get_promotion(promotion_id, db_path=db_path)
    mapped = promotion_from_row(row)
    assert mapped.official_source_url == "https://book.example/promo"
    assert mapped.lifecycle_status == PromotionStatus.TERMS_VERIFIED.value


def test_lifecycle_history_preserved(db_path):
    promotion_id = create_promotion(ontario_promotion(), db_path=db_path)
    set_promotion_lifecycle(promotion_id, PromotionStatus.TERMS_RETRIEVED.value, db_path=db_path)
    set_promotion_lifecycle(promotion_id, PromotionStatus.TERMS_VERIFIED.value, db_path=db_path)
    history = list_lifecycle_history(promotion_id, db_path=db_path)
    assert len(history) == 2
    assert history[0]["new_status"] == PromotionStatus.TERMS_RETRIEVED.value


def test_source_versions_retained(db_path):
    promotion_id = create_promotion(ontario_promotion(), db_path=db_path)
    add_promotion_source(promotion_id, url="u1", text="v1", source_type="OFFICIAL",
                         jurisdiction="Ontario", version_hash=terms_hash("v1"),
                         retrieved_at=NOW, db_path=db_path)
    add_promotion_source(promotion_id, url="u1", text="v2", source_type="OFFICIAL",
                         jurisdiction="Ontario", version_hash=terms_hash("v2"),
                         retrieved_at=NOW, db_path=db_path)
    sources = list_promotion_sources(promotion_id, db_path=db_path)
    assert len(sources) == 2
    assert sources[0]["version_hash"] != sources[1]["version_hash"]


def test_duplicate_reward_tokens_are_idempotent(db_path):
    promotion_id = create_promotion(ontario_promotion(), db_path=db_path)
    token = RewardToken(promotion_id=promotion_id, token_key="tok-1", face_value=Decimal("25"))
    first = create_reward_token(token, db_path=db_path)
    second = create_reward_token(token, db_path=db_path)
    assert first == second
    assert len(list_reward_tokens(promotion_id, db_path=db_path)) == 1


def test_reward_token_status_update(db_path):
    promotion_id = create_promotion(ontario_promotion(), db_path=db_path)
    token_id = create_reward_token(
        RewardToken(promotion_id=promotion_id, token_key="t", face_value=Decimal("25")),
        db_path=db_path,
    )
    update_reward_token(token_id, status=RewardTokenStatus.RECEIVED.value, db_path=db_path)
    update_reward_token(
        token_id, status=RewardTokenStatus.USED.value, realized_value=Decimal("18"), db_path=db_path
    )
    row = list_reward_tokens(promotion_id, db_path=db_path)[0]
    assert row["status"] == RewardTokenStatus.USED.value
    assert row["realized_value"] == 18.0
