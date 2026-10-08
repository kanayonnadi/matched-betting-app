"""Opportunities page: table, filters, ranking, detail and live/mock source.

Business logic lives in the ``opportunities``/``matching``/``providers``/
``database`` services. This module only renders and forwards user actions.
"""

from decimal import Decimal

import pandas as pd
import streamlit as st

from bankroll import summarize
from database import insert_bet, list_bankroll, list_bets, save_prediction_snapshot
from matching import REVIEW_MATCH
from monitoring import format_age, is_stale
from settlement import prediction_from_opportunity
from opportunities import (
    DEFAULT_SORT,
    SORT_KEYS,
    BetKind,
    Opportunity,
    OpportunityFilter,
    default_mock_sports,
    discover_live_opportunities,
    discover_mock_opportunities,
    rank_opportunities,
)
from providers import DEFAULT_ROUTES, CachedProvider, build_providers
from risk import reserve_for_bet

BET_TYPE_LABELS = {
    BetKind.QUALIFYING: "Qualifying bet",
    BetKind.FREE_BET_SNR: "Free bet (stake not returned)",
}

SOURCES = ["Mock (demo)", "Live (Canada / STX)"]


def _money(value) -> str:
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return f"${amount:,.2f}"


def _controls():
    source = st.radio("Data source", SOURCES, horizontal=True)
    live = source == SOURCES[1]

    if live:
        options = [route.label for route in DEFAULT_ROUTES]
    else:
        options = default_mock_sports()
    default = list(options)

    with st.expander("Filters & scoring", expanded=True):
        row1 = st.columns(3)
        selected_sports = row1[0].multiselect("Sports", options, default=default)
        stake = row1[1].number_input("Back stake ($)", min_value=1.0, value=100.0, step=5.0)
        kind_label = row1[2].selectbox(
            "Bet type", ["Qualifying bet", "Free bet (stake not returned)"]
        )
        kind = (
            BetKind.QUALIFYING
            if kind_label.startswith("Qualifying")
            else BetKind.FREE_BET_SNR
        )

        row2 = st.columns(3)
        max_ql_pct = row2[0].number_input(
            "Max qualifying loss %", min_value=0.0, value=100.0, step=0.5,
            help="Raise to disable this filter.",
        )
        min_back = row2[1].number_input("Min back odds", min_value=1.0, value=1.0, step=0.05)
        max_liability = row2[2].number_input(
            "Max liability ($)", min_value=0.0, value=100000.0, step=50.0
        )

        row3 = st.columns(4)
        confirmed_only = row3[0].checkbox("Confirmed matches only", value=False)
        only_liquid = row3[1].checkbox("Sufficient liquidity only", value=True)
        only_fresh = row3[2].checkbox("Only show fresh odds", value=True)
        sort_by = row3[3].selectbox(
            "Sort by",
            list(SORT_KEYS.keys()),
            index=list(SORT_KEYS.keys()).index(DEFAULT_SORT),
        )

        fundable_only = st.checkbox("Only show opportunities I can fund", value=False)

    return {
        "source": source,
        "live": live,
        "sports": selected_sports,
        "stake": stake,
        "kind": kind,
        "max_ql_pct": Decimal(str(max_ql_pct)),
        "min_back": Decimal(str(min_back)),
        "max_liability": Decimal(str(max_liability)),
        "confirmed_only": confirmed_only,
        "only_liquid": only_liquid,
        "only_fresh": only_fresh,
        "fundable_only": fundable_only,
        "sort_by": sort_by,
    }


def _discover(settings):
    if not settings["live"]:
        return (
            discover_mock_opportunities(
                settings["stake"], sports=settings["sports"], kind=settings["kind"]
            ),
            "mock",
        )

    providers = build_providers()
    if not (providers.live and providers.exchange_live):
        st.warning(
            "Live feeds are not configured (missing The Odds API key or STX credentials). "
            "Showing the mock demo instead."
        )
        return (
            discover_mock_opportunities(settings["stake"], kind=settings["kind"]),
            "mock",
        )

    routes = [route for route in DEFAULT_ROUTES if route.label in settings["sports"]]
    book = CachedProvider(providers.sportsbook, ttl_seconds=30)
    exchange = CachedProvider(providers.exchange, ttl_seconds=30)
    opportunities = discover_live_opportunities(
        book, exchange, routes, settings["stake"], kind=settings["kind"]
    )
    return opportunities, "live"


