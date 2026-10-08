from decimal import Decimal

import pytest

from database import (
    add_bankroll_entry,
    init_db,
    list_bankroll,
    list_reservations,
    record_settlement,
)
from risk import (
    EXCHANGE_COLLATERAL,
    SPORTSBOOK_STAKE,
    available_capital,
    can_fund,
    compute_exposure,
    release,
    reserve,
    stress_suite,
)
from settlement import ActualSettlement


def test_full_hedge_is_low_risk():
    exposure = compute_exposure(100, "1.60", "160", [(Decimal("0.62"), Decimal("160"))], "1")
    assert exposure.status == "HEDGED"
    assert exposure.risk == "LOW"
    assert exposure.unhedged_contracts == Decimal("0")
    assert exposure.worst_case_pnl == Decimal("-0.80")


def test_partial_hedge_exposure_computed():
    exposure = compute_exposure(100, "1.60", "160", [(Decimal("0.62"), Decimal("64"))], "1")
    assert exposure.status == "INCOMPLETE"
    assert exposure.risk == "HIGH"
    assert exposure.unhedged_contracts == Decimal("96")
    assert exposure.unhedged_back_stake == Decimal("60.00")
    # Worst case is the back losing with only a partial hedge.
    assert exposure.worst_case_pnl == Decimal("-60.32")
    assert exposure.additional_collateral_required > 0


def test_no_fill_is_no_hedge():
    exposure = compute_exposure(100, "1.60", "160", [], "1")
    assert exposure.status == "NO_HEDGE"
    assert exposure.worst_case_pnl == Decimal("-100.00")


def test_stress_suite_all_pass():
    results = stress_suite()
    assert results
    failures = [r.name for r in results if not r.passed]
    assert failures == []


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "risk.db"
    init_db(path)
    return path


def test_reservations_prevent_double_allocation(db_path):
    add_bankroll_entry("Exchange", 300, db_path=db_path)
    reserve("Exchange", EXCHANGE_COLLATERAL, Decimal("250"), db_path=db_path)

    state = available_capital(
        list_bankroll(db_path), list_reservations(status="RESERVED", db_path=db_path)
    )
    assert state.available["Exchange"] == Decimal("50")
    assert not can_fund("Exchange", Decimal("250"), state)

    reservation_id = list_reservations(status="RESERVED", db_path=db_path)[0]["id"]
    release(reservation_id, db_path=db_path)
    state_after = available_capital(
        list_bankroll(db_path), list_reservations(status="RESERVED", db_path=db_path)
    )
    assert state_after.available["Exchange"] == Decimal("300")


def test_account_capital_is_separate(db_path):
    add_bankroll_entry("Book A", 200, db_path=db_path)
    add_bankroll_entry("Exchange", 50, db_path=db_path)
    reserve("Book A", SPORTSBOOK_STAKE, Decimal("100"), db_path=db_path)
    state = available_capital(
        list_bankroll(db_path), list_reservations(status="RESERVED", db_path=db_path)
    )
    # Exchange collateral cannot be covered by sportsbook capital.
    assert not can_fund("Exchange", Decimal("100"), state)
    assert can_fund("Book A", Decimal("100"), state)


def test_duplicate_settlement_records_are_idempotent(db_path):
    actual = ActualSettlement(bet_id=1, outcome="BACK_WON", settlement_key="dup")
    assert record_settlement(actual, db_path=db_path) is True
    assert record_settlement(actual, db_path=db_path) is False
    assert len(list_reservations(db_path=db_path)) == 0  # unrelated table unaffected
