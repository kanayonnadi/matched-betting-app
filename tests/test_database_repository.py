import pytest

from database import (
    add_bankroll_entry,
    init_db,
    insert_bet,
    list_bankroll,
    list_bets,
    update_bet_status,
)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(path)
    return path


def test_init_db_creates_tables(db_path):
    bets = list_bets(db_path)
    bank = list_bankroll(db_path)
    assert bets == []
    assert bank == []


def test_insert_and_list_bet(db_path):
    bet_id = insert_bet(
        event="Knicks vs Raptors",
        bookmaker="Book A",
        exchange="Exchange",
        market="moneyline",
        bet_type="Qualifying bet",
        back_stake=100.0,
        back_odds=2.10,
        lay_odds=2.16,
        commission=2.0,
        lay_stake=97.2,
        liability=112.7,
        profit_if_back=-12.7,
        profit_if_lay=-12.5,
        db_path=db_path,
    )
    rows = list_bets(db_path)
    assert len(rows) == 1
    assert rows[0]["id"] == bet_id
    assert rows[0]["event"] == "Knicks vs Raptors"
    assert rows[0]["status"] == "Open"


def test_update_bet_status(db_path):
    bet_id = insert_bet(event="Test", db_path=db_path)
    update_bet_status(bet_id, "Won at exchange", db_path=db_path)
    assert list_bets(db_path)[0]["status"] == "Won at exchange"


def test_insert_and_list_bankroll(db_path):
    add_bankroll_entry("Book A", 300.0, "deposit", db_path=db_path)
    add_bankroll_entry("Book A", -50.0, "withdrawal", db_path=db_path)
    rows = list_bankroll(db_path)
    assert len(rows) == 2
    assert sum(row["amount"] for row in rows) == 250.0
