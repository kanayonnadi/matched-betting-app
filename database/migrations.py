"""Schema migrations.

The user's database is never dropped or recreated. New columns/tables are added
by ordered, idempotent migrations recorded in ``schema_migrations``. This works
for both a fresh database and an existing one created by the original app.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "matched_betting.db"


def get_connection(db_path=DB_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _column_exists(connection, table: str, column: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _baseline(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS bets (
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
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS bankroll (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            account TEXT NOT NULL,
            amount REAL NOT NULL,
            note TEXT
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_bets_status ON bets(status)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_bets_event_date ON bets(event_date)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_bankroll_account ON bankroll(account)")


def _add_bet_selection_and_source(connection) -> None:
    if not _column_exists(connection, "bets", "selection"):
        connection.execute("ALTER TABLE bets ADD COLUMN selection TEXT")
    if not _column_exists(connection, "bets", "source"):
        connection.execute("ALTER TABLE bets ADD COLUMN source TEXT")


def _offers_table(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            bookmaker TEXT,
            name TEXT NOT NULL,
            offer_type TEXT,
            qualifying_stake REAL,
            min_odds REAL,
            max_qualifying_loss REAL,
            reward REAL,
            reward_conversion REAL,
            expiry TEXT,
            status TEXT NOT NULL DEFAULT 'Available',
            notes TEXT,
            bet_id INTEGER
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_offers_status ON offers(status)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_offers_expiry ON offers(expiry)")


def _alert_tables(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            kind TEXT,
            max_qualifying_loss_pct REAL,
            max_back_lay_gap_pct REAL,
            min_back_odds REAL,
            max_required_liability REAL,
            max_required_capital REAL,
            bookmaker TEXT,
            sport TEXT,
            min_free_bet_conversion REAL,
            notes TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS alert_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            rule_id INTEGER,
            rule_name TEXT,
            dedup_key TEXT UNIQUE,
            event_id TEXT,
            selection TEXT,
            bookmaker TEXT,
            exchange TEXT,
            message TEXT
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_alert_history_rule ON alert_history(rule_id)")


def _add_bet_sport(connection) -> None:
    if not _column_exists(connection, "bets", "sport"):
        connection.execute("ALTER TABLE bets ADD COLUMN sport TEXT")


def _add_bet_lay_fee(connection) -> None:
    if not _column_exists(connection, "bets", "lay_fee"):
        connection.execute("ALTER TABLE bets ADD COLUMN lay_fee REAL")


def _history_tables(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT,
            currency TEXT DEFAULT 'CAD',
            balance REAL DEFAULT 0
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            name TEXT NOT NULL UNIQUE,
            kind TEXT,
            base_url TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT,
            event_id TEXT,
            sport TEXT,
            league TEXT,
            home_team TEXT,
            away_team TEXT,
            start_time TEXT,
            UNIQUE(provider, event_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS markets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT,
            event_id TEXT,
            name TEXT,
            market_type TEXT,
            UNIQUE(provider, event_id, name)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS odds_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            provider TEXT,
            event_id TEXT,
            market TEXT,
            selection TEXT,
            side TEXT,
            decimal_odds REAL,
            available_size REAL,
            commission REAL,
            quote_timestamp TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_odds_series ON odds_snapshots(provider,event_id,market,selection)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_odds_recorded ON odds_snapshots(recorded_at)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS matched_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            sportsbook_event_id TEXT,
            exchange_event_id TEXT,
            confidence REAL,
            status TEXT,
            swapped INTEGER
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            event_id TEXT,
            selection TEXT,
            market TEXT,
            kind TEXT,
            book_provider TEXT,
            exchange_provider TEXT,
            back_odds REAL,
            lay_odds REAL,
            qualifying_loss REAL,
            qualifying_loss_pct REAL,
            required_capital REAL,
            required_liability REAL,
            rating REAL,
            event_confidence REAL,
            market_confidence REAL,
            quote_timestamp TEXT
        )
        """
    )


