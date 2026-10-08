"""Event and market matching."""

from .confidence import (
    AMBIGUOUS_MARGIN,
    CONFIRMED_MATCH,
    REVIEW_MATCH,
    MatchStatus,
    classify,
    is_confirmed,
)
from .event_matcher import EventMatch, EventMatcher, MatchComponents, time_similarity
from .market_matcher import (
    EXPECTED_SELECTIONS,
    MARKET_ALIASES,
    SUPPORTED_TYPES,
    MarketMatch,
    MarketMatcher,
    SelectionMatch,
    canonical_market_name,
)
from .normalize import SPORT_ALIASES, name_similarity, normalize_name, normalize_sport, strip_accents

__all__ = [
    "AMBIGUOUS_MARGIN",
    "CONFIRMED_MATCH",
    "REVIEW_MATCH",
    "EXPECTED_SELECTIONS",
    "MARKET_ALIASES",
    "SUPPORTED_TYPES",
    "EventMatch",
    "EventMatcher",
    "MarketMatch",
    "MarketMatcher",
    "MatchComponents",
    "MatchStatus",
    "SelectionMatch",
    "canonical_market_name",
    "classify",
    "is_confirmed",
    "SPORT_ALIASES",
    "name_similarity",
    "normalize_name",
    "normalize_sport",
    "strip_accents",
    "time_similarity",
]
