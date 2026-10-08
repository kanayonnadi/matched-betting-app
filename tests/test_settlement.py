from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from database import (
    get_connection,
    get_prediction_snapshot,
    init_db,
    list_reconciliations,
    list_settlements,
    record_settlement,
    save_prediction_snapshot,
    save_reconciliation,
)
from liquidity import build_order_book
from models import ExchangeOdds, NormalizedOdds, Side
from opportunities import build_opportunity
from settlement import (
    ActualSettlement,
    ReconciliationStatus,
    SettledOutcome,
    prediction_from_opportunity,
    reconcile,
    stx_fee_from_fills,
    validate_prediction_fee,
)

START = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
BIDS = [
    {"price": "0.6200", "quantity": "461.00"},
    {"price": "0.6100", "quantity": "399.00"},
    {"price": "0.5900", "quantity": "399.00"},
]


def make_prediction(bet_id=1):
    book = NormalizedOdds(
        provider="theoddsapi:betano_ca_on", event_id="b1", sport="ice_hockey", league="NHL",
        home_team="Colorado Avalanche", away_team="Winnipeg Jets", start_time=START,
        market="moneyline", selection="Colorado Avalanche", side=Side.BACK,
        decimal_odds=Decimal("1.60"), timestamp=NOW,
    )
    exchange = ExchangeOdds(
        provider="stx", event_id="s1", sport="ice_hockey", league="NHL",
        home_team="Colorado Avalanche", away_team="Winnipeg Jets", start_time=START,
        market="moneyline", selection="Colorado Avalanche", side=Side.LAY,
        decimal_odds=Decimal("1.6129"), timestamp=NOW, available_size=Decimal("285.82"),
        commission=Decimal("0"), fee_model="per_wager", fee_factor=Decimal("0.10"),
        max_price=Decimal("1"),
    )
    order_book = build_order_book(BIDS, "1", NOW, completeness="COMPLETE")
    opportunity = build_opportunity(book, exchange, 300, order_book=order_book, now=NOW)
    return prediction_from_opportunity(opportunity, bet_id)


# --- independent fee validation ----------------------------------------------

def test_validate_fee_matches_independent_formula():
    prediction = make_prediction()
    validation = validate_prediction_fee(prediction)
    # ceil(0.10 * (461*0.62*0.38 + 19*0.61*0.39)) = ceil(11.3136) = 11.32
    assert validation.computed_fee == Decimal("11.32")
    assert validation.declared_fee == Decimal("11.32")
    assert validation.matches is True


def test_validate_fee_detects_mismatch():
    prediction = replace(make_prediction(), predicted_exchange_fee=Decimal("11.00"))
    validation = validate_prediction_fee(prediction)
    assert validation.computed_fee == Decimal("11.32")
    assert validation.matches is False
    assert validation.difference == Decimal("0.32")


def test_stx_fee_rounds_up_to_cent_once():
    assert stx_fee_from_fills([(Decimal("0.6"), Decimal("50"))], Decimal("1"), Decimal("0.10")) == Decimal("1.20")
    assert stx_fee_from_fills([], Decimal("1"), Decimal("0.10")) is None


# --- reconciliation ----------------------------------------------------------

def test_reconcile_back_won_exact():
    prediction = make_prediction()
    actual = ActualSettlement(
        bet_id=1, outcome=SettledOutcome.BACK_WON.value,
        actual_lay_contracts=Decimal("480"), actual_liability=Decimal("182.59"),
        actual_exchange_fee=Decimal("11.32"), actual_total_pnl=Decimal("-13.91"),
        actual_sportsbook_settlement=Decimal("80"), actual_exchange_settlement=Decimal("-93.91"),
        settled_at=NOW,
    )
    result = reconcile(prediction, actual)
    assert result.status == ReconciliationStatus.EXACT_MATCH.value
    assert result.pnl_difference == Decimal("0.00") or result.pnl_difference == Decimal("0")
    assert result.predicted_pnl == Decimal("-13.91")


def test_reconcile_lay_won_exact():
    prediction = make_prediction()
    actual = ActualSettlement(
        bet_id=1, outcome=SettledOutcome.LAY_WON.value,
        actual_lay_contracts=Decimal("480"), actual_liability=Decimal("182.59"),
        actual_exchange_fee=Decimal("11.32"), actual_total_pnl=Decimal("-13.91"),
        actual_sportsbook_settlement=Decimal("-300"), actual_exchange_settlement=Decimal("286.09"),
        settled_at=NOW,
    )
    result = reconcile(prediction, actual)
    assert result.status == ReconciliationStatus.EXACT_MATCH.value


