"""Odds provider adapters."""

from .aggregate import MultiSportsbookProvider
from .base import (
    EventNotFoundError,
    OddsProvider,
    OddsProviderError,
    ProviderUnavailableError,
)
from .cache import CachedProvider
from .config import LiveOddsConfig, StxConfig
from .bookmakers import CA_BOOKMAKER_KEYS, BookmakerResolution, resolve_bookmaker
from .factory import (
    ProviderSet,
    build_bookmaker_provider,
    build_exchange_provider,
    build_providers,
    build_sportsbook_provider,
    build_sportsbook_providers,
)
from .mock import (
    DEFAULT_COMMISSION,
    DEFAULT_LIQUIDITY,
    MOCK_EVENTS,
    MOCK_EVENTS_ALT,
    MockEvent,
    MockExchangeProvider,
    MockOutcome,
    MockSecondSportsbookProvider,
    MockSportsbookProvider,
)
from .stx import (
    STX_DEMO_CA,
    STX_DEMO_US,
    STX_PRODUCTION_CA,
    StxClient,
    StxProvider,
    StxSigner,
    load_private_key,
)
from .stx_orderbook import StxOrderBookClient
from .sports import DEFAULT_ROUTES, SportRoute, routes_by_canonical, select_routes
from .theoddsapi import (
    CA_BOOKMAKERS,
    SPORT_KEY_TO_SPORT,
    THE_ODDS_API_BASE,
    TheOddsApiProvider,
    normalize_sport_key,
    parse_time,
)

__all__ = [
    "CA_BOOKMAKERS",
    "CachedProvider",
    "DEFAULT_COMMISSION",
    "DEFAULT_LIQUIDITY",
    "EventNotFoundError",
    "LiveOddsConfig",
    "MOCK_EVENTS",
    "MOCK_EVENTS_ALT",
    "MockEvent",
    "MockExchangeProvider",
    "MockOutcome",
    "MockSecondSportsbookProvider",
    "MockSportsbookProvider",
    "MultiSportsbookProvider",
    "OddsProvider",
    "OddsProviderError",
    "ProviderSet",
    "ProviderUnavailableError",
    "CA_BOOKMAKER_KEYS",
    "BookmakerResolution",
    "DEFAULT_ROUTES",
    "SPORT_KEY_TO_SPORT",
    "STX_DEMO_CA",
    "STX_DEMO_US",
    "STX_PRODUCTION_CA",
    "StxClient",
    "StxConfig",
    "SportRoute",
    "StxOrderBookClient",
    "StxProvider",
    "StxSigner",
    "THE_ODDS_API_BASE",
    "TheOddsApiProvider",
    "build_bookmaker_provider",
    "build_exchange_provider",
    "build_providers",
    "build_sportsbook_provider",
    "build_sportsbook_providers",
    "load_private_key",
    "normalize_sport_key",
    "parse_time",
    "resolve_bookmaker",
    "routes_by_canonical",
    "select_routes",
]
