"""Find a Bet — the guided, promotion-first workflow (M23).

Enter promotion -> find qualifying bet -> confirm reward -> find conversion bet
-> track realized profit. Built on the existing providers/matching/calculators/
verification/liquidity/risk services.
"""

from decimal import Decimal

import streamlit as st

from database import (
    create_reward_token,
    get_promotion,
    list_promotions,
    list_reward_tokens,
)
from promotions import (
    RewardToken,
    promotion_from_row,
)
from services import (
    LiveDataUnavailable,
    MODES,
    calculate_net_profit,
    confirm_qualifying_settlement,
    confirm_reward_received,
    find_conversion_bets,
    find_qualifying_bets,
    parse_promotion,
    rank_conversion_bets,
    rank_qualifying_bets,
    record_conversion_placed,
    record_qualifying_placed,
    save_promotion,
    sync_workflow,
)
from settlement import SettledOutcome

OUTCOMES = [item.value for item in SettledOutcome]


def _money(value) -> str:
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return f"${amount:,.2f}"


def _recommendation_card(rec, index):
    opp = rec.opportunity
    st.markdown(f"**#{index} · {opp.event_label} — {opp.selection}**")
    c1, c2 = st.columns(2)
    with c1:
        st.write(f"Book: `{opp.book_provider}`  ·  Back: `{opp.back_odds:.2f}`  ·  Stake: `{_money(opp.stake)}`")
        st.write(f"Start: {opp.start_time:%Y-%m-%d %H:%M %Z}")
        st.write(f"Market: `{opp.market}`  ·  Selection: `{opp.selection}`")
    with c2:
        st.write(f"STX lay: `{opp.lay_odds:.3f}`  ·  Lay stake: `{_money(opp.lay_stake)}`")
        st.write(f"Liability: `{_money(opp.lay_liability)}`  ·  Fee: `{_money(opp.lay_fee or Decimal('0'))}`")
    flags = []
    if not rec.fully_hedged:
        flags.append("HEDGE INCOMPLETE")
    if rec.stale:
        flags.append("STALE")
    if rec.source_type != "LIVE":
        flags.append("SIMULATED")
    status_line = f"Worst case `{_money(rec.worst_case_pnl)}` · capital `{_money(rec.capital)}`"
    if rec.liquidity_grade:
        status_line += f" · liquidity `{rec.liquidity_grade}`"
    if rec.slippage_pct is not None:
        status_line += f" · slippage `{rec.slippage_pct:.2f}%`"
    st.write(status_line)
    st.caption(f"Source: {rec.source_type} · quote {rec.timestamp}")
    if flags:
        st.warning(" · ".join(flags))
    else:
        st.success("Fully hedged · fresh")