def _build_table(opportunities) -> pd.DataFrame:
    rows = []
    for opp in opportunities:
        confidence = "-" if opp.event_confidence is None else f"{opp.event_confidence:.0%}"
        rows.append(
            {
                "Event": opp.event_label,
                "Sport": opp.sport,
                "Selection": opp.selection,
                "Book": opp.book_provider,
                "Exchange": opp.exchange_provider,
                "Back": f"{opp.back_odds:.2f}",
                "Lay": f"{opp.lay_odds:.2f}",
                "QL": _money(opp.qualifying_loss),
                "QL%": f"{opp.qualifying_loss_pct:.2f}%",
                "Liability": _money(opp.lay_liability),
                "Capital": _money(opp.required_capital),
                "Rating": f"{opp.rating_percent:.1f}%",
                "Eff lay": f"{opp.effective_lay_odds:.3f}" if opp.effective_lay_odds is not None else "-",
                "Liq": opp.liquidity_grade or "-",
                "Match": confidence,
                "Updated": format_age(opp.timestamp),
            }
        )
    return pd.DataFrame(rows)


def _render_execution(opp: Opportunity):
    ex = opp.execution
    if ex is None:
        return
    st.markdown("**STX execution (depth)**")
    left, right = st.columns(2)
    with left:
        st.write(f"Required hedge: `{ex.requested_contracts:.2f}` contracts")
        st.write(f"Immediately fillable: `{ex.fillable_contracts:.2f}` contracts")
        st.write(f"Best lay: `{_fmt_odds(ex.best_lay_odds)}`")
        st.write(f"Effective lay: `{_fmt_odds(ex.effective_lay_odds)}`")
        st.write(f"Worst fill: `{_fmt_odds(ex.worst_lay_odds)}`")
    with right:
        st.write(f"Levels consumed: `{ex.levels_consumed}`")
        st.write(f"Slippage: `{ex.slippage_pct:.2f}%`")
        st.write(f"Liability: `{_money(ex.liability)}`")
        st.write(f"Estimated STX fee: `{_money(ex.estimated_fee)}`")
        st.write(f"Liquidity: `{opp.liquidity_grade or '-'}`")
    if ex.book_age_seconds is not None:
        st.write(f"Order book age: `{ex.book_age_seconds:.0f}` seconds")
    if ex.fills:
        with st.expander("Fill levels"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Price": f"{fill.price:.4f}",
                            "Lay odds": f"{fill.lay_odds:.4f}",
                            "Contracts": f"{fill.contracts:.2f}",
                            "Dollars": _money(fill.lay_stake),
                        }
                        for fill in ex.fills
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
    if opp.fully_hedged is False:
        from risk import compute_exposure

        exposure = compute_exposure(
            opp.stake,
            opp.back_odds,
            ex.requested_contracts,
            [(fill.price, fill.contracts) for fill in ex.fills],
            opp.max_price if opp.max_price is not None else Decimal("1"),
            fee=ex.estimated_fee or Decimal("0"),
        )
        st.error(
            "HEDGE INCOMPLETE — the original qualifying loss does NOT apply. "
            f"Unhedged {exposure.unhedged_contracts:.2f} contracts "
            f"({_money(exposure.unhedged_back_stake)})."
        )
        cols = st.columns(3)
        cols[0].metric("P&L if back wins", _money(exposure.back_win_pnl))
        cols[1].metric("P&L if lay wins", _money(exposure.lay_win_pnl))
        cols[2].metric("Worst case", _money(exposure.worst_case_pnl))


def _fmt_odds(value):
    return f"{value:.4f}" if value is not None else "-"


