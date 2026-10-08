import sqlite3

from database import get_connection, insert_bet, list_bets
from database.migrations import MIGRATIONS, applied_versions, migrate

LEGACY_BETS = """
CREATE TABLE bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    event_date TEXT,
    bookmaker TEXT,
    exchange TEXT,
    event TEXT NOT NULL,
    market TEXT,
    bet_type TEXT NOT NULL,
    back_stake REAL NOT NULL,
    back_odds REAL NOT NULL,
    lay_odds REAL NOT NULL,
    commission REAL NOT NULL,
    lay_stake REAL NOT NULL,
    liability REAL NOT NULL,
    profit_if_back REAL NOT NULL,
    profit_if_lay REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'Open',
    notes TEXT
)
"""


def test_fresh_migration_records_all_versions(tmp_path):
    path = tmp_path / "fresh.db"
    migrate(path)
    with get_connection(path) as connection:
        versions = applied_versions(connection)
    assert versions == {version for version, _, _ in MIGRATIONS}


def test_migration_adds_columns_to_legacy_database(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute(LEGACY_BETS)
    connection.execute(
        """INSERT INTO bets
        (created_at,event_date,bookmaker,exchange,event,market,bet_type,back_stake,
         back_odds,lay_odds,commission,lay_stake,liability,profit_if_back,profit_if_lay,status,notes)
        VALUES ('2026-01-01','2026-01-01','Book A','Exchange','Old event','m','Qualifying bet',
                10,2,2,2,10,10,0,0,'Open','kept')"""
    )
    connection.commit()
    connection.close()

    migrate(path)

    with get_connection(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(bets)")}
        assert {"selection", "source"} <= columns
        row = connection.execute("SELECT event, notes, selection, source FROM bets").fetchone()
        assert row["event"] == "Old event"
        assert row["notes"] == "kept"
        assert row["selection"] is None
        assert row["source"] is None


def test_migrate_is_idempotent(tmp_path):
    path = tmp_path / "twice.db"
    migrate(path)
    migrate(path)
    with get_connection(path) as connection:
        versions = applied_versions(connection)
    assert len(versions) == len(MIGRATIONS)


def test_insert_bet_after_migration(tmp_path):
    path = tmp_path / "write.db"
    migrate(path)
    insert_bet(event="E", selection="S", source="opportunity", db_path=path)
    row = list_bets(path)[0]
    assert row["selection"] == "S"
    assert row["source"] == "opportunity"
