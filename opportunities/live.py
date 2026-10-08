"""Live discovery across The Odds API (sportsbooks) and STX (exchange).

Live feeds use different sport vocabularies, so discovery iterates
:class:`~providers.sports.SportRoute` entries: book events for each Odds API key,
STX events for the STX sport label, then the shared event/market matchers and the
opportunity engine. Sportsbook errors are logged and skipped per route.
"""

from typing import Optional, Sequence

from logging_config import get_logger
from matching import EventMatcher, MarketMatcher

from .discovery import opportunities_for_event
from .engine import BetKind

logger = get_logger(__name__)


def discover_live_opportunities(
    book_provider,
    exchange_provider,
    routes: Sequence,
    stake,
    kind: BetKind = BetKind.QUALIFYING,
    event_matcher: Optional[EventMatcher] = None,
    market_matcher: Optional[MarketMatcher] = None,
    enrich_order_books: bool = False,
):
    event_matcher = event_matcher or EventMatcher()
    market_matcher = market_matcher or MarketMatcher()

    opportunities = []
    for route in routes:
        book_events = []
        for odds_key in route.odds_api_keys:
            try:
                book_events.extend(book_provider.get_events(odds_key))
            except Exception as exc:  # noqa: BLE001 - provider isolation
                logger.warning("sportsbook fetch failed for %s: %s", odds_key, exc)

        try:
            exchange_events = exchange_provider.get_events(route.stx_sport)
        except Exception as exc:  # noqa: BLE001
            logger.warning("exchange fetch failed for %s: %s", route.stx_sport, exc)
            continue

        event_matches = event_matcher.find_matches(book_events, exchange_events)
        if not event_matches:
            continue

        if enrich_order_books and hasattr(exchange_provider, "market_ids"):
            market_ids = []
            for event_match in event_matches:
                market_ids.extend(exchange_provider.market_ids(event_match.exchange_event.event_id))
            if market_ids and hasattr(exchange_provider, "load_order_books"):
                exchange_provider.load_order_books(market_ids)

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
