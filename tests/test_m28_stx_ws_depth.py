import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from liquidity import build_order_book
from models import Event, ExchangeOdds, Market, MarketType, NormalizedOdds, Provenance, Side
from opportunities import build_opportunity, discover_live_opportunities
from providers import ProviderUnavailableError, SportRoute
from providers.cache import CachedProvider
from providers.stx import StxProvider
from providers.stx_orderbook import StxOrderBookClient
from services.promotion_workflow import _opportunity_stale

FIXED = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
START = datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc)
TEST_KEY = """-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f
-----END PRIVATE KEY-----"""


class FakeStxClient:
    def __init__(self, markets):
        self._markets = markets
        from providers.stx import StxSigner

        self.signer = StxSigner("key", TEST_KEY, clock=lambda: FIXED)

    def list_markets(self, **params):
        return {"markets": list(self._markets), "cursor": None}


class FakeWebSocket:
    def __init__(self, messages=None, fail=False):
        self._messages = list(messages or [])
        self._fail = fail
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if self._fail:
            raise ConnectionError("socket closed")
        if self._messages:
            return self._messages.pop(0)
        await asyncio.sleep(10)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeConnector:
    def __init__(self, *websockets):
        self._websockets = list(websockets)
        self.calls = 0

    def __call__(self, uri, additional_headers=None):
        self.calls += 1
        return self._websockets.pop(0)


class TickingClock:
    def __init__(self, start):
        self._now = start

    def __call__(self):
        return self._now

    def advance(self, seconds):
        self._now += timedelta(seconds=seconds)


def market(event_id="evt-1", position="Home Team", bids=None):
    return {
        "market_id": f"{event_id}:{position}",
        "event_id": event_id,
        "event_title": "Away Team at Home Team",
        "event_start": "2026-11-01T19:30:00.000000Z",
        "sport": "Basketball",
        "competition": "NBA",
        "grouping_name": "Moneyline",
        "grouping_id": f"{event_id}:ml",
        "position": position,
        "title": position,
        "participants": [
            {"name": "Away Team", "role": "away"},
            {"name": "Home Team", "role": "home"},
        ],
        "max_price": "1.0000",
        "bids": bids or [],
    }


def ws_reply(market_id, price=0.5, quantity=100.0, timestamp=None):
    response = {"market_id": market_id, "ob": {"b": [{"p": price, "q": quantity}]}}
    if timestamp:
        response["timestamp"] = timestamp
    return json.dumps(
        ["1", "1", f"market:{market_id}", "phx_reply", {"status": "ok", "response": response}]
    )


def provider(markets, connector=None, require_ws_books=True, clock=lambda: FIXED):
    return StxProvider(
        "key", "unused", client=FakeStxClient(markets), clock=clock,
        ws_connector=connector, require_ws_books=require_ws_books,
    )


# --- production uses WS, never REST fallback --------------------------------

def test_ws_required_means_no_quote_without_ws_book():
    p = provider([market(bids=[{"price": "0.40", "quantity": "100"}])])
    p.get_events("Basketball")
    assert p.get_odds("evt-1", "moneyline") == ()
    book = p.get_order_book("evt-1", "Home Team")
    assert book.levels == ()
    assert book.freshness_verified is False
    assert book.completeness == "UNKNOWN"


def test_ws_book_supplies_quote_and_depth():
    markets = [market()]
    mid = markets[0]["market_id"]
    ws = FakeWebSocket([ws_reply(mid, price=0.5, quantity=100)])
    p = provider(markets, connector=FakeConnector(ws))
    p.get_events("Basketball")
    assert p.get_odds("evt-1", "moneyline") == ()
    p.load_order_books([mid], timeout=0.5)

    quote = p.get_odds("evt-1", "moneyline")[0]
    assert quote.decimal_odds == Decimal("2.0")
    assert quote.available_size == Decimal("50.0")
    assert quote.freshness_verified is True
    assert quote.timestamp_source == "ws_received"
    assert quote.received_at == FIXED

    book = p.get_order_book("evt-1", "Home Team")
    assert book.source == "stx-ws"
    assert book.completeness == "COMPLETE"
    assert book.freshness_verified is True
    assert book.levels[0].price == Decimal("0.5")
    assert book.timestamp_source == "ws_received"


