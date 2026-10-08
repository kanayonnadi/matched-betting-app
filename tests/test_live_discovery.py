from datetime import datetime, timezone

from matching import EventMatcher, normalize_sport
from models import Event
from opportunities import discover_live_opportunities
from providers import (
    MockExchangeProvider,
    MockSportsbookProvider,
    SportRoute,
    routes_by_canonical,
    select_routes,
)

START = datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc)


def test_normalize_sport_aliases():
    assert normalize_sport("Hockey") == "ice_hockey"
    assert normalize_sport("ice_hockey") == "ice_hockey"
    assert normalize_sport("Football") == "american_football"
    assert normalize_sport("american_football") == "american_football"
    assert normalize_sport("Basketball") == "basketball"


def test_event_matcher_accepts_alias_sports():
    book = Event("book", "1", "ice_hockey", "NHL", "Winnipeg Jets", "Colorado Avalanche", START)
    exchange = Event("stx", "2", "Hockey", "NHL", "Winnipeg Jets", "Colorado Avalanche", START)
    assert EventMatcher().match(book, exchange) is not None


def test_route_helpers():
    mapping = routes_by_canonical()
    assert mapping["ice_hockey"].stx_sport == "Hockey"
    selected = select_routes(["baseball"])
    assert len(selected) == 1
    assert selected[0].odds_api_keys == ("baseball_mlb",)


def test_live_discovery_with_route():
    route = SportRoute("basketball", "Basketball", ("basketball",), "basketball")
    opportunities = discover_live_opportunities(
        MockSportsbookProvider(), MockExchangeProvider(), [route], 100
    )
    assert len(opportunities) == 6  # 3 basketball games x 2 selections


class FlakyBook(MockSportsbookProvider):
    def get_events(self, sport):
        if sport == "bad-key":
            raise RuntimeError("boom")
        return super().get_events(sport)


def test_live_discovery_isolates_provider_errors():
    route = SportRoute("basketball", "Basketball", ("bad-key", "basketball"), "basketball")
    opportunities = discover_live_opportunities(
        FlakyBook(), MockExchangeProvider(), [route], 100
    )
    assert len(opportunities) == 6
