import pytest

from database import (
    cap_odds_snapshots,
    count_odds_snapshots,
    get_connection,
    init_db,
    list_odds_snapshots,
    prune_odds_snapshots,
    record_odds_snapshot,
)

HISTORY_TABLES = {
    "accounts",
    "providers",
    "events",
    "markets",
    "odds_snapshots",
    "matched_events",
    "opportunities",
}


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "history.db"
    init_db(path)
    return path


def test_history_tables_created(db_path):
    with get_connection(db_path) as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert HISTORY_TABLES <= names


def test_record_and_list_snapshots(db_path):
    record_odds_snapshot("mock_book", "e1", "moneyline", "Knicks", "back", 1.85, db_path=db_path)
    record_odds_snapshot(
        "stx", "e2", "moneyline", "Knicks", "lay", 1.90,
        available_size=500, commission=0.05, db_path=db_path,
    )
    assert count_odds_snapshots(db_path) == 2
    assert list_odds_snapshots(db_path)[0]["provider"] == "stx"


def test_prune_snapshots_by_age(db_path):
    old_id = record_odds_snapshot("p", "e1", "m", "s", "back", 2.0, db_path=db_path)
    with get_connection(db_path) as connection:
        connection.execute(
            "UPDATE odds_snapshots SET recorded_at=? WHERE id=?", ("2000-01-01T00:00:00", old_id)
        )
    record_odds_snapshot("p", "e2", "m", "s", "back", 2.0, db_path=db_path)

    assert prune_odds_snapshots(retention_days=7, db_path=db_path) == 1
    assert count_odds_snapshots(db_path) == 1


def test_cap_snapshots(db_path):
    for index in range(5):
        record_odds_snapshot("p", f"e{index}", "m", "s", "back", 2.0, db_path=db_path)
    assert cap_odds_snapshots(max_rows=3, db_path=db_path) == 2
    assert count_odds_snapshots(db_path) == 3
