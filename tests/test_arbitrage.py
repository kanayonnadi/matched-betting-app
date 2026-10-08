from decimal import Decimal

import pytest

from calculators import calculate_arbitrage
from opportunities import discover_arbitrage
from providers import MockSecondSportsbookProvider, MockSportsbookProvider

TOL = Decimal("1e-9")


def test_no_arbitrage_when_sum_of_inverses_at_least_one():
    assert calculate_arbitrage([Decimal("2.0"), Decimal("2.0")], 100) is None
    assert calculate_arbitrage([Decimal("1.8"), Decimal("2.0")], 100) is None


def test_known_two_way_arbitrage_equalises_payout():
    result = calculate_arbitrage([Decimal("2.15"), Decimal("2.10")], 100)
    assert result is not None
    assert result.sum_inverses < 1
    assert result.profit > 0
    assert result.roi_percent > 0
    for stake, odds in zip(result.stakes, result.outcome_odds):
        assert abs(stake * odds - result.payout) < TOL


def test_three_way_arbitrage():
    result = calculate_arbitrage([Decimal("3.0"), Decimal("3.5"), Decimal("3.2")], 300)
    assert result is not None
    assert len(result.stakes) == 3
    assert abs(sum(result.stakes) - Decimal("300")) < TOL


def test_invalid_inputs_rejected():
    with pytest.raises(ValueError):
        calculate_arbitrage([Decimal("2.0")], 100)
    with pytest.raises(ValueError):
        calculate_arbitrage([Decimal("1.0"), Decimal("2.0")], 100)
    with pytest.raises(ValueError):
        calculate_arbitrage([Decimal("2.0"), Decimal("3.0")], 0)


def test_single_book_has_no_arbitrage():
    opportunities = discover_arbitrage(
        [MockSportsbookProvider()], ["basketball", "soccer"], 100
    )
    assert opportunities == []


def test_detects_cross_book_arbitrage():
    opportunities = discover_arbitrage(
        [MockSportsbookProvider(), MockSecondSportsbookProvider()],
        ["basketball", "soccer"],
        100,
    )
    assert len(opportunities) == 1
    arb = opportunities[0]
    assert arb.event_label == "New York Knicks vs Toronto Raptors"
    assert arb.roi_percent > 0
    assert {leg.provider for leg in arb.legs} == {"mock_book", "mock_book_b"}
    assert abs(sum(leg.stake for leg in arb.legs) - arb.total_stake) < TOL
