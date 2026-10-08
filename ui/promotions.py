"""Promotions page: discovery, verification, live evaluation and ranking.

Explicit DEMO/LIVE modes. DEMO uses deterministic mock data and can never yield
an actionable status. LIVE uses the configured adapters and never falls back to
mock; provider failures surface as errors.
"""

import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import streamlit as st

from bankroll import summarize
from database import (
    add_promotion_source,
    add_promotion_terms,
    create_promotion,
    create_reward_token,
    get_promotion,
    insert_bet,
    link_promotion_match,
    list_bankroll,
    list_bets,
    list_lifecycle_history,
    list_promotion_actions,
    list_promotion_sources,
    list_promotions,
    list_reward_tokens,
    record_opportunity,
    record_promotion_action,
    save_prediction_snapshot,
    set_promotion_eligibility,
    set_promotion_lifecycle,
    update_promotion,
    update_promotion_status,
    update_reward_token,
)
from analytics import expected_profit
from settlement import prediction_from_opportunity
from opportunities import BetKind, discover_live_opportunities, discover_mock_opportunities
from promotions import (
    ALL_STATUSES,
    CandidateStatus,
    EligibilityStatus,
    Promotion,
    PromotionStatus,
    RewardToken,
    RewardTokenStatus,
    best_conversion,
    evaluate_promotion,
    is_terms_verified,
    outstanding_face_value,
    parse_terms,
    pending_conversion_value,
    promotion_from_row,
    realized_value,
    review_issues,
    sync_promotion_lifecycle,
    terms_hash,
    validate_ontario,
    validate_terms,
)
from providers import DEFAULT_ROUTES, CachedProvider, build_providers
from providers.quota import get_usage_tracker
from risk import reserve_for_bet

MODES = ["DEMO MODE", "LIVE MODE"]
_LIVE_TTL_SECONDS = 30


def _money(value) -> str:
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return f"${amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def _dec(text):
    text = str(text).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except Exception:  # noqa: BLE001
        return None