def test_ws_disagreement_with_rest_uses_ws():
    markets = [market(bids=[{"price": "0.40", "quantity": "100"}])]
    mid = markets[0]["market_id"]
    p = provider(markets, connector=FakeConnector(FakeWebSocket([ws_reply(mid, price=0.6, quantity=100)])))
    p.get_events("Basketball")
    p.load_order_books([mid], timeout=0.5)
    quote = p.get_odds("evt-1", "moneyline")[0]
    assert quote.decimal_odds == Decimal("1.0") / Decimal("0.6")
    assert p.get_order_book("evt-1", "Home Team").levels[0].price == Decimal("0.6")


# --- REST timestamp provenance ---------------------------------------------

def test_rest_timestamp_is_fetch_time_not_read_time():
    clock = TickingClock(FIXED)
    p = provider(
        [market(bids=[{"price": "0.5", "quantity": "100"}])],
        require_ws_books=False, clock=clock,
    )
    p.get_events("Basketball")
    clock.advance(30)
    quote = p.get_odds("evt-1", "moneyline")[0]
    assert quote.timestamp == FIXED          # market fetch time, not read time
    assert quote.received_at == FIXED
    assert quote.timestamp_source == "rest_received"
    assert quote.freshness_verified is False


def test_cached_provider_preserves_observation_time():
    clock = TickingClock(FIXED)
    inner = provider(
        [market(bids=[{"price": "0.5", "quantity": "100"}])],
        require_ws_books=False, clock=clock,
    )
    cached = CachedProvider(inner, ttl_seconds=30, clock=clock)
    cached.get_events("Basketball")
    first = cached.get_odds("evt-1", "moneyline")
    clock.advance(10)
    second = cached.get_odds("evt-1", "moneyline")
    assert first is second
    assert second[0].timestamp == FIXED


def test_rest_book_is_not_treated_as_verified():
    p = provider([market(bids=[{"price": "0.5", "quantity": "100"}])], require_ws_books=False)
    p.get_events("Basketball")
    book = p.get_order_book("evt-1", "Home Team")
    assert book.timestamp_source == "rest_received"
    assert book.freshness_verified is False


# --- reconnects / partial snapshots ----------------------------------------

def test_reconnect_retries_missing_markets():
    connector = FakeConnector(
        FakeWebSocket(fail=True),
        FakeWebSocket([ws_reply("m1", price=0.5, quantity=10)]),
    )
    client = StxOrderBookClient(
        SimpleNamespace(headers=lambda *a: {}), "https://demo.stxapp.ca",
        connector=connector, clock=lambda: FIXED,
    )
    records = client.market_book_snapshots(["m1"], timeout=0.2, max_attempts=2)
    assert "m1" in records
    assert connector.calls == 2


def test_partial_ws_snapshot_marks_missing_market_without_rest_fallback():
    markets = [market(event_id="e1", position="Home Team")]
    mid = markets[0]["market_id"]
    p = provider(markets, connector=FakeConnector(FakeWebSocket([ws_reply(mid)])))
    p.get_events("Basketball")
    p.load_order_books([mid, "missing-market"], timeout=0.2, max_attempts=1)
    assert mid in p._books
    assert "missing-market" in p._missing_books
    assert len(p.get_odds("e1", "moneyline")) == 1  # only the market with a book
    assert "missing-market" not in p._books


def test_all_ws_missing_raises_provider_unavailable():
    p = provider(
        [market()], connector=FakeConnector(FakeWebSocket(fail=True)),
    )
    p.get_events("Basketball")
    with pytest.raises(ProviderUnavailableError):
        p.load_order_books(["evt-1:Home Team"], timeout=0.1, max_attempts=1)


# --- freshness gating -------------------------------------------------------

def _quote(freshness_verified=True):
    return ExchangeOdds(
        provider="stx", event_id="evt-1", sport="basketball", league="NBA",
        home_team="Home Team", away_team="Away Team", start_time=START,
        market="moneyline", selection="Home Team", side=Side.LAY,
        decimal_odds=Decimal("2.0"), timestamp=FIXED,
        available_size=Decimal("100"), commission=Decimal("0"),
        fee_model="per_wager", fee_factor=Decimal("0.10"), max_price=Decimal("1"),
        received_at=FIXED, timestamp_source="ws_received",
        freshness_verified=freshness_verified,
    )


def _book_quote():
    return NormalizedOdds(
        provider="theoddsapi:test", event_id="evt-1", sport="basketball", league="NBA",
        home_team="Home Team", away_team="Away Team", start_time=START,
        market="moneyline", selection="Home Team", side=Side.BACK,
        decimal_odds=Decimal("2.0"), timestamp=FIXED,
    )


