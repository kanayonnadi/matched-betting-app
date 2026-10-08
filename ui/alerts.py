"""Alerts page: manage rules, scan for matches, view history."""

import pandas as pd
import streamlit as st

from database import (
    alert_dedup_keys,
    create_alert_rule,
    delete_alert_rule,
    list_alert_history,
    list_alert_rules,
    record_alert,
    set_alert_rule_enabled,
)
from monitoring import AlertRule, InAppNotifier, evaluate
from opportunities import BetKind, discover_mock_opportunities

KINDS = {
    "Any": None,
    "Qualifying bet": BetKind.QUALIFYING.value,
    "Free bet (SNR)": BetKind.FREE_BET_SNR.value,
}


def _optional(value):
    return None if not value else value


def _record(alert):
    record_alert(
        alert.rule_id,
        alert.rule_name,
        alert.dedup_key,
        event_id=alert.event_id,
        selection=alert.selection,
        bookmaker=alert.bookmaker,
        exchange=alert.exchange,
        message=alert.message,
    )


def _add_rule_form():
    with st.expander("Add alert rule", expanded=False):
        with st.form("add_alert_rule", clear_on_submit=True):
            row1 = st.columns(3)
            name = row1[0].text_input("Rule name")
            kind_label = row1[1].selectbox("Bet kind", list(KINDS.keys()))
            sport = row1[2].text_input("Sport (optional)", placeholder="soccer")

            row2 = st.columns(3)
            max_ql_pct = row2[0].number_input("Max qualifying loss %", min_value=0.0, value=2.0, step=0.1)
            min_back_odds = row2[1].number_input("Min back odds", min_value=0.0, value=2.0, step=0.05)
            max_liability = row2[2].number_input("Max liability ($)", min_value=0.0, value=500.0, step=10.0)

            row3 = st.columns(3)
            gap = row3[0].number_input("Max back/lay gap %", min_value=0.0, value=0.0, step=0.1)
            conversion = row3[1].number_input("Min free-bet conversion %", min_value=0.0, value=0.0, step=1.0)
            bookmaker = row3[2].text_input("Bookmaker (optional)")
            notes = st.text_input("Notes")

            submitted = st.form_submit_button("Add rule", type="primary")

        if submitted:
            if not name.strip():
                st.error("Rule name is required.")
            else:
                create_alert_rule(
                    name=name,
                    kind=KINDS[kind_label],
                    max_qualifying_loss_pct=_optional(max_ql_pct),
                    max_back_lay_gap_pct=_optional(gap),
                    min_back_odds=_optional(min_back_odds),
                    max_required_liability=_optional(max_liability),
                    sport=_optional(sport.strip()),
                    bookmaker=_optional(bookmaker.strip()),
                    min_free_bet_conversion=_optional(conversion),
                    notes=notes or None,
                )
                st.success("Rule added.")
                st.rerun()


def _manage_rules(rule_rows):
    st.subheader("Rules")
    columns = st.columns(3)
    rule_id = columns[0].number_input("Rule ID", min_value=1, step=1)
    if columns[1].button("Enable"):
        set_alert_rule_enabled(int(rule_id), True)
        st.success("Rule enabled.")
    if columns[2].button("Disable"):
        set_alert_rule_enabled(int(rule_id), False)
        st.success("Rule disabled.")

    delete_columns = st.columns(2)
    if delete_columns[0].button("Delete rule"):
        delete_alert_rule(int(rule_id))
        st.success("Rule deleted.")


def render():
    st.header("Alerts")
    st.caption("Rules are evaluated in-app over the discovered opportunities.")

    rule_rows = list_alert_rules()
    history_rows = list_alert_history()

    metrics = st.columns(3)
    metrics[0].metric("Rules", len(rule_rows))
    metrics[1].metric("Enabled", sum(1 for row in rule_rows if row["enabled"]))
    metrics[2].metric("Alerts recorded", len(history_rows))

    _add_rule_form()

    if rule_rows:
        st.dataframe(pd.DataFrame([dict(row) for row in rule_rows]), width="stretch", hide_index=True)
        _manage_rules(rule_rows)
    else:
        st.info("No rules yet. Add one above.")

    st.divider()
    columns = st.columns(2)
    stake = columns[0].number_input("Back stake ($)", min_value=1.0, value=100.0, step=5.0)
    if columns[1].button("Scan for alerts", type="primary"):
        opportunities = discover_mock_opportunities(stake)
        rules = [AlertRule.from_row(row) for row in rule_rows]
        notifier = InAppNotifier(record=_record)
        for alert in evaluate(opportunities, rules, existing_keys=alert_dedup_keys()):
            notifier.send(alert)

        if notifier.sent:
            st.success(f"{len(notifier.sent)} new alert(s).")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Rule": alert.rule_name,
                            "Event": alert.opportunity.event_label,
                            "Selection": alert.selection,
                            "Book": alert.bookmaker,
                            "Exchange": alert.exchange,
                            "QL%": f"{alert.opportunity.qualifying_loss_pct:.2f}%",
                            "Liability": f"${alert.opportunity.lay_liability:,.2f}",
                        }
                        for alert in notifier.sent
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("No new alerts (nothing matched, or already alerted).")

    if history_rows:
        st.subheader("Alert history")
        st.dataframe(pd.DataFrame([dict(row) for row in history_rows]), width="stretch", hide_index=True)