def _add_promotion_form():
    with st.expander("Add promotion (manual)", expanded=False):
        columns = st.columns(3)
        sportsbook = columns[0].text_input("Sportsbook")
        name = columns[1].text_input("Offer name")
        jurisdiction = columns[2].text_input("Jurisdiction", value="Ontario")
        columns2 = st.columns(2)
        official_url = columns2[0].text_input("Official promotion URL")
        terms_url = columns2[1].text_input("Terms URL")
        terms_text = st.text_area("Promotion terms (paste official text)")

        if st.button("Parse & preview", key="parse_promo"):
            parsed = parse_terms(terms_text)
            status, issues = validate_terms(parsed)
            st.session_state["parsed_terms"] = parsed
            st.session_state["parsed_status"] = status
            st.session_state["parsed_issues"] = issues

        parsed = st.session_state.get("parsed_terms")
        if parsed is not None:
            st.write(
                f"Parsed status **{st.session_state.get('parsed_status')}** "
                f"(confidence {parsed.confidence:.0%}). Edit before saving:"
            )
            issues = st.session_state.get("parsed_issues") or ()
            if issues:
                st.warning("Parsed issues: " + "; ".join(issues))

            e1 = st.columns(3)
            stake = e1[0].text_input("Qualifying stake", value=str(parsed.qualifying_stake or ""))
            min_odds = e1[1].text_input("Min odds (decimal)", value=str(parsed.qualifying_min_odds or ""))
            max_odds = e1[2].text_input("Max odds (decimal)", value=str(parsed.qualifying_max_odds or ""))
            e2 = st.columns(3)
            reward = e2[0].text_input("Reward amount", value=str(parsed.reward_amount or ""))
            reward_types = ["FREE_BET_SNR", "FREE_BET_SR", "CASH", "BONUS", "UNKNOWN"]
            reward_type = e2[1].selectbox(
                "Reward type", reward_types,
                index=reward_types.index(parsed.reward_type) if parsed.reward_type in reward_types else 4,
            )
            token_count = e2[2].number_input("Token count", min_value=1, value=int(parsed.reward_count or 1))
            e3 = st.columns(3)
            stake_returned_label = e3[0].selectbox(
                "Stake returned",
                ["UNKNOWN", "NO (SNR)", "YES (SR)"],
                index={None: 0, False: 1, True: 2}.get(parsed.stake_returned, 0),
            )
            eligible_sports = e3[1].text_input("Eligible sports (comma-separated)")
            excluded_markets = e3[2].text_input("Excluded markets (comma-separated)")
            e4 = st.columns(3)
            expiry_days = e4[0].number_input("Reward expiry (days, 0=unknown)", min_value=0, value=int(parsed.reward_expiry_days or 0))
            new_customer_label = e4[1].selectbox(
                "New customer only",
                ["UNKNOWN", "YES", "NO"],
                index={None: 0, True: 1, False: 2}.get(parsed.new_customer_only, 0),
            )
            notes = e4[2].text_input("Notes")

            if st.button("Save promotion", type="primary"):
                offer_type = parsed.offer_type
                status = PromotionStatus.DISCOVERED.value
                promotion = Promotion(
                    sportsbook=sportsbook or "Unknown",
                    name=name or "Untitled offer",
                    jurisdiction=jurisdiction or None,
                    offer_type=offer_type,
                    new_customer_only=(
                        True if new_customer_label == "YES" else False if new_customer_label == "NO" else None
                    ),
                    qualifying_stake=_dec(stake),
                    qualifying_min_odds=_dec(min_odds),
                    qualifying_max_odds=_dec(max_odds),
                    reward_amount=_dec(reward),
                    reward_type=None if reward_type == "UNKNOWN" else reward_type,
                    reward_count=int(token_count),
                    stake_returned=None if stake_returned_label == "UNKNOWN" else stake_returned_label.startswith("YES"),
                    reward_expiry_days=expiry_days or None,
                    eligible_sports=tuple(s.strip() for s in eligible_sports.split(",") if s.strip()),
                    excluded_markets=tuple(s.strip() for s in excluded_markets.split(",") if s.strip()),
                    official_source_url=official_url or None,
                    terms_source_url=terms_url or None,
                    terms_text=terms_text,
                    status=status,
                    lifecycle_status=status,
                    confidence=parsed.confidence or 0.0,
                )
                promotion_id = create_promotion(promotion)
                add_promotion_source(
                    promotion_id,
                    url=official_url or terms_url or None,
                    text=terms_text,
                    note="manual entry",
                    source_type="OFFICIAL" if official_url else "MANUAL",
                    jurisdiction=jurisdiction or None,
                    version_hash=terms_hash(terms_text),
                    retrieved_at=datetime.now(timezone.utc),
                )
                add_promotion_terms(promotion_id, source="manual", structured=str(parsed))
                st.success(f"Promotion #{promotion_id} saved ({status}).")
                st.session_state.pop("parsed_terms", None)


def _manage(promotion_rows):
    st.subheader("Manage promotions")
    columns = st.columns(3)
    promotion_id = columns[0].number_input("Promotion ID", min_value=1, step=1)
    status = columns[1].selectbox("Status", ALL_STATUSES)
    if columns[2].button("Set status"):
        update_promotion_status(int(promotion_id), status)
        st.success("Status updated.")
    elig = st.columns(2)
    eligibility = elig[0].selectbox("Eligibility", [item.value for item in EligibilityStatus])
    if elig[1].button("Set eligibility"):
        set_promotion_eligibility(int(promotion_id), eligibility)
        st.success("Eligibility updated.")


def _live_providers():
    providers = build_providers()
    if not (providers.live and providers.exchange_live):
        return None
    return (
        CachedProvider(providers.sportsbook, ttl_seconds=_LIVE_TTL_SECONDS),
        CachedProvider(providers.exchange, ttl_seconds=_LIVE_TTL_SECONDS),
    )


