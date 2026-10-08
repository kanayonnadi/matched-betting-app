"""Risk / stress-testing page (M21)."""

from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import streamlit as st

from database import list_bankroll, list_reservations, set_reservation_status
from risk import (
    EXCHANGE_COLLATERAL,
    SPORTSBOOK_STAKE,
    available_capital,
    compute_exposure,
    release,
    reserve,
    stress_suite,
)


def _money(value) -> str:
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return f"${amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def _partial_hedge_calculator():
    st.subheader("Partial-hedge exposure")
    cols = st.columns(3)
    back_stake = cols[0].number_input("Back stake ($)", min_value=0.0, value=100.0, step=5.0)
    back_odds = cols[1].number_input("Back odds", min_value=1.01, value=1.60, step=0.01)
    requested = cols[2].number_input("Required hedge (contracts)", min_value=0.0, value=160.0, step=1.0)
    cols2 = st.columns(4)
    filled = cols2[0].number_input("Filled (contracts)", min_value=0.0, value=64.0, step=1.0)
    fill_price = cols2[1].number_input("Fill price ($)", min_value=0.01, max_value=1.0, value=0.62, step=0.01)
    max_price = cols2[2].number_input("Max price ($)", min_value=0.01, value=1.0, step=0.01)
    fee = cols2[3].number_input("STX fee ($)", min_value=0.0, value=0.0, step=0.01)

    exposure = compute_exposure(
        Decimal(str(back_stake)), Decimal(str(back_odds)), Decimal(str(requested)),
        [(Decimal(str(fill_price)), Decimal(str(filled)))] if filled else [],
        Decimal(str(max_price)), fee=Decimal(str(fee)),
    )
    metrics = st.columns(3)
    metrics[0].metric("Hedge status", exposure.status)
    metrics[1].metric("Risk", exposure.risk)
    metrics[2].metric("Fill ratio", f"{exposure.fill_ratio * 100:.0f}%")
    metrics2 = st.columns(4)
    metrics2[0].metric("Unhedged contracts", f"{exposure.unhedged_contracts:.2f}")
    metrics2[1].metric("Unhedged back stake", _money(exposure.unhedged_back_stake))
    metrics2[2].metric("P&L if back wins", _money(exposure.back_win_pnl))
    metrics2[3].metric("P&L if lay wins", _money(exposure.lay_win_pnl))
    st.write(
        f"Worst case `{_money(exposure.worst_case_pnl)}` · best case `{_money(exposure.best_case_pnl)}` · "
        f"Lay stake filled `{_money(exposure.lay_stake_filled)}` · "
        f"Liability filled `{_money(exposure.liability_filled)}` · "
        f"Additional collateral to complete `{_money(exposure.additional_collateral_required)}`"
    )
    with st.expander("Independent derivation (outcome-specific)"):
        st.write(f"If back wins: {exposure.details['back_win_derivation']} = {_money(exposure.back_win_pnl)}")
        st.write(f"If lay wins:  {exposure.details['lay_win_derivation']} = {_money(exposure.lay_win_pnl)}")
        st.caption(
            "The STX per-wager fee is charged regardless of outcome and is subtracted from both. "
            "For a partial hedge the unhedged contracts behave like a straight back bet."
        )
    if exposure.status != "HEDGED":
        st.warning(
            "Hedge incomplete — the original qualifying loss does NOT apply. "
            f"Unhedged exposure is {_money(exposure.unhedged_back_stake)}; "
            f"P&L if back wins {_money(exposure.back_win_pnl)}, if lay wins {_money(exposure.lay_win_pnl)}."
        )


def _reservations_panel():
    st.subheader("Bankroll reservations")
    bankroll_rows = list_bankroll()
    reservations = list_reservations()
    state = available_capital(bankroll_rows, [r for r in reservations if r["status"] == "RESERVED"])
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Account": account,
                    "Balance": _money(state.balances.get(account, Decimal("0"))),
                    "Reserved": _money(state.reserved.get(account, Decimal("0"))),
                    "Available": _money(state.available.get(account, Decimal("0"))),
                }
                for account in sorted(set(state.balances) | set(state.reserved))
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        f"Total reserved {_money(state.total_reserved)} · total available {_money(state.total_available)}. "
        "Sportsbook and exchange capital are tracked separately."
    )
    with st.form("reserve_form"):
        cols = st.columns(3)
        account = cols[0].text_input("Account")
        kind = cols[1].selectbox("Kind", [SPORTSBOOK_STAKE, EXCHANGE_COLLATERAL])
        amount = cols[2].number_input("Amount ($)", min_value=0.0, value=0.0, step=5.0)
        submitted = st.form_submit_button("Reserve")
    if submitted and account.strip() and amount > 0:
        reserve(account.strip(), kind, Decimal(str(amount)))
        st.success("Reserved.")
    if reservations:
        release_id = st.number_input("Reservation ID to release", min_value=1, step=1)
        if st.button("Release reservation"):
            release(int(release_id))
            st.success("Released.")


def render():
    st.header("Risk & Stress Testing")
    st.caption(
        "Deterministic execution/bankroll failure scenarios. Missing data is never "
        "assumed to be zero risk. Wagers remain manual."
    )

    st.subheader("Scenario suite")
    results = stress_suite()
    st.dataframe(
        pd.DataFrame(
            [
                {"Scenario": r.name, "Expected": r.expected, "Actual": r.actual,
                 "Result": "PASS" if r.passed else "FAIL"}
                for r in results
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    failures = [r for r in results if not r.passed]
    if failures:
        st.error(f"{len(failures)} scenario(s) failed.")
    else:
        st.success(f"All {len(results)} scenarios passed.")

    st.divider()
    _partial_hedge_calculator()
    st.divider()
    _reservations_panel()
