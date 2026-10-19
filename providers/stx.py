"""STX (Ontario) exchange client: request signing and REST access.

STX is a Canadian (AGCO/iGO licensed) peer-to-peer exchange. Every request is
signed with an Ed25519 API key; there is no login or session token.

Signing (from https://docs.stxapp.io/api/authentication/):

* message  = timestamp_ms + HTTP_METHOD_UPPERCASE + request_path
* path     = endpoint *including* query string, excluding scheme and host
* headers  = X-STX-ACCESS-KEY / -TIMESTAMP / -SIGNATURE (standard base64, padded)
* strictly pure Ed25519 (not Ed25519ph); the body is **not** signed.

This module never places orders and never automates logins.
"""

import base64
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from urllib.parse import urlencode

import requests
from cryptography.hazmat.primitives import serialization

from models import Event, ExchangeOdds, Market, MarketType, Side

from .base import EventNotFoundError, OddsProvider, ProviderUnavailableError
from .theoddsapi import parse_time

STX_DEMO_CA = "https://demo.stxapp.ca"
STX_PRODUCTION_CA = "https://stxapp.ca"
STX_DEMO_US = "https://demo.stxapp.io"
DEFAULT_FEE_FACTOR = Decimal("0.10")  # STX taker factor at the base tier
USER_AGENT = "matched-betting-app/1.0 (python)"


def load_private_key(pem):
    if isinstance(pem, str):
        pem = pem.encode("utf-8")
    return serialization.load_pem_private_key(pem, password=None)


class StxSigner:
    def __init__(self, key_id: str, private_key, clock=None):
        if not key_id:
            raise ValueError("key_id is required")
        self._key_id = key_id
        self._key = private_key if hasattr(private_key, "sign") else load_private_key(private_key)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign_message(self, method: str, path: str, timestamp_ms: Optional[int] = None):
        if timestamp_ms is None:
            timestamp_ms = int(self._clock().timestamp() * 1000)
        message = f"{timestamp_ms}{method.upper()}{path}".encode("utf-8")
        signature = base64.b64encode(self._key.sign(message)).decode("ascii")
        return str(timestamp_ms), signature

    def headers(self, method: str, path: str) -> dict:
        timestamp, signature = self.sign_message(method, path)
        return {
            "X-STX-ACCESS-KEY": self._key_id,
            "X-STX-ACCESS-TIMESTAMP": timestamp,
            "X-STX-ACCESS-SIGNATURE": signature,
            "User-Agent": USER_AGENT,
        }


def _requests_get(url, headers):
    try:
        response = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as exc:
        raise ProviderUnavailableError(f"STX request failed: {exc}") from exc
    if response.status_code == 401:
        raise ProviderUnavailableError("STX rejected the request signature (401)")
    if response.status_code == 403:
        raise ProviderUnavailableError(f"STX account not permitted (403): {response.text[:200]}")
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise ProviderUnavailableError(f"STX error: {exc}") from exc
    return response.json()


class StxClient:
    """Signed read-only access to the STX REST API."""

    def __init__(
        self,
        key_id: str,
        private_key,
        base_url: str = STX_DEMO_CA,
        http_get=None,
        clock=None,
    ):
        self._signer = StxSigner(key_id, private_key, clock=clock)
        self._base_url = base_url.rstrip("/")
        self._http_get = http_get or _requests_get

    @property
    def signer(self) -> StxSigner:
        return self._signer

    def _build_url(self, path: str, params) -> str:
        if not params:
            return self._base_url + path
        normalized = {
            key: ("true" if value else "false") if isinstance(value, bool) else value
            for key, value in params.items()
        }
        return self._base_url + path + "?" + urlencode(normalized)

    def get(self, path: str, params=None):
        url = self._build_url(path, params)
        signed_path = url[len(self._base_url):]
        headers = self._signer.headers("GET", signed_path)
        return self._http_get(url, headers)

    def get_me(self):
        return self.get("/api/v1/me")

    def list_markets(self, **params):
        return self.get("/api/v1/markets", params or None)

    def list_events(self, **params):
        return self.get("/api/v1/events", params or None)


