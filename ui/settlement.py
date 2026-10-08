"""Settlement reconciliation page: predicted vs actual."""

from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import streamlit as st

from database import (
    get_prediction_snapshot,
    list_prediction_snapshots,
    list_reconciliations,
    list_settlements,
    record_settlement,
    save_reconciliation,
)
from risk import release_for_bet
from settlement import (
    FEE_MODEL_STATUS,
    ActualSettlement,
    SettledOutcome,
    prediction_from_row,
    reconcile,
    validate_prediction_fee,
)

OUTCOMES = [item.value for item in SettledOutcome]


def _money(value) -> str:
    if value is None:
        return "-"
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return f"${amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def _opt(field) -> Decimal:
    text = str(field).strip()
    if text == "" or text is None:
        return None
    return Decimal(text)


def render():
    st.header("Settlement Reconciliation")
    st.caption(
        "Compare immutable prediction snapshots against actual settlements. "
        "Missing data is never treated as reconciled."
    )

    snapshots = list_prediction_snapshots()
    settlements = list_settlements()
    reconciliations = list_reconciliations()

    metrics = st.columns(4)
    metrics[0].metric("Predictions", len(snapshots))
    metrics[1].metric("Settlements", len(settlements))
    metrics[2].metric(
        "Exact matches",
        sum(1 for r in reconciliations if r["status"] == "EXACT_MATCH"),
    )
    metrics[3].metric(
        "Discrepancies",
        sum(1 for r in reconciliations if r["status"] in ("DISCREPANCY", "MISSING_DATA")),
    )

    if reconciliations:
        st.subheader("Reconciliation history")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Bet": r["bet_id"],
                        "Status": r["status"],
                        "Predicted P&L": _money(r["predicted_pnl"]),
                        "Actual P&L": _money(r["actual_pnl"]),
                        "P&L diff": _money(r["pnl_difference"]),
                        "Fee diff": _money(r["fee_difference"]),
                        "Liability diff": _money(r["liability_difference"]),
                        "Flags": r["flags"],
                    }
                    for r in reconciliations
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No reconciliations yet. Record an actual settlement below.")

    st.divider()
    st.subheader("Record actual settlement (manual)")
    with st.form("settlement_form"):
        row1 = st.columns(3)
        bet_id = row1[0].number_input("Bet ID", min_value=1, step=1)
        outcome = row1[1].selectbox("Settled outcome", OUTCOMES)
        settled_note = row1[2].text_input("Note / external id", key="settle_note")
        row2 = st.columns(3)
        back_stake = row2[0].text_input("Actual back stake", value="")
        back_odds = row2[1].text_input("Actual back odds", value="")
        lay_contracts = row2[2].text_input("Actual lay contracts", value="")
        row3 = st.columns(3)
        liability = row3[0].text_input("Actual liability", value="")
        fee = row3[1].text_input("Actual STX fee", value="")
        total_pnl = row3[2].text_input("Actual total P&L", value="")
        row4 = st.columns(2)
        book_settlement = row4[0].text_input("Sportsbook settlement", value="")
        exchange_settlement = row4[1].text_input("Exchange settlement", value="")
        submitted = st.form_submit_button("Record & reconcile", type="primary")

    if submitted:
        prediction_row = get_prediction_snapshot(bet_id)
        if prediction_row is None:
            st.error("No prediction snapshot for that bet id.")
        else:
            actual = ActualSettlement(
                bet_id=int(bet_id),
                outcome=outcome,
                actual_back_stake=_opt(back_stake),
                actual_back_odds=_opt(back_odds),
                actual_lay_contracts=_opt(lay_contracts),
                actual_liability=_opt(liability),
                actual_exchange_fee=_opt(fee),
                actual_total_pnl=_opt(total_pnl),
                actual_sportsbook_settlement=_opt(book_settlement),
                actual_exchange_settlement=_opt(exchange_settlement),
                settlement_key=settled_note.strip() or None,
            )
            record_settlement(actual)
            result = reconcile(prediction_from_row(prediction_row), actual)
            save_reconciliation(result, prediction_id=prediction_row["id"])
            released = release_for_bet(int(bet_id))
            st.success(
                f"{result.status}: {result.message}"
                + (f" · released {released} reservation(s)" if released else "")
            )
            if result.flags:
                st.warning("; ".join(result.flags))

    if snapshots:
        st.divider()
        st.subheader("Fee-model validation")
        predict_id = st.number_input("Prediction bet ID (fee check)", min_value=1, step=1)
        row = get_prediction_snapshot(predict_id)
        if row is not None:
            validation = validate_prediction_fee(prediction_from_row(row))
            st.write(
                f"Computed fee: `{_money(validation.computed_fee)}` · "
                f"Declared fee: `{_money(validation.declared_fee)}` · "
                f"Difference: `{_money(validation.difference)}` · "
                f"{'MATCH' if validation.matches else 'CHECK'}"
            )
            st.caption(validation.note)
            st.warning(
                f"Fee model status: {FEE_MODEL_STATUS}. The STX fee formula derives from the "
                "operator fee schedule and has not been confirmed against official documentation "
                "or real transactions."
            )
