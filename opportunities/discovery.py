"""Opportunity discovery: orchestrate providers, matchers and the engine.

Providers return raw normalized quotes; the event matcher pairs events, the
market matcher pairs markets/selections, and the engine prices each matched
selection. The result is a flat list of :class:`~opportunities.engine.Opportunity`
objects annotated with the event/market match confidence.
"""

from dataclasses import replace
from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence

from matching import EventMatcher, MarketMatcher
from models import Event, Provenance
from providers import MockExchangeProvider, MockSportsbookProvider
from providers.mock import MOCK_EVENTS

from .engine import BetKind, Opportunity, build_opportunity


def default_mock_sports() -> list:
    return sorted({event.sport for event in MOCK_EVENTS})


def discover_opportunities(
    book_provider,
    exchange_provider,
    sports: Sequence[str],
    stake,
    kind: BetKind = BetKind.QUALIFYING,
    event_matcher: Optional[EventMatcher] = None,
    market_matcher: Optional[MarketMatcher] = None,
) -> list:
    event_matcher = event_matcher or EventMatcher()
    market_matcher = market_matcher or MarketMatcher()

    opportunities = []
    for sport in sports:
        book_events = book_provider.get_events(sport)
        exchange_events = exchange_provider.get_events(sport)
        event_matches = event_matcher.find_matches(book_events, exchange_events)

        for event_match in event_matches:
            opportunities.extend(
                opportunities_for_event(
                    book_provider,
                    exchange_provider,
                    event_match,
                    stake,
                    kind,
                    market_matcher,
                )
            )
    return opportunities


def opportunities_for_event(
    book_provider,
    exchange_provider,
    event_match,
    stake,
    kind,
    market_matcher,
):
    book_event: Event = event_match.sportsbook_event
    exchange_event: Event = event_match.exchange_event

    book_markets = book_provider.get_markets(book_event.event_id)
    exchange_markets = exchange_provider.get_markets(exchange_event.event_id)
    market_matches = market_matcher.find_matches(book_markets, exchange_markets)

    book_source = getattr(book_provider, "source_type", "MOCK")
    exchange_source = getattr(exchange_provider, "source_type", "MOCK")
    source_type = "LIVE" if book_source == "LIVE" and exchange_source == "LIVE" else "MOCK"
    is_live = source_type == "LIVE"
    retrieved_at = datetime.now(timezone.utc)

    results = []
    for market_match in market_matches:
        book_quotes = {
            quote.selection: quote
            for quote in book_provider.get_odds(
                book_event.event_id, market_match.sportsbook_market.name
            )
        }
        exchange_quotes = {
            quote.selection: quote
            for quote in exchange_provider.get_odds(
                exchange_event.event_id, market_match.exchange_market.name
            )
        }
        get_order_book = getattr(exchange_provider, "get_order_book", None)
        for selection_match in market_match.selection_matches:
            book_quote = book_quotes.get(selection_match.sportsbook_selection)
            exchange_quote = exchange_quotes.get(selection_match.exchange_selection)
            if book_quote is None or exchange_quote is None:
                continue
            order_book = None
            if get_order_book is not None:
                try:
                    order_book = get_order_book(
                        exchange_event.event_id, selection_match.exchange_selection
                    )
                except Exception:  # noqa: BLE001 - depth is best-effort
                    order_book = None
            opportunity = build_opportunity(
                book_quote, exchange_quote, stake, kind=kind, order_book=order_book
            )
            market_name = market_match.sportsbook_market.name
            book_timestamp = book_quote.timestamp
            exchange_timestamp = (
                order_book.timestamp if order_book is not None and order_book.timestamp
                else exchange_quote.timestamp
            )
            liquidity_snapshot_id = (
                f"{exchange_quote.provider}:{market_name}:{exchange_timestamp.isoformat()}"
                if order_book is not None and exchange_timestamp
                else None
            )
            provenance = Provenance(
                source_type=source_type,
                sportsbook_provider=book_quote.provider,
                exchange_provider=exchange_quote.provider,
                event_id=book_event.event_id,
                market_id=market_name,
                selection_id=(
                    f"{book_quote.provider}|{book_event.event_id}|{market_name}|{book_quote.selection}"
                ),
                sportsbook_quote_timestamp=book_timestamp,
                exchange_book_timestamp=exchange_timestamp,
                retrieved_at=retrieved_at,
                event_match_confidence=event_match.confidence,
                market_match_confidence=market_match.confidence,
                liquidity_snapshot_id=liquidity_snapshot_id,
                is_live=is_live,
            )
            results.append(
                replace(
                    opportunity,
                    event_confidence=event_match.confidence,
                    market_confidence=market_match.confidence,
                    swapped=event_match.swapped,
                    source_type=source_type,
                    is_live=is_live,
                    provenance=provenance,
                )
            )
    return results


def discover_mock_opportunities(
    stake,
    sports: Optional[Iterable[str]] = None,
    kind: BetKind = BetKind.QUALIFYING,
) -> list:
    """Convenience entry point using the built-in mock feeds."""
    selected = list(sports) if sports is not None else default_mock_sports()
    return discover_opportunities(
        MockSportsbookProvider(),
        MockExchangeProvider(),
        selected,
        stake,
        kind=kind,
    )