def _live_pool(book, exchange, stake, kind, force):
    key = ("live", stake, getattr(kind, "value", str(kind)))
    cached = st.session_state.get("_live_pool")
    if (
        not force
        and cached
        and cached["key"] == key
        and (datetime.now(timezone.utc) - cached["at"]).total_seconds() < _LIVE_TTL_SECONDS
    ):
        return cached["opportunities"]
    opportunities = discover_live_opportunities(book, exchange, list(DEFAULT_ROUTES), stake, kind=kind)
    st.session_state["_live_pool"] = {
        "key": key,
        "at": datetime.now(timezone.utc),
        "opportunities": opportunities,
    }
    return opportunities


def _evaluate_all(promotion_rows, mode, force=False):
    free_capital = summarize(list_bankroll(), list_bets()).free_capital
    live = mode == MODES[1]
    book = exchange = None
    if live:
        pair = _live_providers()
        if pair is None:
            return None, free_capital, "Live feeds are not configured (missing Odds API key or STX credentials)."
        book, exchange = pair

    evaluations = []
    for row in promotion_rows:
        promotion = promotion_from_row(row)
        stake = float(promotion.qualifying_stake or Decimal("10"))
        if live:
            q_pool = _live_pool(book, exchange, stake, BetKind.QUALIFYING, force)
            c_pool = _live_pool(
                book, exchange, float(promotion.reward_token_amount), BetKind.FREE_BET_SNR, force
            )
        else:
            q_pool = discover_mock_opportunities(stake)
            c_pool = discover_mock_opportunities(
                float(promotion.reward_token_amount), kind=BetKind.FREE_BET_SNR
            )
        evaluations.append(
            evaluate_promotion(promotion, q_pool, c_pool, free_capital=free_capital)
        )
    return evaluations, free_capital, None


def _render_evaluation(evaluation):
    option = evaluation.qualifying
    if option is None:
        return
    opp = option.opportunity
    st.markdown(f"**QUALIFICATION** — {opp.event_label}")
    left, right = st.columns(2)
    with left:
        st.write(f"Bookmaker: `{opp.book_provider}`  ·  Back odds: `{opp.back_odds:.2f}`")
        st.write(f"Expected qualifying loss: `{_money(evaluation.expected_qualifying_loss)}`")
        st.write(f"Qualifying capital: `{_money(evaluation.qualifying_capital)}`")
        st.write(f"Projected conversion capital: `{_money(evaluation.conversion_capital)}`")
        st.write(f"Peak capital (lifecycle): `{_money(evaluation.peak_capital)}`")
    with right:
        if opp.effective_lay_odds is not None:
            st.write(f"STX effective lay odds: `{opp.effective_lay_odds:.4f}`")
            ex = opp.execution
            st.write(
                f"Contracts: `{ex.fillable_contracts:.2f}/{ex.requested_contracts:.2f}` "
                f"({ex.fill_ratio * 100:.0f}%)  ·  Fee: `{_money(ex.estimated_fee)}`"
            )
            st.write(f"Liquidity: `{opp.liquidity_grade}`  ·  Slippage: `{opp.lay_slippage_pct:.2f}%`")
        st.write(f"Projected reward value: `{_money(evaluation.projected_reward_value)}`")
        st.write(f"Projected net: `{_money(evaluation.expected_net_profit)}`")
    st.write(f"Status: **{evaluation.status}**")
    if evaluation.provenance is not None:
        st.caption(
            f"Source: {evaluation.provenance.source_type} · "
            f"book quote {evaluation.provenance.sportsbook_quote_timestamp} · "
            f"book {evaluation.provenance.exchange_book_timestamp} · "
            f"snapshot {evaluation.provenance.liquidity_snapshot_id}"
        )
    if opp.execution is not None and opp.execution.fills:
        with st.expander("View STX depth"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {"Price": f"{f.price:.4f}", "Lay odds": f"{f.lay_odds:.4f}",
                         "Contracts": f"{f.contracts:.2f}", "Dollars": _money(f.lay_stake)}
                        for f in opp.execution.fills
                    ]
                ),
                width="stretch", hide_index=True,
            )
    if evaluation.warnings:
        st.warning("; ".join(evaluation.warnings))
    if st.button("Add to Bet Log", key=f"promo_bet_{evaluation.promotion_id}"):
        provenance_json = (
            json.dumps(evaluation.provenance.as_dict())
            if evaluation.provenance is not None
            else None
        )
        bet_id = insert_bet(
            event=opp.event_label,
            bookmaker=opp.book_provider,
            exchange=opp.exchange_provider,
            market=opp.market,
            selection=opp.selection,
            sport=opp.sport,
            bet_type="Qualifying bet",
            back_stake=float(opp.stake),
            back_odds=float(opp.back_odds),
            lay_odds=float(opp.lay_odds),
            commission=float(opp.commission * 100),
            lay_stake=float(opp.lay_stake),
            liability=float(opp.lay_liability),
            profit_if_back=float(opp.profit_if_back),
            profit_if_lay=float(opp.profit_if_lay),
            lay_fee=float(opp.lay_fee) if opp.lay_fee is not None else None,
            source_type=opp.source_type,
            is_live=opp.is_live,
            provenance=provenance_json,
            notes="From promotion pipeline",
            source="promotion",
        )
        record_opportunity(opp)
        save_prediction_snapshot(prediction_from_opportunity(opp, bet_id))
        if evaluation.promotion_id is not None:
            record_promotion_action(
                evaluation.promotion_id, "qualifying_placed", bet_id=bet_id, note=opp.event_label
            )
            link_promotion_match(
                evaluation.promotion_id, event_id=opp.event_id, selection=opp.selection,
                opportunity_ref=opp.selection,
            )
        reserve_for_bet(
            bet_id, opp.book_provider, opp.exchange_provider, opp.stake, opp.lay_liability
        )
        st.success("Added to bet log, reserved capital and linked to promotion. Place wagers manually.")


