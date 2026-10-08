"""Bankroll tracking and capital deployment.

Balances come from the bankroll ledger; capital committed is derived from the
open bet log (back stake + exchange liability). ``free_capital`` is what is left
to fund new opportunities.
"""

from dataclasses import dataclass
from decimal import Decimal

OPEN_STATUS = "Open"
ZERO = Decimal("0")


def _decimal(value) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(str(value))


def account_balances(rows) -> dict:
    balances = {}
    for row in rows:
        account = row["account"]
        balances[account] = balances.get(account, ZERO) + _decimal(row["amount"])
    return balances


def capital_committed(bet_rows) -> Decimal:
    total = ZERO
    for row in bet_rows:
        if (row["status"] or "") != OPEN_STATUS:
            continue
        total += _decimal(row["back_stake"]) + _decimal(row["liability"])
    return total


@dataclass(frozen=True)
class BankrollSummary:
    balances: dict
    total: Decimal
    capital_committed: Decimal
    free_capital: Decimal


def summarize(bankroll_rows, bet_rows) -> BankrollSummary:
    balances = account_balances(bankroll_rows)
    total = sum(balances.values(), ZERO)
    committed = capital_committed(bet_rows)
    return BankrollSummary(balances, total, committed, total - committed)
