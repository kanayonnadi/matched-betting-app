"""Arbitrage page: cross-book arbitrage, kept separate from matched betting."""

import pandas as pd
import streamlit as st

from monitoring import format_age, is_stale
from opportunities import discover_arbitrage
from providers import MockSecondSportsbookProvider, MockSportsbookProvider

SPORTS = ["basketball", "soccer"]


def _providers():
    return [MockSportsbookProvider(), MockSecondSportsbookProvider()]


def render():
    st.header("Arbitrage")
    st.caption(
        "Cross-book arbitrage (sum of inverse odds < 1). Kept separate from "
        "matched-betting promotions. Wagers are placed manually."
    )

    columns = st.columns(2)
    sports = columns[0].multiselect("Sports", SPORTS, default=SPORTS)
    stake = columns[1].number_input("Total stake ($)", min_value=1.0, value=100.0, step=10.0)

    if not sports:
        st.info("Select at least one sport.")
        return

    opportunities = discover_arbitrage(_providers(), sports, stake)
    if not opportunities:
        st.info("No arbitrage detected.")
        return

    st.caption(f"{len(opportunities)} arbitrage opportunities")
    table = pd.DataFrame(
        [
            {
                "Event": arb.event_label,
                "Sport": arb.sport,
                "Market": arb.market,
                "Total stake": f"${arb.total_stake:,.2f}",
                "Profit": f"${arb.profit:,.2f}",
                "ROI": f"{arb.roi_percent:.2f}%",
                "Updated": format_age(arb.timestamp),
            }
            for arb in opportunities
        ]
    )
    st.dataframe(table, width="stretch", hide_index=True)

    for arb in opportunities:
        label = f"{arb.event_label} — {arb.roi_percent:.2f}%"
        with st.expander(label):
            st.write(
                f"Guaranteed profit **${arb.profit:,.2f}** on a total stake of "
                f"${arb.total_stake:,.2f} (ROI {arb.roi_percent:.2f}%)."
            )
            if is_stale(arb.timestamp):
                st.warning(f"Odds may be stale ({format_age(arb.timestamp)}).")
            legs = pd.DataFrame(
                [
                    {
                        "Selection": leg.selection,
                        "Bookmaker": leg.provider,
                        "Odds": f"{leg.decimal_odds:.3f}",
                        "Stake": f"${leg.stake:,.2f}",
                    }
                    for leg in arb.legs
                ]
            )
            st.dataframe(legs, width="stretch", hide_index=True)
