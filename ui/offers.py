"""Offer Planner page: lifecycle management and expected value."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import streamlit as st

from database import (
    create_offer,
    get_offer,
    link_offer_to_bet,
    list_bets,
    list_offers,
    update_offer_status,
)
from offers import ALL_STATUSES, advance, summarize

OFFER_TYPES = [
    "Sign-up",
    "Reload",
    "Free bet",
    "Risk-free bet",
    "Deposit bonus",
    "Other",
]


def _money(value) -> str:
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return f"${amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def _summary(offer_rows, bet_rows):
    bets_by_id = {int(row["id"]): row for row in bet_rows}
    return summarize(offer_rows, bets_by_id)


def _render_summary(summary):
    row1 = st.columns(3)
    row1[0].metric("Active offers", summary.active_offers)
    row1[1].metric("Expected value", _money(summary.expected_value))
    row1[2].metric("Qualifying losses incurred", _money(summary.qualifying_losses_incurred))
    row2 = st.columns(3)
    row2[0].metric("Rewards received", _money(summary.rewards_received))
    row2[1].metric("Converted profit", _money(summary.converted_profit))
    row2[2].metric("Net profit", _money(summary.net_profit))


def _add_offer_form():
    with st.expander("Add offer", expanded=False):
        with st.form("add_offer", clear_on_submit=True):
            row1 = st.columns(3)
            name = row1[0].text_input("Offer name")
            bookmaker = row1[1].text_input("Bookmaker")
            offer_type = row1[2].selectbox("Offer type", OFFER_TYPES)

            row2 = st.columns(3)
            qualifying_stake = row2[0].number_input(
                "Qualifying stake", min_value=0.0, value=100.0, step=5.0
            )
            min_odds = row2[1].number_input("Minimum odds", min_value=1.0, value=2.0, step=0.05)
            max_qualifying_loss = row2[2].number_input(
                "Max qualifying loss", min_value=0.0, value=5.0, step=0.5
            )

            row3 = st.columns(3)
            reward = row3[0].number_input("Reward face value", min_value=0.0, value=50.0, step=5.0)
            conversion = row3[1].number_input(
                "Expected reward conversion (%)", min_value=0.0, max_value=100.0, value=75.0, step=1.0
            )
            expiry = row3[2].date_input("Expiry", value=date.today())
            notes = st.text_input("Notes")

            submitted = st.form_submit_button("Add offer", type="primary")

        if submitted:
            if not name.strip():
                st.error("Offer name is required.")
            else:
                create_offer(
                    name=name,
                    bookmaker=bookmaker,
                    offer_type=offer_type,
                    qualifying_stake=qualifying_stake,
                    min_odds=min_odds,
                    max_qualifying_loss=max_qualifying_loss,
                    reward=reward,
                    reward_conversion=conversion,
                    expiry=expiry,
                    notes=notes,
                )
                st.success("Offer added.")
                st.rerun()


def _update_controls(offer_rows):
    st.subheader("Manage offers")
    columns = st.columns(3)
    offer_id = columns[0].number_input("Offer ID", min_value=1, step=1)
    new_status = columns[1].selectbox("New status", ALL_STATUSES)

    if columns[2].button("Update status"):
        current = get_offer(int(offer_id))
        if current is None:
            st.error("Offer not found.")
        elif new_status == current["status"]:
            st.info("Status unchanged.")
        else:
            try:
                advance(current["status"], new_status)
            except ValueError as exc:
                st.error(str(exc))
            else:
                update_offer_status(int(offer_id), new_status)
                st.success("Status updated.")

    link_columns = st.columns(2)
    bet_id = link_columns[0].number_input("Link bet ID", min_value=1, step=1)
    if link_columns[1].button("Link bet"):
        link_offer_to_bet(int(offer_id), int(bet_id))
        st.success("Bet linked to offer.")


def _quick_estimator():
    with st.expander("Quick expected-value estimate"):
        columns = st.columns(3)
        required_stake = columns[0].number_input(
            "Required qualifying stake", min_value=0.0, value=100.0, step=10.0
        )
        expected_ql_pct = columns[1].number_input(
            "Expected qualifying loss (%)", min_value=0.0, value=2.0, step=0.1
        )
        reward = columns[2].number_input(
            "Reward face value", min_value=0.0, value=50.0, step=5.0
        )
        columns2 = st.columns(2)
        conversion = columns2[0].number_input(
            "Expected reward conversion (%)", min_value=0.0, max_value=100.0, value=75.0, step=1.0
        )
        extra_costs = columns2[1].number_input(
            "Other expected costs", min_value=0.0, value=0.0, step=1.0
        )

        qloss = required_stake * expected_ql_pct / 100
        reward_value = reward * conversion / 100
        expected_net = reward_value - qloss - extra_costs
        metrics = st.columns(3)
        metrics[0].metric("Expected qualifying loss", _money(qloss))
        metrics[1].metric("Expected reward value", _money(reward_value))
        metrics[2].metric("Expected net value", _money(expected_net))


def render():
    st.header("Offer Planner")
    st.caption(
        "Track promotions through their lifecycle and connect them to logged bets. "
        "This app never places wagers."
    )

    offer_rows = list_offers()
    bet_rows = list_bets()
    _render_summary(_summary(offer_rows, bet_rows))

    _add_offer_form()

    if offer_rows:
        table = pd.DataFrame([dict(row) for row in offer_rows])
        st.dataframe(table, width="stretch", hide_index=True)
        _update_controls(offer_rows)
    else:
        st.info("No offers yet. Add one above.")

    _quick_estimator()