def _enter_promotion():
    st.subheader("1 · Enter the promotion")
    with st.expander("Paste promotion text / enter details", expanded=True):
        text = st.text_area("Promotion text", placeholder="Bet $10, get a $20 free bet. Minimum odds 1.50.")
        cols = st.columns(3)
        sportsbook = cols[0].text_input("Sportsbook")
        name = cols[1].text_input("Offer name")
        jurisdiction = cols[2].text_input("Jurisdiction", value="Ontario")
        cols2 = st.columns(3)
        stake = cols2[0].text_input("Qualifying stake (fallback)")
        reward = cols2[1].text_input("Reward amount (fallback)")
        min_odds = cols2[2].text_input("Min odds (fallback)")
        cols3 = st.columns(2)
        official_url = cols3[0].text_input("Official promotion URL")
        terms_url = cols3[1].text_input("Terms URL")

        if st.button("Parse promotion", key="pf_parse"):
            overrides = {}
            for key, value in (("qualifying_stake", stake), ("reward_amount", reward), ("qualifying_min_odds", min_odds)):
                value = str(value).strip()
                if value:
                    overrides[key] = Decimal(value)
            terms = parse_promotion(text, overrides)
            st.session_state["pf_terms"] = terms
            st.session_state["pf_meta"] = dict(
                sportsbook=sportsbook, name=name, jurisdiction=jurisdiction,
                official_url=official_url, terms_url=terms_url, text=text,
            )

        terms = st.session_state.get("pf_terms")
        if terms is not None:
            st.json(
                {
                    "offer_type": terms.offer_type,
                    "qualifying_stake": str(terms.qualifying_stake),
                    "minimum_odds": str(terms.qualifying_min_odds),
                    "reward_amount": str(terms.reward_amount),
                    "reward_type": terms.reward_type,
                    "reward_count": terms.reward_count,
                    "stake_returned": terms.stake_returned,
                    "unknown_fields": list(terms.unknown_fields),
                }
            )
            if terms.unknown_fields:
                st.warning(
                    "Ambiguous/unverified terms: " + ", ".join(terms.unknown_fields)
                    + ". Confirm the fallback fields above before saving."
                )
            meta = st.session_state.get("pf_meta", {})
            if st.button("Save promotion", type="primary", key="pf_save"):
                promotion_id = save_promotion(
                    terms,
                    meta.get("sportsbook") or "Unknown",
                    meta.get("name") or "Untitled offer",
                    official_url=meta.get("official_url"),
                    terms_url=meta.get("terms_url"),
                    terms_text=meta.get("text"),
                    jurisdiction=meta.get("jurisdiction"),
                )
                st.success(f"Saved promotion #{promotion_id}. Confirm eligibility on the Promotions page, then find a bet below.")
                st.session_state.pop("pf_terms", None)


def _promotion_selector():
    rows = list_promotions()
    if not rows:
        st.info("No promotions saved yet. Enter one above.")
        return None, None
    options = {f"#{r['id']} {r['sportsbook']} — {r['name']}": r["id"] for r in rows}
    label = st.selectbox("Promotion", list(options))
    promotion = promotion_from_row(get_promotion(options[label]))
    state = sync_workflow(promotion.id)
    st.caption(f"Workflow: **{state}** · eligibility: {promotion.eligibility}")
    return promotion, rows


def _find_qualifying(promotion, mode):
    st.subheader("2 · Find a qualifying bet")
    if st.button("Find qualifying odds", key="pf_find_q"):
        try:
            opportunities = find_qualifying_bets(promotion, mode)
            st.session_state["pf_q"] = rank_qualifying_bets(promotion, opportunities)
        except LiveDataUnavailable as exc:
            st.error(str(exc))
            st.session_state["pf_q"] = []
    recommendations = st.session_state.get("pf_q")
    if not recommendations:
        st.caption("No qualifying recommendations yet (or none fully hedged).")
        return
    for index, rec in enumerate(recommendations, start=1):
        _recommendation_card(rec, index)
        if st.button(f"I placed wager #{index}", key=f"pf_q_place_{index}"):
            bet_id = record_qualifying_placed(promotion.id, rec.opportunity)
            st.success(f"Recorded as bet #{bet_id} (capital reserved). Place the wagers manually.")
            st.session_state["pf_last_q_bet"] = bet_id


