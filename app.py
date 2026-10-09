from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import streamlit as st

from bankroll import summarize
from calculators import calculate_free_bet_snr, calculate_qualifying
from env import load_env
from database import (
    add_bankroll_entry,
    init_db,
    insert_bet,
    list_bankroll,
    list_bets,
    update_bet_status,
)
from ui import alerts as alerts_ui
from ui import analytics as analytics_ui
from ui import arbitrage as arbitrage_ui
from ui import bankroll as bankroll_ui
from ui import mybets as mybets_ui
from ui import offers as offers_ui
from ui import opportunities as opportunities_ui
from ui import promotion_finder as promotion_finder_ui
from ui import promotions as promotions_ui
from ui import settlement as settlement_ui
from ui import stress as stress_ui

PRIMARY_PAGES = ["Find a Bet", "My Bets", "Bankroll"]
ADVANCED_PAGES = [
    "Dashboard",
    "Matched Bet Calculator",
    "Opportunities",
    "Arbitrage",
    "Alerts",
    "Bet Tracker",
    "Offer Planner",
    "Promotions",
    "Settlement",
    "Risk",
    "Analytics",
]


def fmt(value):
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return f"${d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def rows_to_frame(rows):
    return pd.DataFrame([dict(row) for row in rows])


st.set_page_config(page_title="Matched Betting Desk", page_icon="📊", layout="wide")
load_env()
init_db()

st.title("Matched Betting Desk")
st.caption(
    "Personal calculator, bet log, offer tracker and bankroll ledger. "
    "No bets are placed by this app."
)

primary = st.sidebar.radio("Menu", PRIMARY_PAGES + ["Advanced"])
if primary == "Advanced":
    page = st.sidebar.radio("Advanced tools", ADVANCED_PAGES)
else:
    page = primary

if page == "Find a Bet":
    promotion_finder_ui.render()

elif page == "My Bets":
    mybets_ui.render()

elif page == "Bankroll":
    bankroll_ui.render()

elif page == "Dashboard":
    st.header("Dashboard")
    bet_rows = list_bets()
    bankroll_rows = list_bankroll()
    bets = rows_to_frame(bet_rows)

    if bets.empty:
        total_profit = 0.0
        open_bets = 0
        completed_bets = 0
    else:
        total_profit = ((bets.profit_if_back + bets.profit_if_lay) / 2).sum()
        open_bets = int((bets.status == "Open").sum())
        completed_bets = len(bets) - open_bets

    bankroll = summarize(bankroll_rows, bet_rows)

    col1, col2, col3 = st.columns(3)
    col1.metric("Expected Profit", fmt(total_profit))
    col2.metric("Bankroll", fmt(bankroll.total))
    col3.metric("Free Capital", fmt(bankroll.free_capital))

    col4, col5, col6 = st.columns(3)
    col4.metric("Capital Deployed", fmt(bankroll.capital_committed))
    col5.metric("Open Bets", open_bets)
    col6.metric("Completed Bets", completed_bets)

elif page == "Matched Bet Calculator":
    st.header("Matched Bet Calculator")

    left, right = st.columns([1, 1])
    with left:
        bet_type = st.selectbox(
            "Bet type",
            ["Qualifying bet", "Free bet (stake not returned)"],
        )
        back_stake = st.number_input("Back stake", min_value=0.01, value=100.00, step=5.0)
        back_odds = st.number_input("Back odds (decimal)", min_value=1.01, value=2.20, step=0.01)
        lay_odds = st.number_input("Lay odds (decimal)", min_value=1.01, value=2.24, step=0.01)
        commission = st.number_input(
            "Exchange commission (%)", min_value=0.0, max_value=20.0, value=2.0, step=0.1
        )

    commission_fraction = Decimal(str(commission)) / Decimal("100")

    if bet_type == "Qualifying bet":
        result = calculate_qualifying(back_stake, back_odds, lay_odds, commission_fraction)
        headline = "Expected qualifying result"
    else:
        result = calculate_free_bet_snr(back_stake, back_odds, lay_odds, commission_fraction)
        headline = "Expected free-bet conversion"

    with right:
        st.subheader(headline)
        a, b = st.columns(2)
        a.metric("Lay stake", fmt(result.lay_stake))
        b.metric("Lay liability", fmt(result.liability))
        a.metric("If back bet wins", fmt(result.profit_if_back))
        b.metric("If lay bet wins", fmt(result.profit_if_lay))
        st.metric("Matched result", fmt(result.expected_profit))
        if bet_type == "Qualifying bet":
            st.metric("Qualifying loss", fmt(result.qualifying_loss))
        else:
            st.metric("Conversion rate", f"{float(result.conversion_rate):.1f}%")

    st.divider()
    st.subheader("Save calculation")
    c1, c2, c3 = st.columns(3)
    event = c1.text_input("Event / selection")
    bookmaker = c2.text_input("Bookmaker")
    exchange = c3.text_input("Exchange")
    c4, c5, c6 = st.columns(3)
    market = c4.text_input("Market", value="Match odds")
    event_date = c5.date_input("Event date", value=date.today())
    notes = c6.text_input("Notes")
    if st.button("Save bet", type="primary", disabled=not event.strip()):
        insert_bet(
            event=event,
            bookmaker=bookmaker,
            exchange=exchange,
            market=market,
            bet_type=bet_type,
            back_stake=float(result.back_stake),
            back_odds=float(result.back_odds),
            lay_odds=float(result.lay_odds),
            commission=float(result.commission) * 100,
            lay_stake=float(result.lay_stake),
            liability=float(result.liability),
            profit_if_back=float(result.profit_if_back),
            profit_if_lay=float(result.profit_if_lay),
            event_date=event_date,
            notes=notes,
        )
        st.success("Bet saved.")

elif page == "Opportunities":
    opportunities_ui.render()

elif page == "Arbitrage":
    arbitrage_ui.render()

elif page == "Alerts":
    alerts_ui.render()

elif page == "Bet Tracker":
    st.header("Bet Tracker")
    df = rows_to_frame(list_bets())
    if df.empty:
        st.info("No saved bets yet.")
    else:
        summary1, summary2, summary3 = st.columns(3)
        summary1.metric("Logged bets", len(df))
        summary2.metric("Total back stakes", fmt(df.back_stake.sum()))
        summary3.metric("Total lay liability", fmt(df.liability.sum()))
        st.dataframe(df, width="stretch", hide_index=True)
        st.download_button(
            "Export CSV",
            df.to_csv(index=False).encode(),
            "matched_bets.csv",
            "text/csv",
        )
        bet_id = st.number_input("Bet ID to update", min_value=1, step=1)
        status = st.selectbox(
            "New status", ["Open", "Won at bookmaker", "Won at exchange", "Void"]
        )
        if st.button("Update status"):
            update_bet_status(int(bet_id), status)
            st.success("Status updated. Refresh the page to see it.")

elif page == "Offer Planner":
    offers_ui.render()

elif page == "Promotions":
    promotions_ui.render()

elif page == "Settlement":
    settlement_ui.render()

elif page == "Risk":
    stress_ui.render()

elif page == "Analytics":
    analytics_ui.render()

st.sidebar.header("Formula notes")
st.sidebar.write("Qualifying lay stake = back stake × back odds ÷ (lay odds − commission).")
st.sidebar.write("SNR free-bet lay stake = free stake × (back odds − 1) ÷ (lay odds − commission).")
st.sidebar.warning(
    "Check operator terms, eligibility, local law, settlement rules, "
    "commission and odds before using any calculation."
)
