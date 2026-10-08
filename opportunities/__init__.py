"""Opportunity discovery, evaluation, ranking and filtering."""

from .arbitrage import (
    ArbitrageLeg,
    ArbitrageOpportunity,
    discover_arbitrage,
)
from .discovery import (
    default_mock_sports,
    discover_mock_opportunities,
    discover_opportunities,
)
from .engine import BetKind, Opportunity, build_opportunity
from .filters import OpportunityFilter
from .live import discover_live_opportunities
from .ranking import DEFAULT_SORT, SORT_KEYS, rank_opportunities, sort_key

__all__ = [
    "DEFAULT_SORT",
    "SORT_KEYS",
    "ArbitrageLeg",
    "ArbitrageOpportunity",
    "BetKind",
    "Opportunity",
    "OpportunityFilter",
    "build_opportunity",
    "default_mock_sports",
    "discover_arbitrage",
    "discover_live_opportunities",
    "discover_mock_opportunities",
    "discover_opportunities",
    "rank_opportunities",
    "sort_key",
]
