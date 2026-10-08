from decimal import Decimal

from bankroll import account_balances, capital_committed, summarize

BANKROLL_ROWS = [
    {"account": "Book A", "amount": 300.0},
    {"account": "Book A", "amount": -50.0},
    {"account": "Exchange", "amount": 150.0},
]

BET_ROWS = [
    {"status": "Open", "back_stake": 100.0, "liability": 112.7},
    {"status": "Won at exchange", "back_stake": 100.0, "liability": 112.7},
]


def test_account_balances_are_summed_and_typed():
    balances = account_balances(BANKROLL_ROWS)
    assert balances["Book A"] == Decimal("250")
    assert balances["Exchange"] == Decimal("150")
    assert all(isinstance(value, Decimal) for value in balances.values())


def test_account_balances_handle_none():
    balances = account_balances([{"account": "A", "amount": None}])
    assert balances["A"] == Decimal("0")


def test_capital_committed_counts_only_open_bets():
    assert capital_committed(BET_ROWS) == Decimal("212.7")


def test_summarize_free_capital():
    summary = summarize(BANKROLL_ROWS, BET_ROWS)
    assert summary.total == Decimal("400")
    assert summary.capital_committed == Decimal("212.7")
    assert summary.free_capital == Decimal("400") - Decimal("212.7")


def test_summarize_empty():
    summary = summarize([], [])
    assert summary.total == Decimal("0")
    assert summary.free_capital == Decimal("0")
