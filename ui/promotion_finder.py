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
    classify_recommendation,
    confirm_qualifying_settlement,
    confirm_reward_received,
    discover_conversion,
    discover_qualifying,
    parse_promotion,
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


def _recommendation_card(rec, index, mode):
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
    status = classify_recommendation(rec, mode)
    st.markdown(f"Status: **{status}**")
    if flags:
        st.warning(" · ".join(flags))


def _dec(value):
    value = str(value).strip()
    if not value:
        return None
    try:
        return Decimal(value)
    except Exception:  # noqa: BLE001
        return None


def _detect_book(text):
    from matching.normalize import normalize_name

    normalized = normalize_name(text or "")
    for candidate in (
        "bet365", "betmgm", "sports interaction", "pointsbet", "proline", "olg",
        "playnow", "betano", "bet99", "betrivers",
    ):
        if candidate in normalized:
            return candidate
    return None


def _quick_input():
    st.subheader("Paste your sportsbook promotion")
    text = st.text_area(
        "Promotion", height=110, key="pf_text",
        placeholder="e.g. BetMGM Ontario: Bet $10, get a $20 free bet. Minimum odds 1.50. Ontario only.",
    )
    book_hint = st.text_input("Sportsbook (if not detected)", key="pf_book_hint")
    if st.button("Find Opportunities", type="primary", key="pf_go"):
        terms = parse_promotion(text)
        book = book_hint.strip() or _detect_book(text)
        missing = []
        if not book:
            missing.append("sportsbook")
        if terms.qualifying_stake is None:
            missing.append("qualifying stake")
        if terms.reward_amount is None:
            missing.append("reward amount")
        st.session_state["pf_terms"] = terms
        st.session_state["pf_book"] = book
        if missing:
            st.session_state["pf_missing"] = missing
        else:
            st.session_state.pop("pf_missing", None)
            promotion_id = save_promotion(
                terms, book, "promotion", jurisdiction="Ontario", terms_text=text
            )
            st.session_state["pf_selected"] = promotion_id
            st.success(f"Saved promotion #{promotion_id}. Review conditions and find a bet below.")

    missing = st.session_state.get("pf_missing")
    if missing:
        st.warning("Confirm missing/essential terms: " + ", ".join(missing))
        terms = st.session_state.get("pf_terms")
        cols = st.columns(3)
        book_c = cols[0].text_input("Sportsbook", value=st.session_state.get("pf_book") or "")
        stake_c = cols[1].text_input("Qualifying stake", value=str(terms.qualifying_stake) if terms and terms.qualifying_stake else "")
        reward_c = cols[2].text_input("Reward amount", value=str(terms.reward_amount) if terms and terms.reward_amount else "")
        if st.button("Confirm & save", key="pf_confirm_save"):
            overrides = {
                key: value
                for key, value in (("qualifying_stake", _dec(stake_c)), ("reward_amount", _dec(reward_c)))
                if value is not None
            }
            confirmed = parse_promotion(st.session_state.get("pf_text", ""), overrides)
            if not book_c.strip():
                st.error("Sportsbook is required (it must not be assumed).")
            else:
                promotion_id = save_promotion(
                    confirmed, book_c.strip(), "promotion", jurisdiction="Ontario",
                    terms_text=st.session_state.get("pf_text", ""),
                )
                st.session_state["pf_selected"] = promotion_id
                st.session_state.pop("pf_missing", None)
                st.success(f"Saved promotion #{promotion_id}.")


def _enter_promotion():
    st.subheader("Advanced · edit structured promotion fields")
    with st.expander("Manual entry / corrections", expanded=False):
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
    keys = list(options)
    default_index = 0
    selected = st.session_state.get("pf_selected")
    if selected is not None:
        for index, key in enumerate(keys):
            if options[key] == selected:
                default_index = index
                break
    label = st.selectbox("Promotion", keys, index=default_index)
    promotion = promotion_from_row(get_promotion(options[label]))
    state = sync_workflow(promotion.id)
    st.caption(f"Workflow: **{state}** · eligibility: {promotion.eligibility}")
    return promotion, rows


def _show_discovery(result):
    for code in result.diagnostics:
        st.warning(f"{code}: {result.message}")
    if not result.recommendations:
        st.info(result.message)
        return
    for index, rec in enumerate(result.recommendations, start=1):
        _recommendation_card(rec, index, result.mode)


