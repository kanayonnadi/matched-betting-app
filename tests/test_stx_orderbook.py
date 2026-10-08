import asyncio
import json
from datetime import datetime, timezone

import pytest

from providers import ProviderUnavailableError
from providers.stx import StxSigner
from providers.stx_orderbook import (
    StxOrderBookClient,
    handshake_headers,
    orderbook_join,
    websocket_url,
)

TEST_KEY = """-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f
-----END PRIVATE KEY-----"""
FIXED = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)


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
        self.uri = None
        self.headers = None

    def __call__(self, uri, additional_headers=None):
        self.uri = uri
        self.headers = additional_headers
        return self.websocket


def test_websocket_url_conversion():
    assert websocket_url("https://demo.stxapp.ca") == "wss://demo.stxapp.ca/socket/websocket?vsn=2.0.0"


def test_handshake_signs_socket_path_without_query():
    signer = StxSigner("key", TEST_KEY, clock=lambda: FIXED)
    headers = handshake_headers(signer)
    expected_ts, expected_sig = signer.sign_message(
        "GET", "/socket/websocket", timestamp_ms=int(FIXED.timestamp() * 1000)
    )
    assert headers["X-STX-ACCESS-SIGNATURE"] == expected_sig
    assert headers["X-STX-ACCESS-TIMESTAMP"] == expected_ts
    assert "?vsn" not in "/socket/websocket"


def test_orderbook_join_frame():
    frame = orderbook_join(["m1", "m2"])
    assert frame == ["1", "1", "orderbook", "phx_join", {"market_ids": ["m1", "m2"]}]


def test_snapshot_collects_books_and_sends_join():
    book = {"market_id": "m1", "bids": [{"price": "0.3900", "quantity": "100.00"}], "offers": []}
    websocket = FakeWebSocket(
        [
            json.dumps(["1", "1", "orderbook", "phx_reply", {"status": "ok"}]),
            json.dumps(["1", "1", "orderbook", "book", book]),
        ]
    )
    connector = FakeConnector(websocket)
    client = StxOrderBookClient(StxSigner("key", TEST_KEY, clock=lambda: FIXED), "https://demo.stxapp.ca", connector=connector)

    books = client.snapshot(["m1"], timeout=1.0)
    assert books == {"m1": book}
    assert json.loads(websocket.sent[0])[3] == "phx_join"
    assert connector.uri == "wss://demo.stxapp.ca/socket/websocket?vsn=2.0.0"
    assert "X-STX-ACCESS-SIGNATURE" in connector.headers


def test_snapshot_raises_on_join_error():
    websocket = FakeWebSocket(
        [json.dumps(["1", "1", "orderbook", "phx_reply", {"status": "error", "response": {"reason": "market_ids_required"}}])]
    )
    client = StxOrderBookClient(StxSigner("key", TEST_KEY), "https://demo.stxapp.ca", connector=FakeConnector(websocket))
    with pytest.raises(ProviderUnavailableError):
        client.snapshot(["m1"], timeout=1.0)


def test_snapshot_times_out_empty():
    websocket = FakeWebSocket([])
    client = StxOrderBookClient(StxSigner("key", TEST_KEY), "https://demo.stxapp.ca", connector=FakeConnector(websocket))
    assert client.snapshot(["m1"], timeout=0.05) == {}
