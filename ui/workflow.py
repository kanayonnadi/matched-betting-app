"""Three-stage matched-betting workflow (M26).

Stage 1 Enter Promotion -> Stage 2 Find Qualifying Bet -> Stage 3 Find Promo Bets.

A thin orchestration layer over the existing services: ingestion/parsing,
eligibility, discovery, STX depth, profitability, split rewards, bankroll and
settlement. The UI holds no betting maths; it only gathers genuinely-missing
information, records the user's approval, and renders backend results.
"""

from dataclasses import replace
from decimal import Decimal

import streamlit as st

from database import (
    get_promotion,
    list_reward_tokens,
    record_promotion_action,
    set_promotion_eligibility,
    update_promotion,
)
from promotions import (
    EligibilityStatus,
    collection_from_promotion,
    evaluate_restrictions,
    ingest_promotion,
    looks_like_url,
    parse_terms,
    promotion_from_row,
    review_issues,
    reward_canonical_status,
    sync_promotion_lifecycle,
)
from services import (
    LiveDataUnavailable,
    confirm_qualifying_settlement,
    confirm_reward_received,
    discover_profitability,
    discover_qualifying,
    issue_reward_tokens,
    record_conversion_placed,
    record_qualifying_placed,
    save_promotion,
    sync_workflow,
    verify_opportunity,
)


def _verify_line(rec):
    """Return (VerificationResult|None, caption) for an opportunity card."""
    try:
        result = verify_opportunity(rec.opportunity)
    except Exception as exc:  # noqa: BLE001
        return None, f"verification error: {exc}"
    caption = (
        f"Execution **{result.status}** · liquidity {result.depth_status} · "
        f"quote {rec.timestamp}"
    )
    if result.reasons:
        caption += " · " + "; ".join(result.reasons[:2])
    return result, caption


def _verification_details(result):
    if result is None:
        return
    st.table(
        [
            {"Metric": key,
             "Independent": str(result.independent.get(key)),
             "Optimizer": str(result.optimizer.get(key))}
            for key in ("contracts", "lay_stake", "liability", "fee", "profit_if_back", "profit_if_lay")
        ]
    )
    if result.discrepancies:
        st.error("Discrepancies: " + "; ".join(result.discrepancies))

from .promotion_finder import (
    _bankroll_available,
    _dec,
    _detect_book,
    _money,
    _recommendation_card,
    _show_discovery,
    _show_profitability,
)

ZERO = Decimal("0")

STAGES = (1, 2, 3)

_STYLES = """
<style>
[class*="st-key-wf_opt_active_"] {
    outline: 2px solid #ff4b4b;
    outline-offset: 2px;
    border-radius: 0.6rem;
}
[class*="st-key-wf_opt_dim_"] {
    opacity: 0.42;
    filter: grayscale(0.85);
    transition: opacity 0.2s ease;
}
[class*="st-key-wf_place_box"] {
    outline: 3px solid #ff4b4b;
    outline-offset: 3px;
    border-radius: 0.6rem;
    animation: wf-pulse 1.8s ease-in-out infinite;
}
@keyframes wf-pulse {
    0%, 100% { box-shadow: 0 0 8px rgba(255, 75, 75, 0.18); }
    50%      { box-shadow: 0 0 26px rgba(255, 75, 75, 0.55); }
}
.wf-arrow-hint {
    text-align: center;
    color: #ff4b4b;
    font-weight: 800;
    font-size: 1.3rem;
    letter-spacing: 0.08em;
    animation: wf-bounce 1.1s ease-in-out infinite;
    margin: 0.15rem 0;
}
@keyframes wf-bounce {
    0%, 100% { transform: translateY(0); }
    50%      { transform: translateY(7px); }
}
</style>
"""


def _inject_workflow_styles():
    st.html(_STYLES)


def _empty_discovery(mode):
    from services.discovery import DiscoveryResult, OpportunityStatus

    return DiscoveryResult((), (), OpportunityStatus.UNAVAILABLE.value, "No search yet.", mode)

def _sig(value) -> str:
    return "" if value is None else str(value)


# --- Stage 1: analysis helpers ----------------------------------------------

def analyze_promotion(value: str, book_hint: str = ""):
    """Return (terms, sportsbook, ingest_result) for a URL or pasted text."""
    value = (value or "").strip()
    if looks_like_url(value):
        ingest = ingest_promotion(value, sportsbook_override=book_hint or None)
        return ingest.terms, (ingest.sportsbook or book_hint or None), ingest
    terms = parse_terms(value)
    book = book_hint or _detect_book(value)
    return terms, book, None