def _promotion_tables(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            sportsbook TEXT,
            name TEXT NOT NULL,
            jurisdiction TEXT,
            offer_type TEXT,
            new_customer_only INTEGER,
            qualifying_stake REAL,
            qualifying_min_odds REAL,
            qualifying_max_odds REAL,
            reward_amount REAL,
            reward_type TEXT,
            reward_count INTEGER,
            stake_returned INTEGER,
            reward_expiry_days INTEGER,
            source_url TEXT,
            terms_text TEXT,
            status TEXT NOT NULL DEFAULT 'DISCOVERED',
            confidence REAL,
            eligibility TEXT NOT NULL DEFAULT 'Unknown',
            expires_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS promotion_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promotion_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            url TEXT,
            text TEXT,
            note TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS promotion_terms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promotion_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            source TEXT,
            structured TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS promotion_eligibility (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promotion_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT,
            note TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS promotion_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promotion_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            action TEXT,
            bet_id INTEGER,
            note TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS promotion_rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promotion_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            amount REAL,
            reward_type TEXT,
            status TEXT,
            note TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS promotion_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promotion_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            event_id TEXT,
            selection TEXT,
            opportunity_ref TEXT,
            note TEXT
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_promotions_status ON promotions(status)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_promotions_book ON promotions(sportsbook)")
    for table in ("promotion_sources", "promotion_terms", "promotion_eligibility",
                  "promotion_actions", "promotion_rewards", "promotion_matches"):
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_promo ON {table}(promotion_id)"
        )


def _provenance_columns(connection) -> None:
    columns = {
        "bets": (("source_type", "TEXT"), ("is_live", "INTEGER"), ("provenance", "TEXT")),
        "opportunities": (
            ("source_type", "TEXT"),
            ("is_live", "INTEGER"),
            ("liquidity_snapshot_id", "TEXT"),
            ("provenance", "TEXT"),
        ),
    }
    for table, additions in columns.items():
        for name, declaration in additions:
            if not _column_exists(connection, table, name):
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def _settlement_tables(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS prediction_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            bet_id INTEGER NOT NULL UNIQUE,
            sportsbook TEXT, exchange TEXT, event TEXT, market TEXT, selection TEXT,
            predicted_back_stake REAL, predicted_back_odds REAL,
            predicted_lay_contracts REAL, predicted_lay_odds REAL,
            predicted_liability REAL, predicted_exchange_fee REAL,
            predicted_back_win_pnl REAL, predicted_lay_win_pnl REAL,
            predicted_total_capital REAL, predicted_qualifying_loss REAL,
            max_price REAL, fee_factor REAL,
            calculation_version TEXT, fee_model_version TEXT,
            source_provenance TEXT, payload_json TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            bet_id INTEGER NOT NULL,
            settlement_key TEXT UNIQUE,
            source TEXT,
            outcome TEXT,
            actual_back_stake REAL, actual_back_odds REAL,
            actual_lay_contracts REAL, actual_fill_prices TEXT,
            actual_liability REAL, actual_exchange_fee REAL,
            actual_sportsbook_settlement REAL, actual_exchange_settlement REAL,
            actual_total_pnl REAL, settled_at TEXT, payload_json TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS reconciliations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            bet_id INTEGER NOT NULL,
            prediction_id INTEGER,
            settlement_id INTEGER,
            status TEXT,
            predicted_pnl REAL, actual_pnl REAL, pnl_difference REAL,
            fee_difference REAL, liability_difference REAL, contracts_difference REAL,
            flags TEXT, message TEXT, payload_json TEXT,
            UNIQUE(prediction_id, settlement_id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_settlements_bet ON settlements(bet_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_reconciliations_bet ON reconciliations(bet_id)"
    )


def _promotion_verification(connection) -> None:
    promotion_columns = (
        ("lifecycle_status", "TEXT"),
        ("official_source_url", "TEXT"),
        ("terms_source_url", "TEXT"),
        ("terms_verified_at", "TEXT"),
        ("effective_date", "TEXT"),
        ("withdrawal_restrictions", "TEXT"),
        ("wagering_requirement_text", "TEXT"),
        ("min_deposit", "REAL"),
        ("max_qualifying_stake", "REAL"),
    )
    for name, declaration in promotion_columns:
        if not _column_exists(connection, "promotions", name):
            connection.execute(f"ALTER TABLE promotions ADD COLUMN {name} {declaration}")

    source_columns = (
        ("retrieved_at", "TEXT"),
        ("source_type", "TEXT"),
        ("jurisdiction", "TEXT"),
        ("effective_date", "TEXT"),
        ("expiry_date", "TEXT"),
        ("version_hash", "TEXT"),
    )
    for name, declaration in source_columns:
        if not _column_exists(connection, "promotion_sources", name):
            connection.execute(f"ALTER TABLE promotion_sources ADD COLUMN {name} {declaration}")

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS promotion_reward_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            promotion_id INTEGER NOT NULL,
            token_key TEXT UNIQUE,
            face_value REAL,
            reward_type TEXT,
            eligible_markets TEXT,
            received_at TEXT,
            expires_at TEXT,
            used_at TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            linked_qualifying_bet_id INTEGER,
            linked_conversion_bet_id INTEGER,
            realized_value REAL,
            note TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS promotion_lifecycle_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promotion_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            previous_status TEXT,
            new_status TEXT,
            note TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_reward_tokens_promo ON promotion_reward_tokens(promotion_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_lifecycle_history_promo ON promotion_lifecycle_history(promotion_id)"
    )


def _reservations_table(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS bankroll_reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            promotion_id INTEGER,
            bet_id INTEGER,
            account TEXT NOT NULL,
            kind TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'RESERVED',
            note TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_reservations_account ON bankroll_reservations(account)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_reservations_status ON bankroll_reservations(status)"
    )


def _promotion_workflow_column(connection) -> None:
    if not _column_exists(connection, "promotions", "workflow_state"):
        connection.execute("ALTER TABLE promotions ADD COLUMN workflow_state TEXT")


MIGRATIONS = (
    (1, "baseline tables", _baseline),
    (2, "bet selection and source", _add_bet_selection_and_source),
    (3, "offers table", _offers_table),
    (4, "alert rules and history", _alert_tables),
    (5, "bet sport", _add_bet_sport),
    (6, "history and reference tables", _history_tables),
    (7, "bet lay fee", _add_bet_lay_fee),
    (8, "promotion tables", _promotion_tables),
    (9, "provenance columns", _provenance_columns),
    (10, "settlement reconciliation tables", _settlement_tables),
    (11, "promotion verification and reward tokens", _promotion_verification),
    (12, "bankroll reservations", _reservations_table),
    (13, "promotion workflow state", _promotion_workflow_column),
)


def applied_versions(connection) -> set:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    return {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}


def migrate(db_path=DB_PATH) -> None:
    with get_connection(db_path) as connection:
        applied = applied_versions(connection)
        for version, description, apply in MIGRATIONS:
            if version in applied:
                continue
            apply(connection)
            connection.execute(
                "INSERT INTO schema_migrations(version, description, applied_at) VALUES (?,?,?)",
                (version, description, datetime.now().isoformat(timespec="seconds")),
            )
