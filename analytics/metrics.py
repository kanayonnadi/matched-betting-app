"""Matched-betting and promotion analytics.

Derived entirely from the persisted bet log, offer tracker and bankroll ledger.
Profit is the guaranteed matched result of each bet (the average of the two
outcomes), which already includes free-bet conversion and commission effects.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from offers import summarize as summarize_offers

HUNDRED = Decimal("100")
ZERO = Decimal("0")
FREE_BET_MARKER = "free bet"


def _decimal(value) -> Decimal:
    if value is None or value == "":
        return ZERO
    return Decimal(str(value))


def _get(row, key, default=None):
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def expected_profit(row) -> Decimal:
    return (
        _decimal(_get(row, "profit_if_back")) + _decimal(_get(row, "profit_if_lay"))
    ) / Decimal("2")


def is_free_bet(row) -> bool:
    return FREE_BET_MARKER in (_get(row, "bet_type", "") or "").lower()


def commission_cost(row) -> Decimal:
    """Exchange cost: the recorded per-wager fee if present, else commission."""
    fee = _get(row, "lay_fee")
    if fee is not None:
        return _decimal(fee)
    return _decimal(_get(row, "lay_stake")) * _decimal(_get(row, "commission")) / HUNDRED


def capital(row) -> Decimal:
    return _decimal(_get(row, "back_stake")) + _decimal(_get(row, "liability"))


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


@dataclass(frozen=True)
class BetAnalytics:
    bet_count: int
    matched_profit: Decimal
    qualifying_losses: Decimal
    free_bet_profit: Decimal
    exchange_commissions: Decimal
    capital_deployed: Decimal
    return_on_capital: Decimal


def analyze_bets(bet_rows) -> BetAnalytics:
    rows = list(bet_rows)
    matched = ZERO
    qualifying_losses = ZERO
    free_profit = ZERO
    commissions = ZERO
    deployed = ZERO
    for row in rows:
        profit = expected_profit(row)
        matched += profit
        commissions += commission_cost(row)
        deployed += capital(row)
        if is_free_bet(row):
            free_profit += profit
        elif profit < 0:
            qualifying_losses += -profit
    roc = matched / deployed * HUNDRED if deployed > 0 else ZERO
    return BetAnalytics(
        bet_count=len(rows),
        matched_profit=matched,
        qualifying_losses=qualifying_losses,
        free_bet_profit=free_profit,
        exchange_commissions=commissions,
        capital_deployed=deployed,
        return_on_capital=roc,
    )


def _group_profit(rows, key) -> dict:
    groups = {}
    for row in rows:
        label = _get(row, key) or "Unknown"
        groups[label] = groups.get(label, ZERO) + expected_profit(row)
    return groups


def profit_by_bookmaker(bet_rows) -> dict:
    return _group_profit(bet_rows, "bookmaker")


def profit_by_sport(bet_rows) -> dict:
    return _group_profit(bet_rows, "sport")


def profit_by_month(bet_rows) -> dict:
    groups = {}
    for row in bet_rows:
        month = str(_get(row, "event_date") or _get(row, "created_at") or "")[:7]
        groups[month] = groups.get(month, ZERO) + expected_profit(row)
    return groups


def profit_by_promotion(offer_rows, bets_by_id=None) -> dict:
    bets_by_id = bets_by_id or {}
    groups = {}
    for offer in offer_rows:
        name = _get(offer, "name") or "Unknown"
        bet_id = _get(offer, "bet_id")
        if bet_id is not None and bet_id in bets_by_id:
            groups[name] = groups.get(name, ZERO) + expected_profit(bets_by_id[bet_id])
        else:
            groups.setdefault(name, ZERO)
    return groups


@dataclass(frozen=True)
class Overview:
    lifetime_profit: Decimal
    profit_30d: Decimal
    matched_profit: Decimal
    arbitrage_profit: Decimal
    qualifying_losses: Decimal
    exchange_commissions: Decimal
    gross_rewards: Decimal
    converted_rewards: Decimal
    net_profit: Decimal
    capital_deployed: Decimal
    return_on_capital: Decimal
    average_qualifying_loss: Decimal
    average_free_bet_conversion: Decimal
    capital_efficiency: Decimal


def overview(bet_rows, offer_rows, bets_by_id=None, arbitrage_profit=ZERO, now=None) -> Overview:
    rows = list(bet_rows)
    bets = analyze_bets(rows)
    offers = summarize_offers(offer_rows, bets_by_id)
    moment = now or datetime.now(timezone.utc)
    cutoff = moment.date() - timedelta(days=30)

    profit_30d = sum(
        (
            expected_profit(row)
            for row in rows
            if _parse_date(_get(row, "event_date") or _get(row, "created_at")) is not None
            and _parse_date(_get(row, "event_date") or _get(row, "created_at")) >= cutoff
        ),
        ZERO,
    )

    qualifying_rows = [row for row in rows if not is_free_bet(row)]
    if qualifying_rows:
        average_qualifying_loss = sum(
            max(-expected_profit(row), ZERO) for row in qualifying_rows
        ) / len(qualifying_rows)
    else:
        average_qualifying_loss = ZERO

    conversions = []
    for row in rows:
        if not is_free_bet(row):
            continue
        stake = _decimal(_get(row, "back_stake"))
        if stake > 0:
            conversions.append(expected_profit(row) / stake * HUNDRED)
    average_free_bet_conversion = (
        sum(conversions, ZERO) / len(conversions) if conversions else ZERO
    )

    net_profit = bets.matched_profit + arbitrage_profit
    capital_efficiency = (
        net_profit / bets.capital_deployed * HUNDRED if bets.capital_deployed > 0 else ZERO
    )

    return Overview(
        lifetime_profit=bets.matched_profit,
        profit_30d=profit_30d,
        matched_profit=bets.matched_profit,
        arbitrage_profit=arbitrage_profit,
        qualifying_losses=bets.qualifying_losses,
        exchange_commissions=bets.exchange_commissions,
        gross_rewards=offers.rewards_received,
        converted_rewards=offers.converted_profit,
        net_profit=net_profit,
        capital_deployed=bets.capital_deployed,
        return_on_capital=bets.return_on_capital,
        average_qualifying_loss=average_qualifying_loss,
        average_free_bet_conversion=average_free_bet_conversion,
        capital_efficiency=capital_efficiency,
    )
