import pytest

from opportunities import (
    BetKind,
    OpportunityFilter,
    discover_mock_opportunities,
)


@pytest.fixture
def opportunities():
    return discover_mock_opportunities(100)


def test_filter_by_sport(opportunities):
    result = OpportunityFilter(sport="soccer").apply(opportunities)
    assert result
    assert all(o.sport == "soccer" for o in result)


def test_filter_by_bookmaker_and_exchange(opportunities):
    result = OpportunityFilter(
        bookmaker="mock_book", exchange="mock_exchange"
    ).apply(opportunities)
    assert len(result) == len(opportunities)

    assert OpportunityFilter(bookmaker="other").apply(opportunities) == []


def test_filter_max_qualifying_loss_pct(opportunities):
    result = OpportunityFilter(max_qualifying_loss_pct=3.5).apply(opportunities)
    assert result
    assert all(o.qualifying_loss_pct <= 3.5 for o in result)
    assert len(result) < len(opportunities)


def test_filter_max_liability(opportunities):
    result = OpportunityFilter(max_liability=200).apply(opportunities)
    assert result
    assert all(o.lay_liability <= 200 for o in result)


def test_filter_min_back_odds(opportunities):
    result = OpportunityFilter(min_back_odds=2.0).apply(opportunities)
    assert result
    assert all(o.back_odds >= 2.0 for o in result)


def test_filter_kind(opportunities):
    qualifying = OpportunityFilter(kind=BetKind.QUALIFYING).apply(opportunities)
    assert len(qualifying) == len(opportunities)
    assert OpportunityFilter(kind=BetKind.FREE_BET_SNR).apply(opportunities) == []


def test_filter_confirmed_only(opportunities):
    result = OpportunityFilter(confirmed_only=True).apply(opportunities)
    assert len(result) == len(opportunities)


def test_filter_only_liquid_drops_illiquid(opportunities):
    small = [o for o in opportunities if not o.liquidity_sufficient]
    result = OpportunityFilter(only_liquid=True).apply(opportunities)
    assert all(o.liquidity_sufficient for o in result)
    if small:
        assert len(result) == len(opportunities) - len(small)
