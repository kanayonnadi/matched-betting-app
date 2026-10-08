from datetime import datetime, timezone
from decimal import Decimal

import pytest

from calculators import calculate_qualifying_with_execution
from database import (
    create_promotion,
    create_reward_token,
    get_connection,
    init_db,
    list_reward_tokens,
    record_settlement,
)
from liquidity import build_order_book, simulate_execution
from promotions import Promotion, RewardToken, RewardTokenStatus, realized_value
from settlement import ActualSettlement, SettledOutcome

START = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
BIDS = [
    {"price": "0.6200", "quantity": "461.00"},
    {"price": "0.6100", "quantity": "399.00"},
    {"price": "0.5900", "quantity": "399.00"},
]


def test_300_example_derived_independently():
    book = build_order_book(BIDS, "1", NOW, completeness="COMPLETE")
    execution = simulate_execution(book, Decimal("480"), Decimal("0.10"), now=NOW)
    result = calculate_qualifying_with_execution(300, "1.60", execution)

    # Independent first-principles arithmetic.
    assert Decimal("300") * Decimal("1.60") / Decimal("1") == Decimal("480")
    assert Decimal("461") * Decimal("0.62") + Decimal("19") * Decimal("0.61") == Decimal("297.41")
    assert Decimal("480") - Decimal("297.41") == Decimal("182.59")
    fee_raw = Decimal("0.10") * (Decimal("461") * Decimal("0.62") * Decimal("0.38")
                                 + Decimal("19") * Decimal("0.61") * Decimal("0.39"))
    assert fee_raw.quantize(Decimal("0.01")) == Decimal("11.31")  # 11.3136 -> 11.31 (not yet rounded up)
    assert execution.estimated_fee == Decimal("11.32")  # ceil to cent

    assert result.lay_stake == Decimal("297.41")
    assert result.liability == Decimal("182.59")
    assert result.profit_if_back == Decimal("-13.91")
    assert result.profit_if_lay == Decimal("-13.91")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "m20i.db"
    init_db(path)
    return path


def test_settlement_records_source(db_path):
    record_settlement(
        ActualSettlement(bet_id=1, outcome=SettledOutcome.BACK_WON.value, source="manual",
                         settlement_key="m1", settled_at=NOW),
        db_path=db_path,
    )
    record_settlement(
        ActualSettlement(bet_id=1, outcome=SettledOutcome.LAY_WON.value, source="provider",
                         settlement_key="p1", settled_at=NOW),
        db_path=db_path,
    )
    with get_connection(db_path) as connection:
        rows = connection.execute("SELECT source FROM settlements ORDER BY id").fetchall()
    assert {row["source"] for row in rows} == {"manual", "provider"}


def test_reward_issuance_is_not_assumed_from_settlement(db_path):
    promotion_id = create_promotion(
        Promotion(id=1, sportsbook="Book A", name="offer",
                  status="QUALIFICATION_PENDING"), db_path=db_path,
    )
    # A qualifying bet settlement alone must not create a reward token.
    record_settlement(
        ActualSettlement(bet_id=1, outcome=SettledOutcome.BACK_WON.value, source="manual",
                         settlement_key="s1", settled_at=NOW),
        db_path=db_path,
    )
    assert list_reward_tokens(promotion_id, db_path=db_path) == []


def test_realized_reward_only_after_conversion_settles():
    received = RewardToken(face_value=Decimal("25"), status=RewardTokenStatus.RECEIVED.value)
    assert realized_value([received], {}) == Decimal("0")

    # Placed (USED) but conversion not settled => not realized.
    used = RewardToken(face_value=Decimal("25"), status=RewardTokenStatus.USED.value,
                       linked_conversion_bet_id=7)
    pending = {7: {"profit_if_back": 18.5, "profit_if_lay": 18.5, "status": "Open"}}
    settled = {7: {"profit_if_back": 18.5, "profit_if_lay": 18.5, "status": "Won at exchange"}}
    assert realized_value([used], pending) == Decimal("0")
    assert realized_value([used], settled) == Decimal("18.5")
