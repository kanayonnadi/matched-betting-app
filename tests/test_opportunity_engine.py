from datetime import datetime, timezone
from decimal import Decimal

import pytest

from calculators import calculate_free_bet_snr, calculate_qualifying
from models import ExchangeOdds, NormalizedOdds, Side
from opportunities import BetKind, build_opportunity

START = datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def book_quote(odds="2.10", **overrides):
    data = dict(
        provider="mock_book",
        event_id="kb-1001",
        sport="basketball",
        league="NBA",
        home_team="New York Knicks",
        away_team="Toronto Raptors",
        start_time=START,
        market="moneyline",
        selection="Toronto Raptors",
        side=Side.BACK,
        decimal_odds=Decimal(odds),
        timestamp=NOW,
    )
    data.update(overrides)
    return NormalizedOdds(**data)


def exchange_quote(odds="2.16", size="500.00", commission="0.02", **overrides):
    data = dict(
        provider="mock_exchange",
        event_id="kx-1001",
        sport="basketball",
        league="NBA",
        home_team="Toronto Raptors",
        away_team="New York Knicks",
        start_time=START,
        market="moneyline",
        selection="Toronto Raptors",
        side=Side.LAY,
        decimal_odds=Decimal(odds),
        timestamp=NOW,
        available_size=Decimal(size),
        commission=Decimal(commission),
    )
    data.update(overrides)
    return ExchangeOdds(**data)


def test_qualifying_opportunity_matches_calculator():
    opp = build_opportunity(book_quote(), exchange_quote(), 100)
    expected = calculate_qualifying(100, Decimal("2.10"), Decimal("2.16"), Decimal("0.02"))

    assert opp.kind is BetKind.QUALIFYING
    assert opp.lay_stake == expected.lay_stake
    assert opp.lay_liability == expected.liability
    assert opp.expected_profit == expected.expected_profit
    assert opp.qualifying_loss == expected.qualifying_loss
    assert opp.qualifying_loss_pct == expected.qualifying_loss_pct
    assert opp.required_capital == expected.required_capital
    assert opp.conversion_rate is None


def test_qualifying_is_a_loss_and_capital_includes_liability():
    opp = build_opportunity(book_quote(), exchange_quote(), 100)
    assert opp.expected_profit < 0
    assert opp.qualifying_loss > 0
    assert opp.required_capital == opp.stake + opp.lay_liability
    assert opp.rating_percent == opp.back_odds / opp.lay_odds * Decimal("100")


def test_qualifying_fields_are_decimal():
    opp = build_opportunity(book_quote(), exchange_quote(), 100)
    for value in (
        opp.back_odds,
        opp.lay_odds,
        opp.commission,
        opp.stake,
        opp.lay_stake,
        opp.lay_liability,
        opp.expected_profit,
        opp.qualifying_loss,
        opp.qualifying_loss_pct,
        opp.required_capital,
        opp.rating_percent,
    ):
        assert isinstance(value, Decimal)


def test_free_bet_opportunity_uses_snr_formula():
    opp = build_opportunity(
        book_quote("5.0"), exchange_quote("5.0"), 10, kind=BetKind.FREE_BET_SNR
    )
    expected = calculate_free_bet_snr(10, Decimal("5.0"), Decimal("5.0"), Decimal("0.02"))

    assert opp.kind is BetKind.FREE_BET_SNR
    assert opp.lay_stake == expected.lay_stake
    assert opp.expected_profit == expected.expected_profit
    assert opp.conversion_rate == expected.conversion_rate
    assert opp.qualifying_loss == Decimal("0")
    assert opp.qualifying_loss_pct == Decimal("0")
    assert opp.required_capital == expected.liability


def test_free_bet_accepts_string_kind():
    opp = build_opportunity(book_quote("5.0"), exchange_quote("5.0"), 10, kind="free_bet_snr")
    assert opp.kind is BetKind.FREE_BET_SNR


def test_quote_timestamp_is_the_oldest():
    older = datetime(2026, 11, 1, 11, 59, tzinfo=timezone.utc)
    opp = build_opportunity(book_quote(timestamp=older), exchange_quote(), 100)
    assert opp.timestamp == older


def test_liquidity_sufficient_flag():
    funded = build_opportunity(book_quote(), exchange_quote(size="500.00"), 100)
    assert funded.liquidity_sufficient is True

    illiquid = build_opportunity(book_quote(), exchange_quote(size="50.00"), 100)
    assert illiquid.liquidity_sufficient is False


def test_event_label_defaults_and_override():
    opp = build_opportunity(book_quote(), exchange_quote(), 100)
    assert opp.event_label == "New York Knicks vs Toronto Raptors"
    override = build_opportunity(book_quote(), exchange_quote(), 100, event_label="Knicks @ Raptors")
    assert override.event_label == "Knicks @ Raptors"


def test_wrong_book_side_rejected():
    with pytest.raises(ValueError):
        build_opportunity(book_quote(side=Side.LAY), exchange_quote(), 100)


def test_non_exchange_lay_quote_rejected():
    bad_lay = NormalizedOdds(
        provider="book",
        event_id="kb-1001",
        sport="basketball",
        league="NBA",
        home_team="Knicks",
        away_team="Raptors",
        start_time=START,
        market="moneyline",
        selection="Raptors",
        side=Side.LAY,
        decimal_odds=Decimal("2.16"),
        timestamp=NOW,
    )
    with pytest.raises(ValueError):
        build_opportunity(book_quote(), bad_lay, 100)


def test_invalid_stake_rejected():
    with pytest.raises(ValueError):
        build_opportunity(book_quote(), exchange_quote(), 0)


def test_per_wager_fee_exchange_uses_fee_model():
    per_wager_exchange = ExchangeOdds(
        provider="stx",
        event_id="kx-1001",
        sport="basketball",
        league="NBA",
        home_team="Toronto Raptors",
        away_team="New York Knicks",
        start_time=START,
        market="moneyline",
        selection="Toronto Raptors",
        side=Side.LAY,
        decimal_odds=Decimal("2.0"),
        timestamp=NOW,
        available_size=Decimal("500"),
        commission=Decimal("0"),
        fee_model="per_wager",
        fee_factor=Decimal("0.10"),
        max_price=Decimal("1"),
    )
    opp = build_opportunity(book_quote("2.0"), per_wager_exchange, 100)
    assert opp.fee_model == "per_wager"
    assert opp.lay_stake == Decimal("100")  # no commission in denominator
    assert opp.lay_fee == Decimal("5")  # 0.10 * 100 * (2-1)/2
    assert opp.expected_profit == Decimal("-5")
