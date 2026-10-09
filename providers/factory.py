"""Build provider sets from configuration.

Live sportsbook odds can be enabled with environment variables. Several
bookmakers may be configured; they are combined behind
:class:`~providers.aggregate.MultiSportsbookProvider`.

The exchange side uses STX (Ontario) when ``STX_KEY_ID`` and a private key path
are configured, otherwise the mock. Exchange adapters never automate logins.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from logging_config import get_logger

from .aggregate import MultiSportsbookProvider
from .base import ProviderUnavailableError
from .config import LiveOddsConfig, StxConfig
from .mock import MockExchangeProvider, MockSportsbookProvider
from .stx import StxProvider
from .theoddsapi import TheOddsApiProvider

PROJECT_ROOT = Path(__file__).resolve().parent.parent
logger = get_logger(__name__)


@dataclass(frozen=True)
class ProviderSet:
    sportsbook: object
    exchange: object
    live: bool
    exchange_live: bool = False


def build_sportsbook_providers(config: Optional[LiveOddsConfig] = None) -> List[object]:
    config = config or LiveOddsConfig.from_env()
    if not config.enabled:
        return [MockSportsbookProvider()]
    return [
        TheOddsApiProvider(
            config.api_key,
            bookmaker,
            region=config.region,
            markets=config.markets,
        )
        for bookmaker in config.bookmakers
    ]


def build_sportsbook_provider(config: Optional[LiveOddsConfig] = None):
    providers = build_sportsbook_providers(config)
    if len(providers) == 1:
        return providers[0]
    return MultiSportsbookProvider(providers)


def build_bookmaker_provider(bookmaker_key: str, config: Optional[LiveOddsConfig] = None):
    """A sportsbook provider scoped to exactly one bookmaker (no substitution)."""
    config = config or LiveOddsConfig.from_env()
    if not config.api_key:
        raise ProviderUnavailableError(
            "The Odds API key is not configured; cannot fetch live odds."
        )
    return TheOddsApiProvider(
        config.api_key,
        bookmaker_key,
        region=config.region or "ca",
        markets=config.markets,
    )


def _resolve_private_key(path: str) -> str:
    key_path = Path(path)
    if not key_path.is_absolute():
        key_path = PROJECT_ROOT / key_path
    return key_path.read_text()


def build_exchange_provider(stx_config: Optional[StxConfig] = None):
    stx_config = stx_config or StxConfig.from_env()
    if stx_config.enabled:
        return StxProvider(
            stx_config.key_id,
            _resolve_private_key(stx_config.private_key_path),
            base_url=stx_config.base_url,
            fee_factor=stx_config.fee_factor,
        )
    return MockExchangeProvider()


def build_providers(
    config: Optional[LiveOddsConfig] = None,
    stx_config: Optional[StxConfig] = None,
) -> ProviderSet:
    config = config or LiveOddsConfig.from_env()
    stx_config = stx_config or StxConfig.from_env()
    providers = ProviderSet(
        sportsbook=build_sportsbook_provider(config),
        exchange=build_exchange_provider(stx_config),
        live=config.enabled,
        exchange_live=stx_config.enabled,
    )
    logger.info(
        "providers built: sportsbook=%s live=%s exchange=%s live=%s",
        type(providers.sportsbook).__name__,
        providers.live,
        type(providers.exchange).__name__,
        providers.exchange_live,
    )
    return providers
