import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from models import MarketType
from providers.base import EventNotFoundError
from providers.stx import StxProvider, StxSigner

FIXED = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
TEST_KEY = """-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f
-----END PRIVATE KEY-----"""


class FakeStxClient:
    def __init__(self, markets):
        self._markets = markets
        self.signer = StxSigner("key", TEST_KEY, clock=lambda: FIXED)

    def list_markets(self, **params):
        return {"markets": list(self._markets), "cursor": None}


class FakeWebSocket:
    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if self._messages:
            return self._messages.pop(0)
        await asyncio.sleep(10)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeConnector:
    def __init__(self, websocket):
        self.websocket = websocket

    def __call__(self, uri, additional_headers=None):
        return self.websocket


def moneyline_market(event_id, position, bid_price=None, bid_qty=None, max_price="1.0000", grouping="Moneyline"):
    bids = []
    if bid_price is not None:
        bids.append({"price": str(bid_price), "quantity": str(bid_qty)})
    return {
        "market_id": f"{event_id}:{position}",
        "event_id": event_id,
        "event_title": "Away Team at Home Team",
        "event_start": "2026-11-01T19:30:00.000000Z",
        "sport": "Basketball",
        "competition": "NBA",
        "grouping_name": grouping,
        "grouping_id": f"{event_id}:ml",
        "position": position,
        "title": position,
        "participants": [
            {"name": "Away Team", "role": "away"},
            {"name": "Home Team", "role": "home"},
        ],
        "max_price": max_price,
        "bids": bids,
    }


def make_provider(markets):
    return StxProvider("key", "unused", client=FakeStxClient(markets), clock=lambda: FIXED)


def test_get_events_uses_participants_for_home_away():
    provider = make_provider([moneyline_market("evt-1", "Home Team", "0.5", "10")])
    events = provider.get_events("Basketball")
    assert len(events) == 1
    event = events[0]
    assert event.provider == "stx"
    assert event.event_id == "evt-1"
    assert event.home_team == "Home Team"
    assert event.away_team == "Away Team"
    assert event.league == "NBA"
    assert event.start_time == datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc)


def test_get_markets_moneyline():
    provider = make_provider(
        [
            moneyline_market("evt-1", "Home Team", "0.5", "10"),
            moneyline_market("evt-1", "Away Team", "0.6", "5"),
        ]
    )
    provider.get_events("Basketball")
    markets = provider.get_markets("evt-1")
    assert len(markets) == 1
    assert markets[0].market_type is MarketType.MONEYLINE
    assert markets[0].selections == ("Home Team", "Away Team")


def test_get_odds_converts_bid_to_lay_decimal():
    provider = make_provider([moneyline_market("evt-1", "Home Team", "0.5", "10", max_price="1.0000")])
    provider.get_events("Basketball")
    quotes = provider.get_odds("evt-1", "moneyline")
    assert len(quotes) == 1
    quote = quotes[0]
    assert quote.side.value == "lay"
    assert quote.decimal_odds == Decimal("2.0")  # 1.00 / 0.50
    assert quote.available_size == Decimal("5.0")  # 0.50 * 10
    assert quote.fee_model == "per_wager"
    assert quote.fee_factor == Decimal("0.10")
    assert quote.max_price == Decimal("1.0000")
    assert quote.commission == Decimal("0")
    assert quote.selection == "Home Team"


def test_market_without_bids_yields_no_quote():
    provider = make_provider([moneyline_market("evt-1", "Home Team")])
    provider.get_events("Basketball")
    assert provider.get_odds("evt-1", "moneyline") == ()


def test_non_moneyline_grouping_ignored():
    provider = make_provider(
        [
            moneyline_market("evt-1", "Home Team", grouping="First Score is a Touchdown"),
        ]
    )
    assert provider.get_events("Basketball") == ()


def test_three_way_grouping_market_type():
    provider = make_provider(
        [
            moneyline_market("evt-1", "Home Team", "0.4", "5"),
            moneyline_market("evt-1", "Away Team", "0.3", "5"),
            moneyline_market("evt-1", "Draw", "0.2", "5"),
        ]
    )
    provider.get_events("Basketball")
    assert provider.get_markets("evt-1")[0].market_type is MarketType.THREE_WAY
    assert len(provider.get_odds("evt-1", "moneyline")) == 3


def test_unknown_event_raises():
    provider = make_provider([moneyline_market("evt-1", "Home Team", "0.5", "10")])
    with pytest.raises(EventNotFoundError):
        provider.get_markets("missing")


def test_unknown_market_returns_empty():
    provider = make_provider([moneyline_market("evt-1", "Home Team", "0.5", "10")])
    provider.get_events("Basketball")
    assert provider.get_odds("evt-1", "totals") == ()


def test_get_odds_prefers_live_order_book():
    markets = [moneyline_market("evt-1", "Home Team")]  # no REST bids
    market_id = markets[0]["market_id"]
    reply = json.dumps(
        [
            "1", "1", f"market:{market_id}", "phx_reply",
            {"status": "ok", "response": {"market_id": market_id, "ob": {"b": [{"p": 0.5, "q": 10.0}]}}},
        ]
    )
    websocket = FakeWebSocket([reply])
    provider = StxProvider(
        "key",
        "unused",
        client=FakeStxClient(markets),
        clock=lambda: FIXED,
        ws_connector=FakeConnector(websocket),
    )
    provider.get_events("Basketball")
    provider.load_order_books([market_id], timeout=1.0)

    quotes = provider.get_odds("evt-1", "moneyline")
    assert len(quotes) == 1
    assert quotes[0].decimal_odds == Decimal("2.0")
    assert quotes[0].available_size == Decimal("5.00")