def _row_to_token(row):
    from datetime import datetime as _dt

    expires_at = None
    if row["expires_at"]:
        try:
            expires_at = _dt.fromisoformat(row["expires_at"])
        except (ValueError, TypeError):
            expires_at = None
    realized = row["realized_value"]
    return RewardToken(
        id=row["id"],
        promotion_id=row["promotion_id"],
        face_value=Decimal(str(row["face_value"] or 0)),
        reward_type=row["reward_type"] or "FREE_BET_SNR",
        expires_at=expires_at,
        status=row["status"] or "PENDING",
        realized_value=Decimal(str(realized)) if realized is not None else None,
    )


def _verification_section():
    st.subheader("Verification & lifecycle")
    promotion_id = st.number_input("Promotion ID (verify)", min_value=1, step=1, key="verify_pid")
    row = get_promotion(int(promotion_id))
    if row is None:
        st.info("Enter a promotion id to verify.")
        return
    promotion = promotion_from_row(row)
    report = validate_ontario(promotion)
    cols = st.columns(3)
    cols[0].metric("Ontario", "VERIFIED" if report.ontario_ok else "UNKNOWN")
    cols[1].metric("Terms", "VERIFIED" if report.terms_verified else "INCOMPLETE")
    cols[2].metric(
        "Account eligibility",
        "CONFIRMED" if not report.account_eligibility_required else "REQUIRED",
    )
    if promotion.official_source_url:
        st.write(f"Official source: {promotion.official_source_url}")
    if promotion.terms_source_url:
        st.write(f"Terms source: {promotion.terms_source_url}")
    if "terms_verified_at" in row.keys() and row["terms_verified_at"]:
        st.caption(f"Terms verified at {row['terms_verified_at']}")
    sources = list_promotion_sources(int(promotion_id))
    if sources:
        st.caption(
            "Sources: "
            + ", ".join(f"{s['source_type']} @ {s['retrieved_at']}" for s in sources[:3])
        )
    issues = review_issues(promotion)
    if issues:
        st.warning("NEEDS REVIEW — resolve the following, then re-sync: " + "; ".join(issues))
    for warning in report.warnings:
        st.info(warning)

    with st.expander("Fix promotion terms"):
        f1 = st.columns(3)
        f_stake = f1[0].text_input("Qualifying stake", value=str(promotion.qualifying_stake or ""), key=f"fix_stake_{promotion_id}")
        f_min = f1[1].text_input("Min odds", value=str(promotion.qualifying_min_odds or ""), key=f"fix_min_{promotion_id}")
        f_max = f1[2].text_input("Max odds", value=str(promotion.qualifying_max_odds or ""), key=f"fix_max_{promotion_id}")
        f2 = st.columns(3)
        f_reward = f2[0].text_input("Reward amount", value=str(promotion.reward_amount or ""), key=f"fix_reward_{promotion_id}")
        reward_types = ["FREE_BET_SNR", "FREE_BET_SR", "CASH", "BONUS", "UNKNOWN"]
        f_rtype = f2[1].selectbox(
            "Reward type", reward_types,
            index=reward_types.index(promotion.reward_type) if promotion.reward_type in reward_types else 4,
            key=f"fix_rtype_{promotion_id}",
        )
        f_count = f2[2].number_input("Token count", min_value=1, value=int(promotion.reward_count or 1), key=f"fix_count_{promotion_id}")
        f3 = st.columns(3)
        f_sr = f3[0].selectbox(
            "Stake returned", ["UNKNOWN", "NO (SNR)", "YES (SR)"],
            index={None: 0, False: 1, True: 2}.get(promotion.stake_returned, 0),
            key=f"fix_sr_{promotion_id}",
        )
        f_jur = f3[1].text_input("Jurisdiction", value=promotion.jurisdiction or "Ontario", key=f"fix_jur_{promotion_id}")
        f_offer = f3[2].text_input("Offer type", value=promotion.offer_type or "", key=f"fix_offer_{promotion_id}")
        if st.button("Save fixes", key=f"fix_save_{promotion_id}"):
            changes = {
                "qualifying_stake": _dec(f_stake),
                "qualifying_min_odds": _dec(f_min),
                "qualifying_max_odds": _dec(f_max),
                "reward_amount": _dec(f_reward),
                "reward_type": None if f_rtype == "UNKNOWN" else f_rtype,
                "reward_count": int(f_count),
                "stake_returned": None if f_sr == "UNKNOWN" else f_sr.startswith("YES"),
                "jurisdiction": f_jur or None,
                "offer_type": f_offer or None,
            }
            update_promotion(int(promotion_id), **changes)
            target = sync_promotion_lifecycle(int(promotion_id))
            st.success(f"Updated. Lifecycle: {target}")
            st.rerun()
    with st.expander("Verification checks"):
        st.dataframe(
            pd.DataFrame(
                [{"Check": c.name, "Passed": c.passed, "Detail": c.detail} for c in report.checks]
            ),
            width="stretch",
            hide_index=True,
        )

    st.write("**Account eligibility (user-confirmed)**")
    ecols = st.columns(3)
    if ecols[0].button("Confirm eligible"):
        set_promotion_eligibility(int(promotion_id), EligibilityStatus.ELIGIBLE.value, note="user confirmed")
        st.success("Eligibility confirmed.")
    if ecols[1].button("Not eligible"):
        set_promotion_eligibility(int(promotion_id), EligibilityStatus.NOT_ELIGIBLE.value)
        st.success("Marked not eligible.")
    if ecols[2].button("Already used"):
        set_promotion_eligibility(int(promotion_id), EligibilityStatus.ALREADY_USED.value)
        st.success("Marked already used.")

    st.write("**Lifecycle (automatic)**")
    st.write(f"Current: **{promotion.lifecycle_status or promotion.status}**")
    st.caption(
        "The lifecycle advances automatically as terms, eligibility, bets, settlements and "
        "rewards are recorded. Incomplete terms, ineligibility or expiry resolve to a flagged "
        "state above. Use the button only to force a re-sync."
    )
    if st.button("Re-sync lifecycle"):
        target = sync_promotion_lifecycle(int(promotion_id))
        st.success(f"Synced to {target}")
    history = list_lifecycle_history(int(promotion_id))
    if history:
        st.dataframe(pd.DataFrame([dict(h) for h in history]), width="stretch", hide_index=True)


