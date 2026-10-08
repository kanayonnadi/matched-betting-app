"""Odds-provider interface.

The application must not depend on a single odds source. Every adapter
(sportsbook, exchange, mock, commercial feed) implements ``OddsProvider`` and
returns the normalized models from :mod:`models`. No adapter may automate
logins or circumvent a bookmaker's controls.
"""

from abc import ABC, abstractmethod
from typing import Sequence

from models import Event, Market, NormalizedOdds


class OddsProviderError(Exception):
    """Base class for recoverable provider failures."""


class ProviderUnavailableError(OddsProviderError):
    """The provider could not be reached or is temporarily down."""


class EventNotFoundError(OddsProviderError):
    """The requested event id is unknown to the provider."""


class OddsProvider(ABC):
    """Read-only access to events, markets and odds for one source."""

    name = "base"
    # Default to MOCK so a source is never assumed live. Live adapters override.
    source_type = "MOCK"

    @abstractmethod
    def get_events(self, sport: str) -> Sequence[Event]:
        """Return upcoming events for ``sport``."""

    @abstractmethod
    def get_markets(self, event_id: str) -> Sequence[Market]:
        """Return markets available for ``event_id``."""

    @abstractmethod
    def get_odds(self, event_id: str, market: str) -> Sequence[NormalizedOdds]:
        """Return normalized quotes for ``market`` of ``event_id``.

        Exchange adapters return :class:`~models.ExchangeOdds` instances.
        """
