import pytest

from database import (
    alert_dedup_keys,
    create_alert_rule,
    delete_alert_rule,
    init_db,
    list_alert_history,
    list_alert_rules,
    record_alert,
    set_alert_rule_enabled,
)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "alerts.db"
    init_db(path)
    return path


def test_create_and_list_rule(db_path):
    rule_id = create_alert_rule(
        name="Low QL",
        max_qualifying_loss_pct=2.0,
        min_back_odds=2.0,
        max_required_liability=500.0,
        db_path=db_path,
    )
    rules = list_alert_rules(db_path)
    assert len(rules) == 1
    assert rules[0]["id"] == rule_id
    assert rules[0]["enabled"] == 1
    assert rules[0]["max_qualifying_loss_pct"] == 2.0


def test_toggle_and_delete_rule(db_path):
    rule_id = create_alert_rule(name="R", db_path=db_path)
    set_alert_rule_enabled(rule_id, False, db_path=db_path)
    assert list_alert_rules(db_path)[0]["enabled"] == 0
    delete_alert_rule(rule_id, db_path=db_path)
    assert list_alert_rules(db_path) == []


def test_record_alert_is_deduplicated(db_path):
    first = record_alert(1, "R", "key-1", event_id="e", selection="s", db_path=db_path)
    second = record_alert(1, "R", "key-1", event_id="e", selection="s", db_path=db_path)
    assert first is True
    assert second is False
    assert len(list_alert_history(db_path)) == 1
    assert alert_dedup_keys(db_path) == {"key-1"}