def _reward_tokens_section(mode):
    st.subheader("Reward tokens")
    promotion_id = st.number_input("Promotion ID (tokens)", min_value=1, step=1, key="token_pid")
    with st.form("add_token"):
        cols = st.columns(3)
        face = cols[0].number_input("Face value", min_value=0.0, value=25.0, step=5.0)
        reward_type = cols[1].selectbox("Reward type", ["FREE_BET_SNR", "FREE_BET_SR", "CASH"])
        expires = cols[2].date_input("Expires")
        add = st.form_submit_button("Add token")
    if add:
        expires_at = datetime(expires.year, expires.month, expires.day, tzinfo=timezone.utc)
        token_id = create_reward_token(
            RewardToken(
                promotion_id=int(promotion_id),
                face_value=Decimal(str(face)),
                reward_type=reward_type,
                expires_at=expires_at,
                status=RewardTokenStatus.PENDING.value,
            )
        )
        st.success(f"Token #{token_id} added (PENDING).")

    tokens = list_reward_tokens(int(promotion_id))
    if not tokens:
        st.caption("No tokens for this promotion yet.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Token": t["id"],
                    "Face": _money(t["face_value"]),
                    "Type": t["reward_type"],
                    "Status": t["status"],
                    "Expires": t["expires_at"],
                    "Realized": _money(t["realized_value"]) if t["realized_value"] is not None else "-",
                }
                for t in tokens
            ]
        ),
        width="stretch",
        hide_index=True,
    )
    bets_by_id = {int(b["id"]): b for b in list_bets()}
    token_objects = [_row_to_token(t) for t in tokens]
    metrics = st.columns(3)
    metrics[0].metric("Outstanding face value", _money(outstanding_face_value(token_objects)))
    metrics[1].metric("Realized (settled conversion)", _money(realized_value(token_objects, bets_by_id)))
    metrics[2].metric("Pending conversion (placed)", _money(pending_conversion_value(token_objects, bets_by_id)))

    controls = st.columns(3)
    token_id = controls[0].number_input("Token ID", min_value=1, step=1, key="tok_id")
    if controls[1].button("Mark RECEIVED"):
        update_reward_token(
            int(token_id), status=RewardTokenStatus.RECEIVED.value,
            received_at=datetime.now(timezone.utc).isoformat(),
        )
        st.success("Token marked received (bonus credited).")
    if controls[2].button("Mark EXPIRED"):
        update_reward_token(int(token_id), status=RewardTokenStatus.EXPIRED.value)
        st.success("Token marked expired.")
    st.caption(
        "A token becomes USED when its conversion wager is placed; realized value is "
        "computed only after that bet settles (via Bet Tracker status)."
    )

    _conversion_section(mode, int(promotion_id), tokens)