def effective_terms(terms, answers: dict):
    """Apply the user's answers to genuinely-missing information only."""
    reward_structure = answers.get("reward_structure")
    stake_returned = answers.get("stake_returned")
    min_odds_choice = answers.get("min_odds_choice")
    min_odds_value = answers.get("min_odds_value")
    claimed = answers.get("claimed")

    if reward_structure == "One free bet":
        terms = replace(
            terms, reward_count=1, reward_denominations=(),
            reward_unit_amount=terms.reward_amount,
        )
    elif reward_structure == "Multiple equal free bets" and terms.reward_amount is not None:
        count = max(terms.reward_count or 1, 1)
        unit = Decimal(str(terms.reward_amount)) / Decimal(count)
        terms = replace(
            terms, reward_count=count,
            reward_denominations=tuple(unit for _ in range(count)),
            reward_unit_amount=unit,
        )

    if stake_returned and stake_returned != "Unknown":
        terms = replace(terms, stake_returned=stake_returned.startswith("Yes"))

    if min_odds_choice == "No minimum stated":
        terms = replace(terms, qualifying_min_odds=None)
    elif min_odds_choice == "Enter minimum odds" and min_odds_value:
        terms = replace(terms, qualifying_min_odds=Decimal(str(min_odds_value)))

    return terms, claimed


def missing_questions(terms, ingest=None):
    """Only ask what is genuinely missing/essential. Returns list of (id, label, options)."""
    questions = []
    if (
        terms.reward_amount is not None
        and terms.reward_unit_amount is None
        and not terms.reward_denominations
        and ((terms.reward_count or 1) > 1 or "reward_denomination" in (terms.unknown_fields or ()))
    ):
        questions.append(
            ("reward_structure", "Is the reward issued as one free bet or multiple equal free bets?",
             ["One free bet", "Multiple equal free bets"])
        )
    if terms.reward_type in (None, "FREE_BET_SNR", "FREE_BET_SR") and terms.stake_returned is None:
        questions.append(
            ("stake_returned", "Does the sportsbook return the original stake when the free bet wins?",
             ["Unknown", "No (stake not returned)", "Yes (stake returned)"])
        )
    if terms.qualifying_min_odds is None:
        questions.append(
            ("min_odds_choice", "Does the promotion state a minimum qualifying odds?",
             ["No minimum stated", "Enter minimum odds"])
        )
    questions.append(
        ("claimed", "Have you already claimed this promotion?",
         ["No, I'm eligible", "Yes, already claimed", "Unsure"])
    )
    return questions


def _unresolved(terms, ingest, answers=None):
    answers = answers or {}
    items = []
    if ingest is not None:
        if ingest.status == "UNREACHABLE":
            items.append("the promotion page could not be read")
        for name in ingest.missing_terms:
            items.append(f"missing {name}")
    for issue in review_issues(_terms_to_promotion(terms)):
        if answers.get("min_odds_choice") == "No minimum stated" and "minimum odds" in issue:
            continue
        if answers.get("stake_returned") in ("No (stake not returned)", "Yes (stake returned)") and "stake-return" in issue:
            continue
        items.append(issue)
    return items


def _terms_to_promotion(terms, sportsbook="Unknown"):
    return promotion_from_row(
        {  # minimal mapping; review_issues only reads attributes
            "id": 0, "sportsbook": sportsbook, "name": "", "jurisdiction": terms.jurisdiction,
            "offer_type": terms.offer_type, "new_customer_only": terms.new_customer_only,
            "qualifying_stake": terms.qualifying_stake,
            "qualifying_min_odds": terms.qualifying_min_odds,
            "qualifying_max_odds": terms.qualifying_max_odds,
            "reward_amount": terms.reward_amount, "reward_type": terms.reward_type,
            "reward_count": terms.reward_count or 1, "stake_returned": terms.stake_returned,
            "reward_expiry_days": terms.reward_expiry_days, "source_url": None,
            "terms_text": None, "status": "DISCOVERED", "confidence": terms.confidence,
            "eligibility": EligibilityStatus.UNKNOWN.value,
        }
    )