def test_unverified_quote_is_not_fully_hedged():
    opp = build_opportunity(
        _book_quote(), _quote(freshness_verified=False), Decimal("10"),
        order_book=build_order_book([{"price": "0.5", "quantity": "100"}], "1", FIXED,
                                    completeness="COMPLETE"),
    )
    assert opp.depth_status == "UNVERIFIED"
    assert opp.fully_hedged is False
    assert opp.liquidity_sufficient is False


def test_unverified_provenance_is_stale():
    prov = Provenance(
        source_type="LIVE", sportsbook_provider="theoddsapi:test",
        exchange_provider="stx", event_id="evt-1", market_id="moneyline",
        selection_id="x", sportsbook_quote_timestamp=FIXED,
        exchange_book_timestamp=FIXED, retrieved_at=FIXED,
        event_match_confidence=1.0, market_match_confidence=1.0,
        liquidity_snapshot_id=None, is_live=True,
        exchange_book_completeness="COMPLETE", exchange_freshness_verified=False,
    )
    assert _opportunity_stale(SimpleNamespace(provenance=prov, timestamp=FIXED)) is True

    verified = Provenance(**{**prov.__dict__, "exchange_freshness_verified": True})
    assert _opportunity_stale(SimpleNamespace(provenance=verified, timestamp=FIXED)) is False


# --- end-to-end: live discovery actually enriches from WS -------------------

class FakeBookProvider:
    name = "theoddsapi:test"
    source_type = "LIVE"

    def get_events(self, sport):
        return (Event(self.name, "b1", "basketball", "NBA", "Home Team", "Away Team", START),)

    def get_markets(self, event_id):
        return (Market(self.name, event_id, "moneyline", MarketType.MONEYLINE,
                       ("Home Team", "Away Team")),)

    def get_odds(self, event_id, market):
        return (NormalizedOdds(
            self.name, event_id, "basketball", "NBA", "Home Team", "Away Team",
            START, "moneyline", "Home Team", Side.BACK, Decimal("2.0"), FIXED,
        ),)


def test_live_discovery_enriches_ws_before_quoting():
    markets = [market(position="Home Team"), market(position="Away Team")]
    home_mid, away_mid = markets[0]["market_id"], markets[1]["market_id"]
    exchange = provider(
        markets,
        connector=FakeConnector(FakeWebSocket([
            ws_reply(home_mid, price=0.5, quantity=100),
            ws_reply(away_mid, price=0.5, quantity=100),
        ])),
    )
    route = SportRoute("basketball", "Basketball", ("basketball",), "Basketball")
    opportunities = discover_live_opportunities(
        FakeBookProvider(), exchange, [route], Decimal("10")
    )
    assert len(opportunities) == 1
    opp = opportunities[0]
    assert opp.order_book.source == "stx-ws"
    assert opp.depth_status == "VERIFIED"
    assert opp.fully_hedged is True
    assert opp.provenance.exchange_timestamp_source == "ws_received"
    assert opp.provenance.exchange_freshness_verified is True
    assert opp.provenance.exchange_book_completeness == "COMPLETE"
    assert opp.requested_contracts == Decimal("20")
    assert opp.executable_contracts == Decimal("20")


def test_verification_blocks_unverified_rest_book():
    from services.verification import VerificationStatus, verify_opportunity

    opp = build_opportunity(
        _book_quote(), _quote(freshness_verified=False), Decimal("10"),
        order_book=build_order_book(
            [{"price": "0.5", "quantity": "100"}], "1", FIXED,
            completeness="COMPLETE", freshness_verified=False,
            timestamp_source="rest_received",
        ),
    )
    result = verify_opportunity(opp, now=FIXED)
    assert result.status == VerificationStatus.BLOCKED.value
    assert "freshness" in result.reasons[0].lower()


def test_market_level_timestamp_is_not_used_as_book_freshness():
    # Live STX replies carry a top-level market `timestamp` (often much older
    # than the book). It must not be treated as the book's observation time.
    markets = [market()]
    mid = markets[0]["market_id"]
    reply = json.dumps([
        "1", "1", f"market:{mid}", "phx_reply",
        {"status": "ok", "response": {
            "market_id": mid,
            "timestamp": "2026-10-19T18:12:39.099857Z",
            "ob": {"b": [{"p": 0.5, "q": 100.0}]},
        }},
    ])
    p = provider(markets, connector=FakeConnector(FakeWebSocket([reply])))
    p.get_events("Basketball")
    p.load_order_books([mid], timeout=0.5)
    quote = p.get_odds("evt-1", "moneyline")[0]
    assert quote.source_timestamp is None
    assert quote.timestamp_source == "ws_received"
    assert quote.timestamp == FIXED
    assert quote.freshness_verified is True
