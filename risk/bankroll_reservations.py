"""Transaction-safe bankroll reservations.

Capital is reserved per account and cannot be double-allocated. Sportsbook
stakes and STX collateral are tracked separately — funds at a sportsbook are not
assumed to be transferable to the exchange.
"""

from decimal import Decimal

from bankroll import account_balances
from database import (
    create_reservation,
    list_reservations,
    list_reservations_for_bet,
    set_reservation_status,
    update_reservation_amount,
)

from .models import AvailableCapital

ZERO = Decimal("0")

SPORTSBOOK_STAKE = "SPORTSBOOK_STAKE"
EXCHANGE_COLLATERAL = "EXCHANGE_COLLATERAL"


def reserve(account, kind, amount, bet_id=None, promotion_id=None, note=None, db_path=None):
    kwargs = dict(bet_id=bet_id, promotion_id=promotion_id, note=note)
    if db_path is not None:
        kwargs["db_path"] = db_path
    return create_reservation(account, kind, amount, **kwargs)


def release(reservation_id, db_path=None):
    if db_path is not None:
        set_reservation_status(reservation_id, "RELEASED", db_path=db_path)
    else:
        set_reservation_status(reservation_id, "RELEASED")


def settle(reservation_id, db_path=None):
    if db_path is not None:
        set_reservation_status(reservation_id, "SETTLED", db_path=db_path)
    else:
        set_reservation_status(reservation_id, "SETTLED")


def available_capital(bankroll_rows, reservations=None) -> AvailableCapital:
    balances = account_balances(bankroll_rows)
    if reservations is None:
        reservations = list_reservations(status="RESERVED")
    reserved = {}
    for row in reservations:
        amount = Decimal(str(row["amount"] or 0))
        reserved[row["account"]] = reserved.get(row["account"], ZERO) + amount
    accounts = set(balances) | set(reserved)
    available = {
        account: balances.get(account, ZERO) - reserved.get(account, ZERO)
        for account in accounts
    }
    return AvailableCapital(
        balances=balances,
        reserved=reserved,
        available=available,
        total_available=sum(available.values(), ZERO),
        total_reserved=sum(reserved.values(), ZERO),
    )


def can_fund(account, amount, state: AvailableCapital) -> bool:
    return state.available.get(account, ZERO) >= Decimal(str(amount))


def reserve_for_bet(
    bet_id, book_provider, exchange_provider, back_stake, liability,
    promotion_id=None, db_path=None,
) -> list:
    """Reserve the sportsbook stake and exchange collateral for a bet."""
    ids = []
    back = Decimal(str(back_stake or 0))
    collateral = Decimal(str(liability or 0))
    if back > ZERO and book_provider:
        ids.append(
            reserve(book_provider, SPORTSBOOK_STAKE, back, bet_id=bet_id,
                    promotion_id=promotion_id, db_path=db_path)
        )
    if collateral > ZERO and exchange_provider:
        ids.append(
            reserve(exchange_provider, EXCHANGE_COLLATERAL, collateral, bet_id=bet_id,
                    promotion_id=promotion_id, db_path=db_path)
        )
    return ids


def update_exchange_reservation(reservation_id, amount, db_path=None) -> None:
    if db_path is not None:
        update_reservation_amount(reservation_id, amount, db_path=db_path)
    else:
        update_reservation_amount(reservation_id, amount)


def reservations_for_bet(bet_id, db_path=None) -> list:
    if db_path is not None:
        return list_reservations_for_bet(bet_id, db_path=db_path)
    return list_reservations_for_bet(bet_id)


def release_for_bet(bet_id, db_path=None) -> int:
    """Release all still-RESERVED reservations for a bet. Idempotent."""
    released = 0
    for row in reservations_for_bet(bet_id, db_path=db_path):
        if row["status"] == "RESERVED":
            if db_path is not None:
                set_reservation_status(row["id"], "RELEASED", db_path=db_path)
            else:
                set_reservation_status(row["id"], "RELEASED")
            released += 1
    return released
