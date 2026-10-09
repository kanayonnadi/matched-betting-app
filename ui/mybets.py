"""My Bets page (primary): records grouped by promotion."""

from decimal import Decimal

import pandas as pd
import streamlit as st

from database import list_bets, list_promotion_actions, list_promotions, list_reward_tokens
from promotions import collection_from_promotion, promotion_from_row, reward_canonical_status
from services import calculate_net_profit, sync_workflow


def _money(value) -> str:
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return f"${amount:,.2f}"


def _promotion_bets(actions, bets_by_id, action_name):
    return [
        bets_by_id[a["bet_id"]]
        for a in actions
        if a["action"] == action_name and a["bet_id"] in bets_by_id
    ]


def render():
    st.header("My Bets")
    st.caption("Wagers grouped by promotion. Underlying wager and settlement records are preserved below.")

    promotions = list_promotions()
    if not promotions:
        st.info("No promotions yet. Start on **Find a Bet**.")
        return

    bets_by_id = {int(b["id"]): b for b in list_bets()}

    for row in promotions:
        promotion = promotion_from_row(row)
        state = sync_workflow(promotion.id)
        actions = list_promotion_actions(promotion.id)
        tokens = list_reward_tokens(promotion.id)
        net = calculate_net_profit(promotion.id)

        qualifying = _promotion_bets(actions, bets_by_id, "qualifying_placed")
        conversions = _promotion_bets(actions, bets_by_id, "conversion_placed")

        label = f"{promotion.sportsbook} — {promotion.name}  ·  {state}"
        with st.expander(label, expanded=False):
            metrics = st.columns(4)
            metrics[0].metric("Qualifying loss", _money(net["qualifying_loss"]))
            metrics[1].metric("Conversion (settled)", _money(net["realized_conversion"]))
            metrics[2].metric("Net realized", _money(net["realized_net"]))
            metrics[3].metric("Outstanding liability", _money(net["outstanding_liability"]))

            collection = collection_from_promotion(promotion)
            reward_cols = st.columns(4)
            reward_cols[0].metric("Rewards", f"{len(tokens)}" + (f" × {_money(collection.unit_amount)}" if collection.unit_amount is not None and collection.count > 1 else ""))
            reward_cols[1].metric("Face value", _money(collection.total_face_value))
            reward_cols[2].metric("Outstanding", _money(net["token_outstanding"]))
            reward_cols[3].metric("Pending conversion", _money(net["pending_conversion"]))

            st.write("**Qualifying bets**")
            if qualifying:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {"Bet": b["id"], "Event": b["event"], "Selection": b["selection"],
                             "Back": b["back_odds"], "Lay": b["lay_odds"], "Status": b["status"]}
                            for b in qualifying
                        ]
                    ),
                    width="stretch", hide_index=True,
                )
            else:
                st.caption("None recorded.")

            st.write("**Conversion bets**")
            if conversions:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {"Bet": b["id"], "Event": b["event"], "Selection": b["selection"],
                             "Back": b["back_odds"], "Lay": b["lay_odds"], "Status": b["status"]}
                            for b in conversions
                        ]
                    ),
                    width="stretch", hide_index=True,
                )
            else:
                st.caption("None recorded.")

            st.write("**Rewards (individual bonus bets)**")
            if tokens:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Reward": (t["sequence"] + 1) if t["sequence"] is not None else "—",
                                "Face": _money(t["face_value"]),
                                "Type": t["reward_type"],
                                "Status": reward_canonical_status(t["status"]),
                                "Expires": (t["expires_at"] or "")[:19],
                                "Realized": _money(t["realized_pnl"]) if t["realized_pnl"] is not None else "—",
                            }
                            for t in tokens
                        ]
                    ),
                    width="stretch", hide_index=True,
                )
            else:
                st.caption("No rewards yet.")