def _summary_rows(terms, sportsbook, claimed):
    reward = terms.reward_amount
    if terms.reward_count and terms.reward_count > 1 and terms.reward_unit_amount is not None:
        structure = f"{terms.reward_count} × {_money(terms.reward_unit_amount)}"
    else:
        structure = "single reward"
    eligibility = {
        "No, I'm eligible": "Eligible (you confirmed)",
        "Yes, already claimed": "Already claimed",
    }.get(claimed, "Requires confirmation")
    return {
        "Sportsbook": sportsbook or "Unknown",
        "Qualifying stake": _money(terms.qualifying_stake) if terms.qualifying_stake is not None else "Unknown",
        "Minimum odds": str(terms.qualifying_min_odds) if terms.qualifying_min_odds is not None else "None stated",
        "Reward": _money(reward) if reward is not None else "Unknown",
        "Reward structure": structure,
        "Reward type": terms.reward_type or "Unknown",
        "Stake returned": {True: "Yes", False: "No", None: "Unknown"}.get(terms.stake_returned),
        "Expiry": f"{terms.reward_expiry_hours}h after credit" if terms.reward_expiry_hours else "Unknown",
        "Eligibility": eligibility,
    }


def _approve(terms, sportsbook, ingest, answers, override_fields):
    claimed = answers.get("claimed")
    eligibility = {
        "No, I'm eligible": EligibilityStatus.ELIGIBLE.value,
        "Yes, already claimed": EligibilityStatus.ALREADY_USED.value,
    }.get(claimed, EligibilityStatus.UNKNOWN.value)

    promotion_id = save_promotion(
        terms, sportsbook or "Unknown", "promotion", jurisdiction=terms.jurisdiction or "Ontario",
        official_url=(ingest.final_url if ingest else None),
        terms_text=(ingest.text if ingest else None),
    )
    fields = {}
    if answers.get("min_odds_choice") == "No minimum stated":
        fields["no_min_odds"] = True
    if eligibility != EligibilityStatus.UNKNOWN.value:
        set_promotion_eligibility(promotion_id, eligibility, note="user confirmed in workflow")
    if ingest is not None:
        fields["terms_source_url"] = ingest.final_url
    if fields:
        update_promotion(promotion_id, **fields)
    sync_workflow(promotion_id)
    sync_promotion_lifecycle(promotion_id)
    record_promotion_action(
        promotion_id, "approved",
        note=(
            "manual approval; eligibility=" + eligibility
            + ("; overrides=" + str(override_fields) if override_fields else "")
        ),
    )
    return promotion_id


# --- Stage 1 render ----------------------------------------------------------

