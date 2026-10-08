from decimal import Decimal

import pytest

from database import (
    add_bankroll_entry,
    init_db,
    list_bankroll,
    list_reservations,
)
from risk import (
    EXCHANGE_COLLATERAL,
    available_capital,
    compute_exposure,
    release_for_bet,
    reserve_for_bet,
    reservations_for_bet,
    update_exchange_reservation,
)
from settlement import FEE_MODEL_STATUS


def test_partial_hedge_both_outcome_pnls_derived():
    exposure = compute_exposure(
        100, "1.60", "160", [(Decimal("0.62"), Decimal("64"))], "1", fee=Decimal("0")
    )
    # If back wins: 100*(1.60-1) - 64*(1-0.62) = 60 - 24.32 = 35.68
    assert exposure.back_win_pnl == Decimal("35.68")
    # If lay wins:  -100 + 64*0.62 = -60.32
    assert exposure.lay_win_pnl == Decimal("-60.32")
    assert exposure.worst_case_pnl == Decimal("-60.32")
    assert exposure.best_case_pnl == Decimal("35.68")
    assert "back_win_derivation" in exposure.details


def test_partial_hedge_fee_reduces_both_outcomes():
    exposure = compute_exposure(
        100, "1.60", "160", [(Decimal("0.62"), Decimal("64"))], "1", fee=Decimal("2.00")
    )
    assert exposure.back_win_pnl == Decimal("33.68")
    assert exposure.lay_win_pnl == Decimal("-62.32")


def test_fee_model_is_labeled_unverified():
    assert FEE_MODEL_STATUS == "EXTERNALLY_UNVERIFIED"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "m22.db"
    init_db(path)
    return path


def test_reservations_lifecycle_is_integrated(db_path):
    add_bankroll_entry("Book A", 200, db_path=db_path)
    add_bankroll_entry("STX", 300, db_path=db_path)

    # 1. Logging a bet reserves sportsbook stake + exchange collateral.
    reserve_for_bet(1, "Book A", "STX", 100, 60, db_path=db_path)
    state = available_capital(
        list_bankroll(db_path), list_reservations(status="RESERVED", db_path=db_path)
    )
    assert state.available["Book A"] == Decimal("100")
    assert state.available["STX"] == Decimal("240")

    # 2. Partial fill updates the exchange collateral reservation.
    exchange_reservation = [
        r for r in reservations_for_bet(1, db_path=db_path)
        if r["kind"] == EXCHANGE_COLLATERAL
    ][0]
    update_exchange_reservation(exchange_reservation["id"], Decimal("24.32"), db_path=db_path)
    state2 = available_capital(
        list_bankroll(db_path), list_reservations(status="RESERVED", db_path=db_path)
    )
    assert state2.available["STX"] == Decimal("275.68")

    # 3. Settlement releases reservations.
    assert release_for_bet(1, db_path=db_path) == 2
    state3 = available_capital(
        list_bankroll(db_path), list_reservations(status="RESERVED", db_path=db_path)
    )
    assert state3.available["Book A"] == Decimal("200")
    assert state3.available["STX"] == Decimal("300")

    # 4. Duplicate settlement must not release capital twice.
    assert release_for_bet(1, db_path=db_path) == 0
    state4 = available_capital(
        list_bankroll(db_path), list_reservations(status="RESERVED", db_path=db_path)
    )
    assert state4.available == state3.available
