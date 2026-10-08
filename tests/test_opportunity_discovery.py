from decimal import Decimal

from opportunities import (
    BetKind,
    default_mock_sports,
    discover_mock_opportunities,
    discover_opportunities,
)
from providers import MockExchangeProvider, MockSportsbookProvider


def test_default_sports_cover_mock_catalogue():
    assert default_mock_sports() == ["basketball", "soccer"]


def test_discover_all_mock_opportunities():
    opportunities = discover_mock_opportunities(100)
    assert len(opportunities) == 9  # 3 basketball games x 2 + 1 soccer x 3
    assert all(o.kind is BetKind.QUALIFYING for o in opportunities)
    assert all(o.event_confidence is not None and o.event_confidence >= 0.95 for o in opportunities)
    assert all(o.market_confidence is not None and o.market_confidence >= 0.95 for o in opportunities)


def test_discovery_populates_event_metadata():
    opportunities = discover_mock_opportunities(100, sports=["basketball"])
    sample = next(o for o in opportunities if o.event_id == "kb-1001")
    assert sample.sport == "basketball"
    assert sample.league == "NBA"
    assert sample.start_time is not None
    assert sample.swapped is False
    assert sample.event_label == "New York Knicks vs Toronto Raptors"


def test_discovery_subset_of_sports():
    basketball = discover_mock_opportunities(100, sports=["basketball"])
    soccer = discover_mock_opportunities(100, sports=["soccer"])
    assert len(basketball) == 6
    assert len(soccer) == 3


def test_discovery_free_bet_kind():
    opportunities = discover_mock_opportunities(10, sports=["basketball"], kind=BetKind.FREE_BET_SNR)
    assert all(o.kind is BetKind.FREE_BET_SNR for o in opportunities)
    assert all(o.conversion_rate is not None for o in opportunities)


def test_discover_with_explicit_providers():
    opportunities = discover_opportunities(
        MockSportsbookProvider(), MockExchangeProvider(), ["soccer"], 100
    )
    assert len(opportunities) == 3
    assert all(o.sport == "soccer" for o in opportunities)
