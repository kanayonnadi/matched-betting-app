from datetime import datetime, timezone
from decimal import Decimal

from analytics import (
    analyze_bets,
    commission_cost,
    expected_profit,
    is_free_bet,
    overview,
    profit_by_bookmaker,
    profit_by_month,
    profit_by_sport,
)

NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)


def bet(profit_back, profit_lay, **overrides):
    row = {
        "profit_if_back": profit_back,
        "profit_if_lay": profit_lay,
        "back_stake": 100.0,
        "liability": 100.0,
        "lay_stake": 100.0,
        "commission": 2.0,
        "bet_type": "Qualifying bet",
        "bookmaker": "Book A",
        "sport": "basketball",
        "event_date": "2026-10-15",
        "created_at": "2026-10-15T10:00:00",
    }
    row.update(overrides)
    return row


def test_expected_profit_is_average():
    assert expected_profit(bet(-4.0, -4.0)) == Decimal("-4.0")


def test_is_free_bet():
    assert is_free_bet(bet(0, 0, bet_type="Free bet (stake not returned)"))
    assert not is_free_bet(bet(0, 0, bet_type="Qualifying bet"))


def test_analyze_bets():
    rows = [
        bet(-4.0, -4.0),
        bet(-2.0, -2.0),
        bet(20.0, 20.0, bet_type="Free bet (stake not returned)"),
    ]
    analytics = analyze_bets(rows)
    assert analytics.bet_count == 3
    assert analytics.matched_profit == Decimal("14.0")
    assert analytics.qualifying_losses == Decimal("6.0")
    assert analytics.free_bet_profit == Decimal("20.0")
    assert analytics.exchange_commissions == Decimal("6.0")  # 3 x (100 * 2%)
    assert analytics.capital_deployed == Decimal("600.0")
    assert analytics.return_on_capital > 0


def test_grouping():
    rows = [
        bet(-4.0, -4.0, bookmaker="Book A", sport="basketball"),
        bet(-2.0, -2.0, bookmaker="Book B", sport="soccer"),
    ]
    assert profit_by_bookmaker(rows) == {"Book A": Decimal("-4.0"), "Book B": Decimal("-2.0")}
    assert profit_by_sport(rows) == {"basketball": Decimal("-4.0"), "soccer": Decimal("-2.0")}
    assert profit_by_month(rows) == {"2026-10": Decimal("-6.0")}


def test_overview():
    rows = [
        bet(-4.0, -4.0),
        bet(20.0, 20.0, bet_type="Free bet (stake not returned)"),
    ]
    offers = [
        {
            "name": "Sign up",
            "status": "Reward received",
            "reward": 50.0,
            "reward_conversion": 75.0,
            "max_qualifying_loss": 4.0,
            "bet_id": None,
        }
    ]
    result = overview(rows, offers, now=NOW)
    assert result.matched_profit == Decimal("16.0")
    assert result.gross_rewards == Decimal("50.0")
    assert result.converted_rewards == Decimal("37.5")
    assert result.average_qualifying_loss == Decimal("4.0")
    assert result.average_free_bet_conversion == Decimal("20.0")
    assert result.net_profit == Decimal("16.0")
    assert result.return_on_capital > 0


def test_commission_cost_prefers_recorded_lay_fee():
    assert commission_cost({"lay_fee": 3.99, "lay_stake": 100.0, "commission": 2.0}) == Decimal("3.99")
    assert commission_cost({"lay_stake": 100.0, "commission": 2.0}) == Decimal("2.0")


def test_profit_30d_window():
    rows = [
        bet(-4.0, -4.0, event_date="2026-10-25"),
        bet(-2.0, -2.0, event_date="2026-01-01"),
    ]
    result = overview(rows, [], now=NOW)
    assert result.profit_30d == Decimal("-4.0")
    assert result.lifetime_profit == Decimal("-6.0")