def _render_stage1(mode):
    st.subheader("Step 1 · Enter Promotion")
    st.text_area(
        "Promotion terms & conditions or URL",
        key="wf_input",
        height=140,
        placeholder="Paste promotion terms & conditions or a promotion URL",
        label_visibility="collapsed",
    )
    if st.button("Analyze Promotion", key="wf_analyze", type="primary"):
        value = st.session_state.get("wf_input", "")
        if not value.strip():
            st.warning("Paste promotion terms or a URL first.")
        else:
            terms, book, ingest = analyze_promotion(value)
            st.session_state.update(
                wf_terms=terms, wf_sportsbook=book, wf_ingest=ingest, wf_answers={}
            )
            st.session_state.pop("wf_approved_id", None)

    terms = st.session_state.get("wf_terms")
    if terms is None:
        st.caption(
            "The app extracts everything it can and asks only for what is missing. "
            "Nothing is wagered."
        )
        return

    ingest = st.session_state.get("wf_ingest")
    if ingest is not None:
        for message in ingest.messages:
            st.warning(message)
    sportsbook = st.session_state.get("wf_sportsbook")

    # Dynamic, compact review questions (missing/conflicting/essential only).
    questions = missing_questions(terms, ingest)
    st.markdown("**A few quick questions**")
    cols = st.columns(len(questions) or 1)
    for index, (qid, label, options) in enumerate(questions):
        answer = cols[index % len(cols)].selectbox(label, options, key=f"wf_q_{qid}")
        st.session_state.setdefault("wf_answers", {})[qid] = answer
    if st.session_state["wf_answers"].get("min_odds_choice") == "Enter minimum odds":
        st.session_state["wf_answers"]["min_odds_value"] = st.number_input(
            "Minimum odds", min_value=1.01, value=1.50, step=0.01, key="wf_min_value"
        )
    answers = st.session_state.get("wf_answers", {})

    effective, claimed = effective_terms(terms, answers)
    restricted = evaluate_restrictions(_terms_to_promotion(effective, sportsbook or ""))
    unresolved = _unresolved(effective, ingest, answers)

    st.markdown("### Promotion summary")
    st.table(_summary_rows(effective, sportsbook, claimed))
    if restricted.restricted:
        st.error("This operator's general terms restrict minimal-risk/matched betting. " + restricted.note)
    if unresolved:
        st.warning("Needs confirmation: " + "; ".join(unresolved))

    material = bool(unresolved) or restricted.restricted
    acknowledged = True
    if material:
        acknowledged = st.checkbox(
            "I have reviewed these warnings and take responsibility for proceeding",
            key="wf_ack",
        )

    c1, c2 = st.columns([1, 1])
    if c1.button("Approve Promotion", key="wf_approve", type="primary"):
        if material and not acknowledged:
            st.error("Please acknowledge the warnings before approving.")
        else:
            promotion_id = _approve(
                effective, sportsbook or "", ingest, answers,
                override_fields={k: v for k, v in answers.items() if v},
            )
            st.session_state["wf_approved_id"] = promotion_id
            st.session_state["wf_stage"] = 2
            st.rerun()
    c2.button("Edit Details", key="wf_edit_toggle")

    # Edit Details: full override of any extracted field.
    with st.expander("Edit Details (override extracted values)", expanded=False):
        st.caption("Overrides are recorded; unknown values are never silently verified.")
        e1, e2, e3 = st.columns(3)
        book_in = e1.text_input("Sportsbook", value=sportsbook or "", key="wf_edit_book")
        stake_in = e2.text_input("Qualifying stake", value=_sig(effective.qualifying_stake), key="wf_edit_stake")
        min_in = e3.text_input("Minimum odds", value=_sig(effective.qualifying_min_odds), key="wf_edit_min")
        e4, e5, e6 = st.columns(3)
        reward_in = e4.text_input("Reward total", value=_sig(effective.reward_amount), key="wf_edit_reward")
        count_in = e5.text_input("Reward count", value=_sig(effective.reward_count), key="wf_edit_count")
        unit_in = e6.text_input("Reward unit", value=_sig(effective.reward_unit_amount), key="wf_edit_unit")
        e7, e8 = st.columns(2)
        sr_in = e7.selectbox(
            "Stake returned", ["Unknown", "No", "Yes"],
            index={None: 0, False: 1, True: 2}.get(effective.stake_returned, 0), key="wf_edit_sr",
        )
        jur_in = e8.text_input("Jurisdiction", value=effective.jurisdiction or "Ontario", key="wf_edit_jur")
        if st.button("Apply edits", key="wf_edit_apply"):
            count = int(count_in) if count_in.strip().isdigit() else effective.reward_count
            updated = replace(
                effective,
                qualifying_stake=_dec(stake_in) or effective.qualifying_stake,
                qualifying_min_odds=_dec(min_in),
                reward_amount=_dec(reward_in) or effective.reward_amount,
                reward_count=count,
                reward_unit_amount=_dec(unit_in),
                reward_denominations=(),
                stake_returned={"Unknown": None, "No": False, "Yes": True}[sr_in],
                jurisdiction=jur_in or effective.jurisdiction,
            )
            st.session_state["wf_terms"] = updated
            st.session_state["wf_sportsbook"] = book_in.strip() or sportsbook
            st.success("Details updated. Review the summary and approve.")
            st.rerun()


# --- Stage 2 render ----------------------------------------------------------

