"""Analytics page: profit, capital and promotion performance."""

from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import streamlit as st

from analytics import (
    overview,
    profit_by_bookmaker,
    profit_by_month,
    profit_by_promotion,
    profit_by_sport,
)
from database import list_bets, list_offers


def _money(value) -> str:
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return f"${amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def _pct(value) -> str:
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return f"{amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}%"


def _group_frame(mapping) -> pd.DataFrame:
    return pd.DataFrame(
        [{"Group": key, "Profit": _money(value)} for key, value in sorted(mapping.items())]
    )


def render():
    st.header("Analytics")

    bet_rows = list_bets()
    offer_rows = list_offers()
    bets_by_id = {int(row["id"]): row for row in bet_rows}
    result = overview(bet_rows, offer_rows, bets_by_id)

    row1 = st.columns(3)
    row1[0].metric("Lifetime profit", _money(result.lifetime_profit))
    row1[1].metric("30-day profit", _money(result.profit_30d))
    row1[2].metric("Net profit", _money(result.net_profit))

    row2 = st.columns(3)
    row2[0].metric("Capital deployed", _money(result.capital_deployed))
    row2[1].metric("Return on capital", _pct(result.return_on_capital))
    row2[2].metric("Capital efficiency", _pct(result.capital_efficiency))

    row3 = st.columns(3)
    row3[0].metric("Gross rewards", _money(result.gross_rewards))
    row3[1].metric("Converted rewards", _money(result.converted_rewards))
    row3[2].metric("Qualifying losses", _money(result.qualifying_losses))

    row4 = st.columns(3)
    row4[0].metric("Exchange commissions", _money(result.exchange_commissions))
    row4[1].metric("Average qualifying loss", _money(result.average_qualifying_loss))
    row4[2].metric("Average free-bet conversion", _pct(result.average_free_bet_conversion))

    st.divider()
    if not bet_rows:
        st.info("No logged bets yet — profit breakdowns will appear once you log bets.")
        return

    columns = st.columns(2)
    with columns[0]:
        st.subheader("Profit by bookmaker")
        st.dataframe(_group_frame(profit_by_bookmaker(bet_rows)), width="stretch", hide_index=True)
        st.subheader("Profit by sport")
        st.dataframe(_group_frame(profit_by_sport(bet_rows)), width="stretch", hide_index=True)
    with columns[1]:
        st.subheader("Profit by month")
        st.dataframe(_group_frame(profit_by_month(bet_rows)), width="stretch", hide_index=True)
        st.subheader("Profit by promotion")
        st.dataframe(
            _group_frame(profit_by_promotion(offer_rows, bets_by_id)),
            width="stretch",
            hide_index=True,
        )