def _confirm_steps(promotion):
    st.subheader("3 · Confirm outcome & reward")
    bet_id = st.number_input(
        "Qualifying bet ID", min_value=1, step=1,
        value=int(st.session_state.get("pf_last_q_bet", 1)),
        key="pf_settle_bet",
    )
    cols = st.columns(3)
    outcome = cols[0].selectbox("Settled outcome", OUTCOMES, key="pf_outcome")
    fee = cols[1].text_input("Actual STX fee")
    pnl = cols[2].text_input("Actual total P&L")
    if st.button("Confirm qualifying settlement", key="pf_confirm_settle"):
        actual = None
        if fee.strip() or pnl.strip():
            from settlement import ActualSettlement

            actual = ActualSettlement(
                bet_id=int(bet_id), outcome=outcome,
                actual_exchange_fee=Decimal(fee) if fee.strip() else None,
                actual_total_pnl=Decimal(pnl) if pnl.strip() else None,
            )
        result = confirm_qualifying_settlement(int(bet_id), outcome, actual)
        st.success(f"{result.status}: {result.message}")

    st.write("**Confirm reward received**")
    tokens = list_reward_tokens(promotion.id)
    if tokens:
        st.dataframe(
            [{"Token": t["id"], "Face": t["face_value"], "Type": t["reward_type"], "Status": t["status"]} for t in tokens]
        )
        token_ids = st.text_input("Token IDs to mark received (comma separated)", key="pf_tokens")
        if st.button("Confirm reward received", key="pf_reward"):
            ids = [int(x) for x in token_ids.split(",") if x.strip().isdigit()]
            if ids:
                confirm_reward_received(promotion.id, ids)
                st.success("Reward tokens marked received.")
    else:
        cols2 = st.columns(2)
        count = cols2[0].number_input("Tokens to create", min_value=1, value=int(promotion.reward_count or 1), key="pf_tokcount")
        if cols2[1].button("Create & mark received", key="pf_create_tokens"):
            if promotion.reward_amount is None:
                st.error("Reward amount unknown.")
            else:
                ids = []
                for index in range(int(count)):
                    ids.append(
                        create_reward_token(
                            RewardToken(
                                promotion_id=promotion.id,
                                token_key=f"auto-{promotion.id}-{index}",
                                face_value=promotion.reward_token_amount,
                                reward_type=promotion.reward_type or "FREE_BET_SNR",
                            )
                        )
                    )
                confirm_reward_received(promotion.id, ids)
                st.success("Reward tokens created and marked received.")


def _find_conversion(promotion, mode):
    st.subheader("4 · Find a conversion bet")
    tokens = list_reward_tokens(promotion.id)
    available = [t for t in tokens if t["status"] in ("RECEIVED", "USED") and t["face_value"]]
    if not available:
        st.caption("Confirm a received reward token first.")
        return
    token_map = {f"#{t['id']} (${t['face_value']})": t for t in available}
    label = st.selectbox("Token", list(token_map))
    token = token_map[label]
    if st.button("Find conversion odds", key="pf_find_c"):
        try:
            opportunities = find_conversion_bets(promotion, token["face_value"], mode)
            st.session_state["pf_c"] = rank_conversion_bets(token["face_value"], opportunities)
        except LiveDataUnavailable as exc:
            st.error(str(exc))
            st.session_state["pf_c"] = []
    recommendations = st.session_state.get("pf_c")
    if not recommendations:
        st.caption("No conversion recommendations yet.")
        return
    for index, rec in enumerate(recommendations, start=1):
        _recommendation_card(rec, index)
        rate = rec.opportunity.conversion_rate
        if rate is not None:
            st.write(f"Estimated conversion: `{rate:.1f}%` · guaranteed value `{_money(rec.opportunity.expected_profit)}`")
        if st.button(f"I placed conversion #{index}", key=f"pf_c_place_{index}"):
            bet_id = record_conversion_placed(promotion.id, token["id"], rec.opportunity)
            st.success(f"Conversion recorded as bet #{bet_id}; token marked USED. Place manually.")


def _result(promotion):
    st.subheader("5 · Result")
    net = calculate_net_profit(promotion.id)
    cols = st.columns(4)
    cols[0].metric("Qualifying loss", _money(net["qualifying_loss"]))
    cols[1].metric("Conversion (settled)", _money(net["realized_conversion"]))
    cols[2].metric("Net realized", _money(net["realized_net"]))
    cols[3].metric("Outstanding liability", _money(net["outstanding_liability"]))


def render():
    st.header("Find a Bet")
    st.caption(
        "Promotion-first workflow. This app never places wagers; links/records only. "
        "Place every wager manually."
    )
    mode = st.radio("Odds source", MODES, horizontal=True)
    if mode == "LIVE":
        st.caption("LIVE mode uses The Odds API + production STX (consumes API quota). No mock fallback.")

    _enter_promotion()
    promotion, _rows = _promotion_selector()
    if promotion is None:
        return
    _find_qualifying(promotion, mode)
    _confirm_steps(promotion)
    _find_conversion(promotion, mode)
    _result(promotion)