def _render_stage2(mode):
    promotion_id = st.session_state.get("wf_approved_id")
    st.subheader("Step 2 · Find Qualifying Bet")
    if promotion_id is None:
        st.info("Approve a promotion in step 1 first.")
        return
    promotion = promotion_from_row(get_promotion(promotion_id))
    with st.expander("Approved promotion", expanded=False):
        st.write(f"**{promotion.sportsbook}** · {_money(promotion.qualifying_stake or 0)} qualifying · "
                 f"reward {_money(promotion.reward_amount or 0)} ({promotion.reward_type})")
        st.caption(f"eligibility: {promotion.eligibility} · status: {promotion.status}")

    if st.button("Search for Qualifying Bets", key="wf_q_search", type="primary"):
        try:
            result = discover_qualifying(promotion, mode)
        except LiveDataUnavailable as exc:
            st.error(str(exc))
            return
        st.session_state["wf_q_result"] = result
        st.session_state.pop("wf_q_selected", None)

    result = st.session_state.get("wf_q_result")
    if result is not None:
        selected = st.session_state.get("wf_q_selected")
        _show_discovery(result)
        recommendations = list(result.recommendations)
        if recommendations:
            st.markdown("**Top opportunities**")
            if selected is None:
                st.caption("Pick one option to continue. The others will be greyed out.")
            for index, rec in enumerate(recommendations[:3]):
                is_selected = selected == index
                if selected is None:
                    card_key = f"wf_opt_{index}"
                elif is_selected:
                    card_key = f"wf_opt_active_{index}"
                else:
                    card_key = f"wf_opt_dim_{index}"
                with st.container(border=True, key=card_key):
                    st.markdown(f"**Option {index + 1} · {rec.opportunity.event_label}**")
                    st.write(
                        f"{rec.opportunity.selection} · back `{rec.opportunity.back_odds:.2f}` · "
                        f"stake `{_money(rec.opportunity.stake)}` · hedge `{_money(rec.opportunity.lay_stake)}` · "
                        f"worst `{_money(rec.worst_case_pnl)}` · depth **{rec.depth_status or 'UNKNOWN'}**"
                    )
                    verification, line = _verify_line(rec)
                    st.caption(line)
                    b1, b2 = st.columns(2)
                    if is_selected:
                        b1.button("Selected", key=f"wf_q_pick_{index}", disabled=True, type="primary")
                    elif b1.button("Select This Bet", key=f"wf_q_pick_{index}",
                                   disabled=selected is not None):
                        st.session_state["wf_q_selected"] = index
                        st.rerun()
                    if b2.button("View Calculations", key=f"wf_q_view_{index}"):
                        st.session_state["wf_q_view"] = index
                    if st.session_state.get("wf_q_view") == index:
                        _recommendation_card(rec, index + 1, result.mode)
                        _verification_details(verification)

            if st.button("Recheck Opportunity (refresh quotes)", key="wf_q_recheck"):
                try:
                    fresh = discover_qualifying(promotion, mode)
                except LiveDataUnavailable as exc:
                    st.error(str(exc))
                    return
                old = list(result.recommendations)
                new = list(fresh.recommendations)
                changes = []
                for position, (before, after) in enumerate(zip(old, new), start=1):
                    if before.worst_case_pnl != after.worst_case_pnl or before.depth_status != after.depth_status:
                        changes.append(
                            f"#{position}: worst {_money(before.worst_case_pnl)} → {_money(after.worst_case_pnl)}, "
                            f"depth {before.depth_status or 'NA'} → {after.depth_status or 'NA'}"
                        )
                st.session_state["wf_q_result"] = fresh
                st.session_state["wf_q_recheck_note"] = (
                    "Changed since last check: " + " | ".join(changes) if changes
                    else "Quotes refreshed; no material change."
                )
                st.rerun()
            note = st.session_state.pop("wf_q_recheck_note", None)
            if note:
                st.info(note)

    selected = st.session_state.get("wf_q_selected")
    recommendations = list((result.recommendations if result else ()) or ())
    if selected is not None and selected < len(recommendations):
        rec = recommendations[selected]
        _inject_workflow_styles()
        st.markdown(
            '<div class="wf-arrow-hint">▼&nbsp;&nbsp;NEXT STEP&nbsp;&nbsp;▼</div>',
            unsafe_allow_html=True,
        )
        with st.container(border=True, key="wf_place_box"):
            st.markdown("### Place these two wagers manually")
            st.write(
                f"1. Back **{rec.opportunity.selection}** at **{rec.opportunity.book_provider}** · "
                f"stake `{_money(rec.opportunity.stake)}` @ `{rec.opportunity.back_odds:.2f}`"
            )
            st.write(
                f"2. Lay **{rec.opportunity.selection}** on **STX** · stake `{_money(rec.opportunity.lay_stake)}` "
                f"@ `{rec.opportunity.lay_odds:.3f}` · liability `{_money(rec.opportunity.lay_liability)}`"
            )
            if st.button("Confirm Qualifying Bet", key="wf_q_confirm", type="primary"):
                bet_id = record_qualifying_placed(promotion.id, rec.opportunity)
                st.session_state["wf_q_bet"] = bet_id
                st.success(f"Recorded bet #{bet_id}. Place the wagers manually; settlement is separate.")

    bet_id = st.session_state.get("wf_q_bet")
    if bet_id is not None:
        st.markdown("**Settlement (separate from placement)**")
        outcome = st.selectbox(
            "Settled outcome", ["BACK_WON", "LAY_WON", "VOID", "PUSH", "CANCELLED"], key="wf_q_outcome"
        )
        if st.button("Qualifying bet settled", key="wf_q_settle"):
            confirm_qualifying_settlement(int(bet_id), outcome)
            st.success("Settlement recorded. Confirm the reward below when it is credited.")
        if st.button("Fetch qualifying opportunities", key="wf_q_refresh"):
            st.session_state.pop("wf_q_result", None)
            st.rerun()

    # Reward issuance / credit.
    tokens = list_reward_tokens(promotion.id)
    collection = collection_from_promotion(promotion)
    if bet_id is not None:
        st.markdown("**Reward**")
        if not tokens:
            if collection.count:
                st.caption(f"{collection.count} reward(s) of {_money(collection.unit_amount or 0)} expected.")
                if st.button("Create rewards", key="wf_q_issue"):
                    issue_reward_tokens(promotion)
                    st.rerun()
        else:
            pending = [t for t in tokens if reward_canonical_status(t["status"]) == "PENDING"]
            if pending and st.button("Rewards credited:", key="wf_q_credit"):
                confirm_reward_received(
                    promotion.id, [t["id"] for t in pending],
                    expiry_hours=promotion.reward_expiry_hours,
                )
                st.rerun()
            elif not pending:
                st.success("Reward(s) credited. Continue to promo bets.")
                if st.button("Continue to Promo Bets", key="wf_q_continue", type="primary"):
                    st.session_state["wf_stage"] = 3
                    st.rerun()


