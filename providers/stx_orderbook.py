"""STX WebSocket order book client.

STX publishes live order books over WebSockets (Phoenix Channels framing). The
handshake is signed like a REST ``GET /socket/websocket`` (query string
excluded). We join the authoritative ``market:<id>`` channel for each market and
read the complete aggregated book (``ob``) from the join reply. Reconnects are
handled by re-joining only the markets still missing; a partial snapshot is
never replaced with REST depth.

This client only reads market data. It never places or cancels orders.
"""

import asyncio
import json
import time
from datetime import datetime, timezone

from .base import ProviderUnavailableError
from .stx import USER_AGENT, StxSigner

WS_PATH = "/socket/websocket"

_SOURCE_TIME_KEYS = ("timestamp", "updated_at", "last_updated_at", "ts", "server_time")


def _parse_timestamp(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _source_timestamp(response):
    """Book-level exchange timestamp from a ``market:<id>`` reply, if any.

    The top-level ``timestamp`` describes the *market record* (often its
    creation time), not the order book snapshot. Using it as book freshness
    would mark a live book hours/minutes stale, so only timestamps nested in
    ``ob`` are considered. When ``ob`` carries none, ``received_at`` is the
    observation time.
    """
    if not isinstance(response, dict):
        return None
    ob = response.get("ob")
    if not isinstance(ob, dict):
        return None
    for key in _SOURCE_TIME_KEYS:
        parsed = _parse_timestamp(ob.get(key))
        if parsed is not None:
            return parsed
    return None


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
    def __init__(self, signer: StxSigner, base_url: str, connector=None, clock=None):
        self._signer = signer
        self._base_url = base_url
        self._connect = connector or _default_connect
        self._clock = clock or (lambda: datetime.now(timezone.utc))

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
            loop = asyncio.get_running_loop()
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
        Returns records with the raw ``ob`` plus timestamp provenance.
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
            loop = asyncio.get_running_loop()
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
                    books[market_id] = {
                        "market_id": market_id,
                        "ob": response["ob"],
                        "source_timestamp": _source_timestamp(response),
                        "received_at": self._clock(),
                    }
        return books

    def market_book_snapshots(self, market_ids, timeout: float = 8.0, max_attempts: int = 3) -> dict:
        """Fetch full books, reconnecting for any markets still missing.

        A dropped connection or a partial snapshot leaves those markets absent;
        we retry them on a fresh connection rather than falling back to REST.
        """
        remaining = list(market_ids)
        results = {}
        attempts = max(1, int(max_attempts))
        for attempt in range(attempts):
            if not remaining:
                break
            try:
                batch = asyncio.run(self._market_books(remaining, timeout))
            except Exception:  # noqa: BLE001 - reconnect and retry remaining ids
                batch = {}
            results.update(batch)
            remaining = [market_id for market_id in remaining if market_id not in results]
            if remaining and attempt + 1 < attempts:
                time.sleep(0.2)
        return results

    def market_books(self, market_ids, timeout: float = 8.0, max_attempts: int = 3) -> dict:
        records = self.market_book_snapshots(market_ids, timeout, max_attempts)
        return {market_id: record["ob"] for market_id, record in records.items()}
