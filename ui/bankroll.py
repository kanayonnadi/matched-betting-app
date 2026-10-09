"""Bankroll page (primary)."""

import pandas as pd
import streamlit as st

from bankroll import summarize
from database import add_bankroll_entry, list_bankroll, list_bets
from risk import available_capital


def _money(value) -> str:
    from decimal import Decimal

    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return f"${amount:,.2f}"


def render():
    st.header("Bankroll")

    b1, b2, b3 = st.columns(3)
    account = b1.text_input("Account", placeholder="Bookmaker / exchange / bank")
    amount = b2.number_input(
        "Adjustment", value=0.0, step=10.0,
        help="Deposit/credit positive; withdrawal/debit negative",
    )
    note = b3.text_input("Ledger note")
    if st.button("Add bankroll entry", disabled=not account.strip() or amount == 0):
        add_bankroll_entry(account, amount, note)
        st.success("Entry added.")

    bankroll_rows = list_bankroll()
    summary = summarize(bankroll_rows, list_bets())
    state = available_capital(bankroll_rows)

    metrics = st.columns(3)
    metrics[0].metric("Tracked bankroll", _money(summary.total))
    metrics[1].metric("Reserved", _money(state.total_reserved))
    metrics[2].metric("Available", _money(state.total_available))

    if bankroll_rows:
        bank = pd.DataFrame([dict(row) for row in bankroll_rows])
        totals = (
            bank.groupby("account", as_index=False)["amount"]
            .sum()
            .rename(columns={"amount": "balance"})
        )
        st.dataframe(totals, width="stretch", hide_index=True)
        st.dataframe(bank, width="stretch", hide_index=True)