# --- Stage 3 render ----------------------------------------------------------

def _render_stage3(mode):
    promotion_id = st.session_state.get("wf_approved_id")
    st.subheader("Step 3 · Find Promo Bets")
    if promotion_id is None:
        st.info("Approve a promotion and complete qualification first.")
        return
    promotion = promotion_from_row(get_promotion(promotion_id))

    if st.button("Search for Promo Bets", key="wf_c_search", type="primary"):
        try:
            evaluation, q_result, c_result = discover_profitability(
                promotion, mode, balances=_bankroll_available()
            )
        except LiveDataUnavailable as exc:
            st.error(str(exc))
            return
        st.session_state["wf_c_eval"] = evaluation
        st.session_state["wf_c_result"] = c_result

    evaluation = st.session_state.get("wf_c_eval")
    if evaluation is None:
        st.caption("Searches the reward configuration automatically; no re-entry needed.")
        return
    _show_profitability(
        promotion, evaluation,
        st.session_state.get("wf_q_result") or _empty_discovery(mode),
        st.session_state.get("wf_c_result") or _empty_discovery(mode),
    )
    plan = getattr(evaluation, "reward_plan", ()) or ()
    if plan:
        st.markdown("**Per-reward opportunities**")
        for index, rc in enumerate(plan):
            with st.container(border=True):
                st.write(
                    f"Reward {rc.reward_id} · face `{_money(rc.amount)}` · "
                    f"depth **{rc.depth_status or 'UNKNOWN'}** · "
                    f"{'verified' if rc.verified else ('reused' if rc.reused else 'unverified')}"
                )
                if rc.opportunity is not None:
                    st.caption(
                        f"{rc.opportunity.event_label} — {rc.opportunity.selection} · "
                        f"back `{rc.opportunity.back_odds:.2f}` · hedge `{_money(rc.opportunity.lay_stake)}` · "
                        f"projected `{_money(rc.worst_case)}`"
                    )
    if st.button("Back to qualifying", key="wf_c_back"):
        st.session_state["wf_stage"] = 2
        st.rerun()


# --- Controller --------------------------------------------------------------

def _stage_header(stage):
    labels = {1: "1 Enter Promotion", 2: "2 Find Qualifying Bet", 3: "3 Find Promo Bets"}
    cols = st.columns(3)
    for index, number in enumerate((1, 2, 3)):
        marker = "**" if stage == number else ""
        cols[index].markdown(f"{marker}{labels[number]}{marker}")


def render(mode):
    stage = st.session_state.get("wf_stage", 1)
    if stage not in STAGES:
        stage = 1
    _stage_header(stage)
    if stage == 1:
        _render_stage1(mode)
    elif stage == 2:
        _render_stage2(mode)
    else:
        _render_stage3(mode)
    # Navigation: allow returning to previous stages without losing progress.
    nav = st.columns(2)
    if stage > 1 and nav[0].button("← Back", key="wf_back"):
        st.session_state["wf_stage"] = stage - 1
        st.rerun()
    if stage < 3 and "wf_approved_id" in st.session_state and nav[1].button(
        "Continue →", key="wf_next"
    ):
        st.session_state["wf_stage"] = stage + 1
        st.rerun()