def _find_qualifying(promotion, mode, force=False):
    st.subheader("2 · Find a qualifying bet")
    if st.button("Find qualifying opportunities", key="pf_find_q") or force:
        try:
            result = discover_qualifying(promotion, mode)
        except LiveDataUnavailable as exc:
            st.error(str(exc))
            return
        st.session_state["pf_q_result"] = result
        st.session_state["pf_q"] = list(result.recommendations)
    result = st.session_state.get("pf_q_result")
    recommendations = st.session_state.get("pf_q") or []
    if result is not None:
        _show_discovery(result)
    if not recommendations:
        if result is None:
            st.caption("No qualifying opportunities yet. Click Find qualifying opportunities.")
        return
    for index, rec in enumerate(recommendations, start=1):
        if not rec.fully_hedged:
            continue
        if st.button(f"I placed wager #{index}", key=f"pf_q_place_{index}"):
            bet_id = record_qualifying_placed(promotion.id, rec.opportunity)
            st.success(f"Recorded as bet #{bet_id} (capital reserved). Place the wagers manually.")
            st.session_state["pf_last_q_bet"] = bet_id


def _confirm_steps(promotion):
    st.subheader("3 · Confirm placement, outcome & reward")
    last_bet = st.session_state.get("pf_last_q_bet")

    if st.button("I placed the sportsbook bet + STX hedge", key="pf_hedge_placed"):
        if last_bet is None:
            st.error("Record a recommended wager first (section 2).")
        else:
            st.success("Placement noted (both sides). Confirm settlement once resolved.")

    if last_bet is None:
        st.caption("Confirm a recommended qualifying wager in section 2 to enable settlement confirmation.")
    else:
        cols = st.columns(3)
        outcome = cols[0].selectbox("Settled outcome", OUTCOMES, key="pf_outcome")
        fee = cols[1].text_input("Actual STX fee (optional)")
        pnl = cols[2].text_input("Actual total P&L (optional)")
        if st.button("Qualifying bet settled", key="pf_confirm_settle"):
            actual = None
            if fee.strip() or pnl.strip():
                from settlement import ActualSettlement

                actual = ActualSettlement(
                    bet_id=int(last_bet), outcome=outcome,
                    actual_exchange_fee=Decimal(fee) if fee.strip() else None,
                    actual_total_pnl=Decimal(pnl) if pnl.strip() else None,
                )
            result = confirm_qualifying_settlement(int(last_bet), outcome, actual)
            st.success(f"{result.status}: {result.message}")

    st.write("**Free bet received**")
    tokens = list_reward_tokens(promotion.id)
    pending = [t for t in tokens if t["status"] == "PENDING"]
    if not tokens:
        cols2 = st.columns(2)
        count = cols2[0].number_input(
            "Reward tokens", min_value=1, value=int(promotion.reward_count or 1), key="pf_tokcount"
        )
        if cols2[1].button("Create reward tokens", key="pf_create_tokens"):
            if promotion.reward_amount is None:
                st.error("Reward amount unknown.")
            else:
                for index in range(int(count)):
                    create_reward_token(
                        RewardToken(
                            promotion_id=promotion.id,
                            token_key=f"auto-{promotion.id}-{index}",
                            face_value=promotion.reward_token_amount,
                            reward_type=promotion.reward_type or "FREE_BET_SNR",
                        )
                    )
                st.success("Reward tokens created (pending).")
    else:
        st.dataframe(
            [{"Face": t["face_value"], "Type": t["reward_type"], "Status": t["status"]} for t in tokens]
        )
        if pending and st.button("Free bet received", key="pf_reward"):
            confirm_reward_received(promotion.id, [t["id"] for t in pending])
            st.success("Reward marked received.")


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
    if st.button("Find conversion opportunities", key="pf_find_c"):
        try:
            result = discover_conversion(promotion, token["face_value"], mode)
        except LiveDataUnavailable as exc:
            st.error(str(exc))
            return
        st.session_state["pf_c_result"] = result
        st.session_state["pf_c"] = list(result.recommendations)
    result = st.session_state.get("pf_c_result")
    recommendations = st.session_state.get("pf_c") or []
    if result is not None:
        _show_discovery(result)
    if not recommendations:
        return
    for index, rec in enumerate(recommendations, start=1):
        _recommendation_card(rec, index, mode)
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
    cols[1].metric("Projected (placed, pending)", _money(net["pending_conversion"]))
    cols[2].metric("Realized (settled)", _money(net["realized_net"]))
    cols[3].metric("Outstanding liability", _money(net["outstanding_liability"]))
    st.caption(
        "Projected = current quotes (estimate). Realized = confirmed settlements only. "
        "Locked-in = outcome P&L from confirmed fills (shown per recommendation)."
    )


def render():
    st.header("Find a Bet")
    st.caption(
        "Promotion-first workflow. This app never places wagers; links/records only. "
        "Place every wager manually."
    )
    mode = st.radio("Odds source", MODES, horizontal=True)
    if mode == "LIVE":
        st.caption("LIVE mode uses The Odds API + production STX (consumes API quota). No mock fallback.")

    _quick_input()
    _enter_promotion()
    promotion, _rows = _promotion_selector()
    if promotion is None:
        return
    _find_qualifying(promotion, mode)
    _confirm_steps(promotion)
    _find_conversion(promotion, mode)
    _result(promotion)