MONEYLINE = "moneyline"


def _to_decimal(value, default=None):
    if value is None or value == "":
        return default
    return Decimal(str(value))


def _best_bid(bids):
    """Best (highest) resting bid = the price at which we can lay."""
    best_price = None
    best_quantity = Decimal("0")
    for bid in bids or []:
        price = _to_decimal(bid.get("price"), Decimal("0"))
        quantity = _to_decimal(bid.get("quantity"), Decimal("0"))
        if price <= 0 or quantity <= 0:
            continue
        if best_price is None or price > best_price:
            best_price, best_quantity = price, quantity
    if best_price is None:
        return None
    return best_price, best_quantity


def _home_away(market):
    home = away = None
    for participant in market.get("participants") or []:
        role = (participant.get("role") or "").lower()
        if role == "home":
            home = participant.get("name")
        elif role == "away":
            away = participant.get("name")
    if home and away:
        return home, away
    title = market.get("event_title") or ""
    if " at " in title:
        away, home = title.split(" at ", 1)
        return home.strip(), away.strip()
    if " vs " in title.lower():
        home, away = title.split(" vs ", 1)
        return home.strip(), away.strip()
    return "", ""


class StxProvider(OddsProvider):
    """STX (Ontario) exchange adapter exposing LAY quotes.

    STX markets are binary contracts settling at ``max_price``. The order book's
    bids are buyers of the outcome; selling into a bid is economically a lay, so:

        lay decimal odds = max_price / best_bid_price
        available lay stake = best_bid_price * best_bid_quantity

    Only moneyline groupings are exposed; anything without resting bids yields no
    quote (never an invented price).
    """

    name = "stx"
    source_type = "LIVE"

    def __init__(
        self,
        key_id: str,
        private_key,
        base_url: str = STX_DEMO_CA,
        fee_factor=DEFAULT_FEE_FACTOR,
        client=None,
        clock=None,
        ws_connector=None,
        require_ws_books: bool = True,
    ):
        self._client = client or StxClient(key_id, private_key, base_url=base_url, clock=clock)
        self._base_url = base_url
        self._fee_factor = Decimal(str(fee_factor))
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._ws_connector = ws_connector
        self._require_ws_books = require_ws_books
        self._events = {}
        self._grouped = {}
        self._books = {}
        self._missing_books = {}
        self._market_received_at = {}
        self._event_received_at = {}
        self._markets_fetched_at = None

    def _fetch_markets(self, sport: str):
        results = []
        cursor = None
        while True:
            params = {"sports": sport, "trading": True, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = self._client.list_markets(**params)
            results.extend(data.get("markets") or [])
            cursor = data.get("cursor")
            if not cursor:
                break
        # REST list_markets carries no per-book timestamp; record the
        # observation time once for the whole fetch, never at read time.
        fetched_at = self._clock()
        self._markets_fetched_at = fetched_at
        for market in results:
            market_id = market.get("market_id")
            if market_id:
                self._market_received_at[market_id] = fetched_at
        return results

    def get_events(self, sport: str):
        grouped = {}
        for market in self._fetch_markets(sport):
            if (market.get("grouping_name") or "").strip().lower() != MONEYLINE:
                continue
            event_id = market.get("event_id")
            if not event_id:
                continue
            grouped.setdefault(event_id, []).append(market)

        events = []
        for event_id, markets in grouped.items():
            first = markets[0]
            home, away = _home_away(first)
            if not home or not away or not first.get("event_start"):
                continue
            event = Event(
                provider=self.name,
                event_id=event_id,
                sport=first.get("sport") or sport,
                league=first.get("competition") or "",
                home_team=home,
                away_team=away,
                start_time=parse_time(first["event_start"]),
            )
            self._events[event_id] = event
            self._grouped[event_id] = markets
            self._event_received_at[event_id] = self._markets_fetched_at or self._clock()
            events.append(event)
        return tuple(events)

    def _require(self, event_id: str):
        event = self._events.get(event_id)
        if event is None:
            raise EventNotFoundError(
                f"{self.name}: event {event_id!r} is not loaded; call get_events first"
            )
        return event

    def get_markets(self, event_id: str):
        self._require(event_id)
        markets = self._grouped[event_id]
        market_type = {2: MarketType.MONEYLINE, 3: MarketType.THREE_WAY}.get(
            len(markets), MarketType.MONEYLINE
        )
        selections = tuple(
            market.get("position") or market.get("title") or "" for market in markets
        )
        return (
            Market(
                provider=self.name,
                event_id=event_id,
                name=MONEYLINE,
                market_type=market_type,
                selections=selections,
            ),
        )

    def get_odds(self, event_id: str, market: str):
        event = self._require(event_id)
        if market != MONEYLINE:
            return ()
        quotes = []
        for raw_market in self._grouped[event_id]:
            market_id = raw_market.get("market_id")
            cached = self._books.get(market_id)
            bids = None
            received_at = None
            source_timestamp = None
            timestamp_source = "unknown"
            freshness_verified = False
            if cached is not None:
                if cached.get("ob") is not None:
                    bids = [
                        {"price": level.get("p"), "quantity": level.get("q")}
                        for level in (cached["ob"] or {}).get("b") or []
                    ]
                elif cached.get("bids") is not None:
                    bids = cached["bids"]
                received_at = cached.get("received_at")
                source_timestamp = cached.get("source_timestamp")
                timestamp_source = cached.get("timestamp_source") or (
                    "ws_source" if source_timestamp else "ws_received"
                )
                freshness_verified = bool(cached.get("freshness_verified", True))
            elif not self._require_ws_books:
                # Explicit non-live/legacy path: REST depth is usable for
                # calculation but is never marked as verified freshness.
                bids = raw_market.get("bids")
                received_at = self._market_received_at.get(market_id)
                timestamp_source = "rest_received"
                freshness_verified = False
            else:
                # No authoritative WebSocket book: emit no quote rather than
                # silently substituting REST depth.
                continue
            bid = _best_bid(bids)
            if bid is None:
                continue
            price, quantity = bid
            max_price = _to_decimal(raw_market.get("max_price"), Decimal("1"))
            if max_price is None or max_price <= 0:
                continue
            decimal_odds = max_price / price
            if decimal_odds <= 1:
                continue
            timestamp = source_timestamp or received_at
            if timestamp is None:
                continue
            quotes.append(
                ExchangeOdds(
                    provider=self.name,
                    event_id=event_id,
                    sport=event.sport,
                    league=event.league,
                    home_team=event.home_team,
                    away_team=event.away_team,
                    start_time=event.start_time,
                    market=MONEYLINE,
                    selection=raw_market.get("position") or raw_market.get("title") or "",
                    side=Side.LAY,
                    decimal_odds=decimal_odds,
                    timestamp=timestamp,
                    available_size=price * quantity,
                    commission=Decimal("0"),
                    fee_model="per_wager",
                    fee_factor=self._fee_factor,
                    max_price=max_price,
                    source_timestamp=source_timestamp,
                    received_at=received_at,
                    last_updated_at=source_timestamp,
                    timestamp_source=timestamp_source,
                    freshness_verified=freshness_verified,
                )
            )
        return tuple(quotes)

    def get_order_book(self, event_id: str, selection: str):
        """Lay-side depth for one selection.

        Production uses only the authoritative WebSocket ``ob`` snapshot. REST
        ``bids`` are used only when ``require_ws_books=False`` (explicit legacy
        paths) and are never marked as verified freshness.
        """
        from liquidity import build_order_book, build_order_book_from_ws

        self._require(event_id)
        for raw_market in self._grouped[event_id]:
            label = raw_market.get("position") or raw_market.get("title") or ""
            if label != selection:
                continue
            market_id = raw_market.get("market_id")
            max_price = _to_decimal(raw_market.get("max_price"), Decimal("1"))
            cached = self._books.get(market_id)
            if cached is not None:
                received_at = cached.get("received_at")
                source_timestamp = cached.get("source_timestamp")
                timestamp_source = cached.get("timestamp_source") or (
                    "ws_source" if source_timestamp else "ws_received"
                )
                timestamp = source_timestamp or received_at
                completeness = cached.get("completeness", "COMPLETE")
                if cached.get("ob") is not None:
                    return build_order_book_from_ws(
                        cached["ob"], max_price, timestamp,
                        completeness=completeness,
                        received_at=received_at,
                        source_timestamp=source_timestamp,
                        timestamp_source=timestamp_source,
                        freshness_verified=True,
                    )
                if cached.get("bids") is not None:
                    return build_order_book(
                        cached["bids"], max_price, timestamp,
                        completeness=completeness,
                        received_at=received_at,
                        source_timestamp=source_timestamp,
                        timestamp_source=timestamp_source,
                        freshness_verified=True,
                    )
            if self._require_ws_books:
                missing = self._missing_books.get(market_id) or {}
                return build_order_book(
                    [], max_price, None,
                    completeness="UNKNOWN",
                    received_at=missing.get("attempted_at"),
                    timestamp_source="ws_missing",
                    freshness_verified=False,
                )
            received_at = self._market_received_at.get(market_id)
            return build_order_book(
                raw_market.get("bids"), max_price, received_at,
                completeness="UNKNOWN",
                received_at=received_at,
                timestamp_source="rest_received",
                freshness_verified=False,
            )
        raise EventNotFoundError(f"{self.name}: selection {selection!r} not found")

    def market_ids(self, event_id: str):
        self._require(event_id)
        return tuple(
            market.get("market_id")
            for market in self._grouped[event_id]
            if market.get("market_id")
        )

    def load_order_books(self, market_ids, timeout: float = 5.0, max_attempts: int = 3) -> dict:
        """Fetch authoritative WebSocket order-book snapshots for ``market_ids``.

        Only the requested (matched) markets are subscribed. Missing markets
        are remembered as unavailable so ``get_odds`` never substitutes REST
        depth. If no snapshot at all is received, raise so the caller can mark
        discovery unavailable rather than silently using REST liquidity.
        """
        from .stx_orderbook import StxOrderBookClient

        ids = list(dict.fromkeys(market_ids or []))
        if not ids:
            return {}
        client = StxOrderBookClient(
            self._client.signer, self._base_url,
            connector=self._ws_connector, clock=self._clock,
        )
        try:
            records = client.market_book_snapshots(
                ids, timeout=timeout, max_attempts=max_attempts
            )
        except Exception as exc:  # noqa: BLE001 - surface as provider unavailable
            raise ProviderUnavailableError(
                f"STX WebSocket order books unavailable: {exc}"
            ) from exc
        if not records:
            raise ProviderUnavailableError(
                "STX WebSocket order books unavailable (no snapshots received)"
            )
        for market_id, record in records.items():
            self._books[market_id] = {
                "ob": record.get("ob"),
                "bids": record.get("bids"),
                "received_at": record.get("received_at") or self._clock(),
                "source_timestamp": record.get("source_timestamp"),
                "timestamp_source": record.get("timestamp_source") or (
                    "ws_source" if record.get("source_timestamp") else "ws_received"
                ),
                "freshness_verified": True,
                "completeness": "COMPLETE",
            }
            self._missing_books.pop(market_id, None)
        attempted_at = self._clock()
        for market_id in ids:
            if market_id not in records:
                self._missing_books[market_id] = {
                    "attempted_at": attempted_at,
                    "reason": "no WebSocket snapshot",
                }
        return {
            market_id: record.get("ob") for market_id, record in records.items()
        }