def _conversion_pool(mode, face_value):
    if mode == MODES[1]:
        pair = _live_providers()
        if pair is None:
            return []
        return _live_pool(pair[0], pair[1], float(face_value), BetKind.FREE_BET_SNR, False)
    return discover_mock_opportunities(float(face_value), kind=BetKind.FREE_BET_SNR)


def _conversion_section(mode, promotion_id, tokens):
    st.write("**Convert a received token (SNR free bet)**")
    controls = st.columns(3)
    token_id = controls[0].number_input("Token ID to convert", min_value=1, step=1, key="conv_token")
    token_row = next((t for t in tokens if int(t["id"]) == int(token_id)), None)
    if token_row is None:
        st.caption("Enter a valid token id.")
        return
    face = Decimal(str(token_row["face_value"] or 0))
    candidates = best_conversion(face, _conversion_pool(mode, face), limit=1)
    if not candidates:
        st.info("No live conversion candidate available for this token.")
        return
    candidate = candidates[0]
    opp = candidate.opportunity
    controls[1].metric("Conversion", f"{opp.conversion_rate:.1f}%")
    controls[2].metric("Guaranteed value", _money(opp.expected_profit))
    st.write(
        f"Best conversion: **{opp.event_label} — {opp.selection}** "
        f"(back {opp.back_odds:.2f}, effective lay {opp.effective_lay_odds or opp.lay_odds:.4f}, "
        f"liquidity {opp.liquidity_grade or 'n/a'})"
    )
    if st.button("Add conversion to Bet Log", key=f"conv_{token_id}"):
        bet_id = insert_bet(
            event=opp.event_label,
            bookmaker=opp.book_provider,
            exchange=opp.exchange_provider,
            market=opp.market,
            selection=opp.selection,
            sport=opp.sport,
            bet_type="Free bet (stake not returned)",
            back_stake=float(opp.stake),
            back_odds=float(opp.back_odds),
            lay_odds=float(opp.lay_odds),
            commission=float(opp.commission * 100),
            lay_stake=float(opp.lay_stake),
            liability=float(opp.lay_liability),
            profit_if_back=float(opp.profit_if_back),
            profit_if_lay=float(opp.profit_if_lay),
            lay_fee=float(opp.lay_fee) if opp.lay_fee is not None else None,
            source_type=opp.source_type,
            is_live=opp.is_live,
            notes="Free-bet conversion",
            source="promotion",
        )
        save_prediction_snapshot(prediction_from_opportunity(opp, bet_id))
        record_promotion_action(promotion_id, "conversion_placed", bet_id=bet_id, note=opp.event_label)
        update_reward_token(
            int(token_id),
            linked_conversion_bet_id=bet_id,
            status=RewardTokenStatus.USED.value,
            used_at=datetime.now(timezone.utc).isoformat(),
        )
        # Free bet: no cash sportsbook stake, but exchange collateral is required.
        reserve_for_bet(bet_id, None, opp.exchange_provider, 0, opp.lay_liability)
        st.success(
            "Conversion wager added and token marked USED. Realized value appears once the "
            "conversion bet is settled. Place the wager manually."
        )