def _render_detail(opp: Opportunity, free_capital: Decimal):
    st.divider()
    st.subheader(f"{opp.event_label} — {opp.selection}")

    left, right = st.columns(2)
    with left:
        st.markdown("**Opportunity**")
        st.write(f"Sportsbook: `{opp.book_provider}`")
        st.write(f"Exchange: `{opp.exchange_provider}`")
        st.write(f"Market: `{opp.market}`")
        st.write(f"Start: {opp.start_time:%Y-%m-%d %H:%M %Z}")
        st.write(f"Back odds: `{opp.back_odds:.3f}`")
        st.write(f"Lay odds: `{opp.lay_odds:.3f}`")
        st.write(f"Back stake: `{_money(opp.stake)}`")
        st.write(f"Recommended lay stake: `{_money(opp.lay_stake)}`")
        if opp.fee_model == "per_wager":
            st.write(f"Exchange fee (per wager): `{_money(opp.lay_fee or Decimal('0'))}`")
        else:
            st.write(f"Exchange commission: `{opp.commission * 100:.2f}%`")

    with right:
        st.markdown("**Risk & capital**")
        st.write(f"Exchange liability: `{_money(opp.lay_liability)}`")
        st.write(f"Total capital required: `{_money(opp.required_capital)}`")
        st.write(f"Available exchange liquidity: `{_money(opp.available_size)}`")
        st.write(f"Rating: `{opp.rating_percent:.1f}%`")
        st.write(f"Profit if back wins: `{_money(opp.profit_if_back)}`")
        st.write(f"Profit if lay wins: `{_money(opp.profit_if_lay)}`")
        st.write(f"Expected result: `{_money(opp.expected_profit)}`")
        if opp.qualifying_loss:
            st.write(
                f"Qualifying loss: `{_money(opp.qualifying_loss)}` "
                f"(`{opp.qualifying_loss_pct:.2f}%`)"
            )
        if opp.conversion_rate is not None:
            st.write(f"Free-bet conversion: `{opp.conversion_rate:.1f}%`")

    st.markdown("**Safety checks**")
    fresh = not is_stale(opp.timestamp)
    checks = [
        (True, f"Event matched (confidence {opp.event_confidence:.0%})" if opp.event_confidence is not None else "Event matched"),
        (True, f"Market matched (confidence {opp.market_confidence:.0%})" if opp.market_confidence is not None else "Market matched"),
        (True, "Selection matched"),
        (opp.liquidity_sufficient, "Exchange liquidity sufficient"),
        (fresh, f"Odds fresh ({format_age(opp.timestamp)})"),
        (
            opp.required_capital <= free_capital,
            f"Fundable from free capital (${free_capital:,.2f})",
        ),
    ]
    if opp.execution is not None:
        ex = opp.execution
        book_age = ex.book_age_seconds if ex.book_age_seconds is not None else Decimal("0")
        checks.extend(
            [
                (ex.fully_fillable, "Full hedge available at current depth"),
                (ex.slippage_pct <= Decimal("1.5"), f"Slippage acceptable ({ex.slippage_pct:.2f}%)"),
                (book_age <= Decimal("30"), f"Order book fresh ({book_age:.0f}s)"),
            ]
        )
    ready = all(passed for passed, _ in checks)
    st.markdown(f"**Safety status: {'READY' if ready else 'NOT READY'}**")
    for passed, label in checks:
        st.write(f"{'✓' if passed else '⚠'} {label}")

    _render_execution(opp)
    st.caption(f"Quotes updated {opp.timestamp:%Y-%m-%d %H:%M:%S %Z}")

    if st.button("Recheck opportunity", key=f"recheck_{opp.event_id}_{opp.selection}"):
        st.rerun()

    button_key = f"add_{opp.event_id}_{opp.selection}"
    if st.button("Add to Bet Log", type="primary", key=button_key):
        bet_id = insert_bet(
            event=opp.event_label,
            bookmaker=opp.book_provider,
            exchange=opp.exchange_provider,
            market=opp.market,
            selection=opp.selection,
            sport=opp.sport,
            bet_type=BET_TYPE_LABELS[opp.kind],
            back_stake=float(opp.stake),
            back_odds=float(opp.back_odds),
            lay_odds=float(opp.lay_odds),
            commission=float(opp.commission * 100),
            lay_stake=float(opp.lay_stake),
            liability=float(opp.lay_liability),
            profit_if_back=float(opp.profit_if_back),
            profit_if_lay=float(opp.profit_if_lay),
            lay_fee=float(opp.lay_fee) if opp.lay_fee is not None else None,
            notes="From opportunity finder",
            source="opportunity",
        )
        save_prediction_snapshot(prediction_from_opportunity(opp, bet_id))
        reserve_for_bet(
            bet_id, opp.book_provider, opp.exchange_provider, opp.stake, opp.lay_liability
        )
        st.success(
            "Added to bet log and reserved capital. Wagers still need to be placed manually."
        )


def render():
    st.header("Matched Betting Opportunities")
    st.caption("This app never places wagers; place them manually.")

    settings = _controls()

    if not settings["sports"]:
        st.info("Select at least one sport.")
        return

    try:
        with st.spinner("Scanning feeds and matching markets..."):
            opportunities, source = _discover(settings)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        st.error(f"Could not build opportunities: {exc}")
        return

    free_capital = summarize(list_bankroll(), list_bets()).free_capital

    filters = OpportunityFilter(
        min_back_odds=settings["min_back"],
        max_liability=settings["max_liability"],
        confirmed_only=settings["confirmed_only"],
        only_liquid=settings["only_liquid"],
    )
    if settings["kind"] is BetKind.QUALIFYING:
        filters.max_qualifying_loss_pct = settings["max_ql_pct"]
    if settings["fundable_only"]:
        filters.max_required_capital = free_capital

    opportunities = filters.apply(opportunities)
    if settings["only_fresh"]:
        opportunities = [o for o in opportunities if not is_stale(o.timestamp)]
    ranked = rank_opportunities(opportunities, sort_by=settings["sort_by"])

    st.caption(
        f"{len(ranked)} opportunities · source: {source} · "
        f"sorted by {settings['sort_by']} · free capital ${free_capital:,.2f}"
    )

    if not ranked:
        st.info("No opportunities match the current filters.")
        return

    table = _build_table(ranked)
    event = st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="opportunities_table",
    )

    selected_rows = []
    if event is not None and getattr(event, "selection", None) is not None:
        selected_rows = list(event.selection.rows)

    if selected_rows:
        _render_detail(ranked[selected_rows[0]], free_capital)
    else:
        st.caption("Select a row to open the full calculation.")
