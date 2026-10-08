from decimal import Decimal

from monitoring import (
    AlertRule,
    InAppNotifier,
    alert_key,
    back_lay_gap_pct,
    build_alert,
    evaluate,
    matches,
)
from opportunities import BetKind, discover_mock_opportunities

OPPS = discover_mock_opportunities(100)


def test_min_back_odds():
    rule = AlertRule(id=1, min_back_odds=Decimal("2.0"))
    matched = [o for o in OPPS if matches(rule, o)]
    assert matched
    assert all(o.back_odds >= Decimal("2.0") for o in matched)


def test_max_qualifying_loss_pct():
    rule = AlertRule(id=2, max_qualifying_loss_pct=Decimal("3.5"))
    matched = [o for o in OPPS if matches(rule, o)]
    assert all(o.qualifying_loss_pct <= Decimal("3.5") for o in matched)


def test_max_required_liability():
    rule = AlertRule(id=3, max_required_liability=Decimal("200"))
    matched = [o for o in OPPS if matches(rule, o)]
    assert all(o.lay_liability <= Decimal("200") for o in matched)


def test_bookmaker_and_sport():
    rule = AlertRule(id=4, bookmaker="mock_book", sport="soccer")
    matched = [o for o in OPPS if matches(rule, o)]
    assert matched
    assert all(o.sport == "soccer" for o in matched)


def test_disabled_rule_matches_nothing():
    rule = AlertRule(id=5, enabled=False, max_qualifying_loss_pct=Decimal("100"))
    assert not any(matches(rule, o) for o in OPPS)


def test_back_lay_gap():
    opportunity = OPPS[0]
    expected = (opportunity.lay_odds - opportunity.back_odds) / opportunity.back_odds * Decimal("100")
    assert back_lay_gap_pct(opportunity) == expected


def test_free_bet_conversion_rule_requires_free_bet():
    free = discover_mock_opportunities(10, kind=BetKind.FREE_BET_SNR)
    rule = AlertRule(id=6, min_free_bet_conversion=Decimal("40"))
    matched = [o for o in free if matches(rule, o)]
    assert matched
    assert all(o.conversion_rate >= Decimal("40") for o in matched)
    assert not any(matches(rule, o) for o in OPPS)


def test_evaluate_returns_one_alert_per_opportunity():
    rule = AlertRule(id=7, max_qualifying_loss_pct=Decimal("100"))
    alerts = evaluate(OPPS, [rule])
    assert len(alerts) == len(OPPS)


def test_evaluate_deduplicates_existing_keys():
    rule = AlertRule(id=8, max_qualifying_loss_pct=Decimal("100"))
    alerts = evaluate(OPPS, [rule])
    keys = [alert.dedup_key for alert in alerts]
    assert evaluate(OPPS, [rule], existing_keys=keys) == []


def test_alert_key_is_unique_per_selection():
    rule = AlertRule(id=9)
    keys = {alert_key(rule, o) for o in OPPS}
    assert len(keys) == len(OPPS)


def test_in_app_notifier_records():
    recorded = []
    notifier = InAppNotifier(record=recorded.append)
    alert = build_alert(AlertRule(id=10, name="Low QL"), OPPS[0])
    notifier.send(alert)
    assert notifier.sent == [alert]
    assert recorded == [alert]
    assert "Low QL" in alert.message