def _promotion_progress_section():
    st.subheader("Promotion progress & realized profit")
    promotion_id = st.number_input("Promotion ID (progress)", min_value=1, step=1, key="progress_pid")
    row = get_promotion(int(promotion_id))
    if row is None:
        st.info("Enter a promotion id.")
        return
    actions = list_promotion_actions(int(promotion_id))
    bets_by_id = {int(b["id"]): b for b in list_bets()}

    qualifying_bets = [
        bets_by_id[a["bet_id"]]
        for a in actions
        if a["action"] == "qualifying_placed" and a["bet_id"] in bets_by_id
    ]
    conversion_bets = [
        bets_by_id[a["bet_id"]]
        for a in actions
        if a["action"] == "conversion_placed" and a["bet_id"] in bets_by_id
    ]

    def _settled(bet):
        return (bet["status"] or "Open") != "Open"

    qualifying_loss = sum((expected_profit(b) for b in qualifying_bets), Decimal("0"))
    realized_conversion = sum(
        (expected_profit(b) for b in conversion_bets if _settled(b)), Decimal("0")
    )
    pending_conversion = sum(
        (expected_profit(b) for b in conversion_bets if not _settled(b)), Decimal("0")
    )

    tokens = [_row_to_token(t) for t in list_reward_tokens(int(promotion_id))]
    token_realized = realized_value(tokens, bets_by_id)
    token_outstanding = outstanding_face_value(tokens)
    realized_net = qualifying_loss + realized_conversion

    metrics = st.columns(3)
    metrics[0].metric("Qualifying loss (linked)", _money(qualifying_loss))
    metrics[1].metric("Realized conversion (settled)", _money(realized_conversion))
    metrics[2].metric("Net realized profit", _money(realized_net))
    metrics2 = st.columns(3)
    metrics2[0].metric("Pending conversion (open)", _money(pending_conversion))
    metrics2[1].metric("Token outstanding face", _money(token_outstanding))
    metrics2[2].metric("Token realized (manual)", _money(token_realized))
    st.caption(
        "Realized profit counts only linked bets with a settled status. Token realized "
        "value is shown for reference and is not added again (it is already reflected in "
        "the conversion bets) to avoid double counting."
    )
    if actions:
        st.dataframe(pd.DataFrame([dict(a) for a in actions]), width="stretch", hide_index=True)


