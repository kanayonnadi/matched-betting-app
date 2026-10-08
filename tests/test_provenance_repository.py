import json

import pytest

from database import get_connection, init_db, insert_bet, record_opportunity
from opportunities import discover_mock_opportunities


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "prov.db"
    init_db(path)
    return path


def test_provenance_columns_exist(db_path):
    with get_connection(db_path) as connection:
        bets_columns = {row[1] for row in connection.execute("PRAGMA table_info(bets)")}
        opp_columns = {row[1] for row in connection.execute("PRAGMA table_info(opportunities)")}
    assert {"source_type", "is_live", "provenance"} <= bets_columns
    assert {"source_type", "is_live", "liquidity_snapshot_id", "provenance"} <= opp_columns


def test_insert_bet_persists_provenance(db_path):
    insert_bet(
        event="E",
        source_type="MOCK",
        is_live=False,
        provenance=json.dumps({"source_type": "MOCK"}),
        db_path=db_path,
    )
    with get_connection(db_path) as connection:
        row = connection.execute("SELECT source_type, is_live, provenance FROM bets").fetchone()
    assert row["source_type"] == "MOCK"
    assert row["is_live"] == 0
    assert "MOCK" in row["provenance"]


def test_record_opportunity_writes_provenance(db_path):
    opportunity = discover_mock_opportunities(100)[0]
    record_opportunity(opportunity, db_path=db_path)
    with get_connection(db_path) as connection:
        row = connection.execute(
            "SELECT source_type, is_live, provenance, liquidity_snapshot_id FROM opportunities"
        ).fetchone()
    assert row["source_type"] == "MOCK"
    assert row["is_live"] == 0
    assert row["provenance"] is not None