def test_reconcile_partial_fill_is_discrepancy():
    prediction = make_prediction()
    actual = ActualSettlement(
        bet_id=1, outcome=SettledOutcome.LAY_WON.value,
        actual_lay_contracts=Decimal("200"), actual_liability=Decimal("76.00"),
        actual_exchange_fee=Decimal("4.72"), actual_total_pnl=Decimal("50.00"),
        actual_sportsbook_settlement=Decimal("-300"), actual_exchange_settlement=Decimal("350"),
        settled_at=NOW,
    )
    result = reconcile(prediction, actual)
    assert result.status == ReconciliationStatus.DISCREPANCY.value
    assert any("partial fill" in flag for flag in result.flags)


def test_reconcile_void_is_exact_and_flagged():
    prediction = make_prediction()
    actual = ActualSettlement(
        bet_id=1, outcome=SettledOutcome.VOID.value, actual_total_pnl=Decimal("0"),
        settled_at=NOW,
    )
    result = reconcile(prediction, actual)
    assert result.status == ReconciliationStatus.EXACT_MATCH.value
    assert any("void" in flag for flag in result.flags)


def test_reconcile_missing_actual_is_unverified():
    result = reconcile(make_prediction(), None)
    assert result.status == ReconciliationStatus.UNVERIFIED.value


def test_reconcile_missing_field_is_missing_data():
    prediction = make_prediction()
    actual = ActualSettlement(
        bet_id=1, outcome=SettledOutcome.BACK_WON.value,
        actual_lay_contracts=Decimal("480"), actual_liability=Decimal("182.59"),
        actual_exchange_fee=None, actual_total_pnl=Decimal("-13.91"), settled_at=NOW,
    )
    result = reconcile(prediction, actual)
    assert result.status == ReconciliationStatus.MISSING_DATA.value


def test_reconcile_fee_discrepancy():
    prediction = make_prediction()
    actual = ActualSettlement(
        bet_id=1, outcome=SettledOutcome.BACK_WON.value,
        actual_lay_contracts=Decimal("480"), actual_liability=Decimal("182.59"),
        actual_exchange_fee=Decimal("12.50"), actual_total_pnl=Decimal("-15.09"),
        settled_at=NOW,
    )
    result = reconcile(prediction, actual)
    assert result.status == ReconciliationStatus.DISCREPANCY.value
    assert result.fee_difference == Decimal("1.18")


# --- persistence -------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "settle.db"
    init_db(path)
    return path


def test_settlement_tables_created(db_path):
    with get_connection(db_path) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"prediction_snapshots", "settlements", "reconciliations"} <= names


def test_prediction_snapshot_is_immutable(db_path):
    prediction = make_prediction(bet_id=1)
    save_prediction_snapshot(prediction, db_path=db_path)
    modified = replace(prediction, predicted_back_stake=Decimal("999"))
    save_prediction_snapshot(modified, db_path=db_path)
    row = get_prediction_snapshot(1, db_path=db_path)
    assert row["predicted_back_stake"] == float(prediction.predicted_back_stake)


def test_settlement_recording_is_idempotent(db_path):
    actual = ActualSettlement(bet_id=1, outcome="BACK_WON", settlement_key="k1", settled_at=NOW)
    assert record_settlement(actual, db_path=db_path) is True
    assert record_settlement(actual, db_path=db_path) is False
    assert len(list_settlements(1, db_path=db_path)) == 1


def test_save_reconciliation_once(db_path):
    prediction = make_prediction(bet_id=1)
    actual = ActualSettlement(
        bet_id=1, outcome=SettledOutcome.BACK_WON.value, actual_lay_contracts=Decimal("480"),
        actual_liability=Decimal("182.59"), actual_exchange_fee=Decimal("11.32"),
        actual_total_pnl=Decimal("-13.91"), settled_at=NOW,
    )
    result = reconcile(prediction, actual)
    first = save_reconciliation(result, prediction_id=1, settlement_id=1, db_path=db_path)
    second = save_reconciliation(result, prediction_id=1, settlement_id=1, db_path=db_path)
    assert first == 1
    assert second == 0
    assert len(list_reconciliations(db_path)) == 1