def render():
    st.header("Promotions")
    st.caption(
        "Discovery, verification, live evaluation and ranking. Decision support only — "
        "wagers are placed manually."
    )

    mode = st.radio("Data source", MODES, horizontal=True)
    promotion_rows = list_promotions()

    # Auto-advance the lifecycle from recorded facts (writes only on change).
    for row in promotion_rows:
        sync_promotion_lifecycle(row["id"])
    if promotion_rows:
        promotion_rows = list_promotions()

    metrics = st.columns(3)
    metrics[0].metric("Promotions", len(promotion_rows))
    metrics[1].metric(
        "Terms verified",
        sum(1 for row in promotion_rows if is_terms_verified(row["status"])),
    )
    metrics[2].metric(
        "Eligible", sum(1 for row in promotion_rows if row["eligibility"] == "Eligible")
    )

    if mode == MODES[1]:
        snapshot = get_usage_tracker().snapshot
        st.caption(
            f"LIVE MODE · Odds API used `{snapshot.requests_used}` · remaining "
            f"`{snapshot.requests_remaining}`"
            + (" · QUOTA EXHAUSTED" if snapshot.exhausted else "")
        )

    _add_promotion_form()

    if not promotion_rows:
        st.info("No promotions yet. Add one above.")
        return

    st.dataframe(pd.DataFrame([dict(row) for row in promotion_rows]), width="stretch", hide_index=True)
    _manage(promotion_rows)

    _verification_section()
    _reward_tokens_section(mode)
    _promotion_progress_section()

    controls = st.columns(2)
    force = controls[0].button("Recheck live feed") if mode == MODES[1] else False
    run = controls[1].button("Evaluate promotions", type="primary")

    if not run and not force:
        return

    evaluations, free_capital, error = _evaluate_all(promotion_rows, mode, force=force)
    if error:
        st.error(error)
        st.stop()
        return

    best = max(evaluations, key=lambda e: e.expected_net_profit, default=None)
    if best is not None and best.status == CandidateStatus.LIVE_CANDIDATE.value:
        st.success(
            f"BEST NEXT ACTION — {best.sportsbook} {best.name}: expected net "
            f"{_money(best.expected_net_profit)}, peak capital {_money(best.peak_capital)}"
        )
        _render_evaluation(best)

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Book": e.sportsbook,
                    "Offer": e.name,
                    "Status": e.status,
                    "Source": e.source_type,
                    "QL": _money(e.expected_qualifying_loss),
                    "Reward": _money(e.projected_reward_value),
                    "Net": _money(e.expected_net_profit),
                    "Q-Cap": _money(e.qualifying_capital),
                    "C-Cap": _money(e.conversion_capital),
                    "Peak": _money(e.peak_capital),
                    "Review": "; ".join(review_issues(promotion_from_row(next(r for r in promotion_rows if r["id"] == e.promotion_id)))),
                    "Warnings": "; ".join(e.warnings),
                }
                for e in evaluations
            ]
        ),
        width="stretch",
        hide_index=True,
    )
