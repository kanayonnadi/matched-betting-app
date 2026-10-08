from datetime import datetime, timezone
from decimal import Decimal

import pytest

from models import Event, Market, MarketType, NormalizedOdds, Side
from providers import OddsProvider

START = datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def test_provider_is_abstract():
    with pytest.raises(TypeError):
        OddsProvider()


class MockProvider(OddsProvider):
    name = "mock"

    def get_events(self, sport):
        return [
            Event(
                provider=self.name,
                event_id="1",
                sport=sport,
                league="NBA",
                home_team="Knicks",
                away_team="Raptors",
                start_time=START,
            )
        ]

    def get_markets(self, event_id):
        return [
            Market(
                provider=self.name,
                event_id=event_id,
                name="Moneyline",
                market_type=MarketType.MONEYLINE,
                selections=("Knicks", "Raptors"),
            )
        ]

    def get_odds(self, event_id, market):
        return [
            NormalizedOdds(
                provider=self.name,
                event_id=event_id,
                sport="basketball",
                league="NBA",
                home_team="Knicks",
                away_team="Raptors",
                start_time=START,
                market=market,
                selection="Raptors",
                side=Side.BACK,
                decimal_odds=Decimal("3.20"),
                timestamp=NOW,
            )
        ]


def test_complete_subclass_can_be_instantiated_and_used():
    provider = MockProvider()
    assert provider.name == "mock"
    events = provider.get_events("basketball")
    assert len(events) == 1
    markets = provider.get_markets(events[0].event_id)
    assert markets[0].market_type is MarketType.MONEYLINE
    odds = provider.get_odds(events[0].event_id, markets[0].name)
    assert odds[0].decimal_odds == Decimal("3.2")


def test_incomplete_subclass_cannot_be_instantiated():
    class Incomplete(OddsProvider):
        def get_events(self, sport):
            return []

    with pytest.raises(TypeError):
        Incomplete()
