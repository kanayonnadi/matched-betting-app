"""STX WebSocket order book client.

STX publishes live order books over WebSockets (Phoenix Channels framing). The
handshake is signed like a REST ``GET /socket/websocket`` (query string
excluded). We join the aggregated ``orderbook`` topic for a set of market ids
and collect one full snapshot per market; every push is a complete snapshot, so
the last one wins.

This client only reads market data. It never places or cancels orders.
"""

import asyncio
import json

from .base import ProviderUnavailableError
from .stx import USER_AGENT, StxSigner

WS_PATH = "/socket/websocket"


def websocket_url(base_url: str) -> str:
    scheme = "wss" if base_url.startswith("https://") else "ws"
    host = base_url.split("://", 1)[-1].rstrip("/")
    return f"{scheme}://{host}{WS_PATH}?vsn=2.0.0"


def handshake_headers(signer: StxSigner) -> dict:
    headers = signer.headers("GET", WS_PATH)
    headers["User-Agent"] = USER_AGENT
    return headers


def join_frame(topic: str, payload, ref: str = "1", join_ref: str = "1") -> list:
    return [join_ref, ref, topic, "phx_join", payload]


def orderbook_join(market_ids, ref: str = "1") -> list:
    return join_frame("orderbook", {"market_ids": list(market_ids)}, ref=ref)


def _default_connect(uri, additional_headers=None):
    import websockets

    return websockets.connect(uri, additional_headers=additional_headers)


class StxOrderBookClient:
    def __init__(self, signer: StxSigner, base_url: str, connector=None):
        self._signer = signer
        self._base_url = base_url
        self._connect = connector or _default_connect

    @staticmethod
    def handle_message(message, books: dict) -> None:
        if not isinstance(message, list) or len(message) < 5:
            return
        event = message[3]
        payload = message[4]
        if event == "book" and isinstance(payload, dict):
            market_id = payload.get("market_id")
            if market_id:
                books[market_id] = payload
        elif event == "phx_reply" and isinstance(payload, dict):
            if payload.get("status") == "error":
                raise ProviderUnavailableError(
                    f"STX order book join failed: {payload.get('response')}"
                )

    async def _snapshot(self, market_ids, timeout: float) -> dict:
        market_ids = list(market_ids)
        books = {}
        headers = handshake_headers(self._signer)
        async with self._connect(
            websocket_url(self._base_url), additional_headers=headers
        ) as websocket:
            await websocket.send(json.dumps(orderbook_join(market_ids)))
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout
            while len(books) < len(market_ids):
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
                except TimeoutError:
                    break
                self.handle_message(json.loads(raw), books)
        return books

    def snapshot(self, market_ids, timeout: float = 5.0) -> dict:
        return asyncio.run(self._snapshot(market_ids, timeout))

    async def _market_books(self, market_ids, timeout: float) -> dict:
        """Full book per market via the authoritative ``market:<id>`` channel.

        Each join replies immediately with the market record including ``ob``
        (the complete aggregated book), unlike the ``orderbook`` change feed.
        """
        market_ids = list(market_ids)
        books = {}
        headers = handshake_headers(self._signer)
        async with self._connect(
            websocket_url(self._base_url), additional_headers=headers
        ) as websocket:
            for index, market_id in enumerate(market_ids):
                await websocket.send(
                    json.dumps([f"m{index}", str(index + 1), f"market:{market_id}", "phx_join", {}])
                )
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout
            while len(books) < len(market_ids):
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
                except TimeoutError:
                    break
                message = json.loads(raw)
                if not isinstance(message, list) or len(message) < 5:
                    continue
                topic, event, payload = message[2], message[3], message[4]
                if event != "phx_reply" or not isinstance(payload, dict):
                    continue
                response = payload.get("response") or {}
                market_id = response.get("market_id")
                if market_id is None and "market:" in topic:
                    market_id = topic.split("market:", 1)[1]
                if payload.get("status") == "ok" and market_id and response.get("ob") is not None:
                    books[market_id] = response["ob"]
        return books

    def market_books(self, market_ids, timeout: float = 8.0) -> dict:
        return asyncio.run(self._market_books(market_ids, timeout))
