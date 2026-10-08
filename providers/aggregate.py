"""Aggregate several sportsbook providers behind one ``OddsProvider``.

The Odds API returns one bookmaker per request, but a matched-betting scan wants
all configured books. This adapter fans out to each provider and namespaces
event ids so two books quoting the same game do not collide.
"""

from dataclasses import replace
from typing import Sequence

from models import Event, Market, NormalizedOdds

from .base import EventNotFoundError, OddsProvider


class MultiSportsbookProvider(OddsProvider):
    name = "multi_book"

    @property
    def source_type(self) -> str:
        sources = {getattr(provider, "source_type", "MOCK") for provider in self._providers}
        return "LIVE" if sources == {"LIVE"} else "MOCK"

    def __init__(self, providers: Sequence[OddsProvider]):
        providers = tuple(providers)
        if not providers:
            raise ValueError("at least one provider is required")
        self._providers = providers
        self._routes = {}

    def _namespaced(self, index: int, event_id: str) -> str:
        return f"{index}:{event_id}"

    def _route(self, event_id: str):
        entry = self._routes.get(event_id)
        if entry is None:
            raise EventNotFoundError(
                f"{self.name}: event {event_id!r} is not loaded; call get_events first"
            )
        return entry

    def get_events(self, sport: str) -> Sequence[Event]:
        events = []
        for index, provider in enumerate(self._providers):
            for event in provider.get_events(sport):
                namespaced = self._namespaced(index, event.event_id)
                self._routes[namespaced] = (provider, event.event_id)
                events.append(replace(event, event_id=namespaced))
        return tuple(events)

    def get_markets(self, event_id: str) -> Sequence[Market]:
        provider, original_id = self._route(event_id)
        return provider.get_markets(original_id)

    def get_odds(self, event_id: str, market: str) -> Sequence[NormalizedOdds]:
        provider, original_id = self._route(event_id)
        return provider.get_odds(original_id, market)
