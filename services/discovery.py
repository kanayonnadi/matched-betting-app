"""One-input, bookmaker-scoped opportunity discovery.

Given a parsed promotion, this fetches *only that sportsbook's* odds, matches
them to STX, applies eligibility gates, checks executable depth, and returns
ranked recommendations plus precise no-opportunity diagnostics.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional, Tuple

from monitoring import is_stale
from opportunities import BetKind, discover_live_opportunities, discover_mock_opportunities
from providers import DEFAULT_ROUTES, CachedProvider, build_bookmaker_provider
from providers import resolve_bookmaker
from providers.base import ProviderUnavailableError

from .promotion_workflow import (
    LiveDataUnavailable,
    Recommendation,
    _recommendation,
    rank_conversion_bets,
    rank_qualifying_bets,
)

class DiagnosticCode(str, Enum):
    OK = "OK"
    SPORTSBOOK_UNKNOWN = "SPORTSBOOK_UNKNOWN"
    SPORTSBOOK_UNSUPPORTED = "SPORTSBOOK_UNSUPPORTED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    NO_ELIGIBLE_MARKETS = "NO_ELIGIBLE_MARKETS"
    NO_STX_MARKETS = "NO_STX_MARKETS"
    INSUFFICIENT_LIQUIDITY = "INSUFFICIENT_LIQUIDITY"
    UNFAVORABLE_PRICING = "UNFAVORABLE_PRICING"
    STALE_DATA = "STALE_DATA"


class OpportunityStatus(str, Enum):
    LIVE_VERIFIED = "LIVE VERIFIED"
    LIVE_STALE = "LIVE STALE"
    SIMULATED = "SIMULATED"
    HEDGE_INCOMPLETE = "HEDGE INCOMPLETE"
    UNAVAILABLE = "UNAVAILABLE"


_MESSAGES = {
    DiagnosticCode.SPORTSBOOK_UNKNOWN: "Sportsbook could not be identified from the promotion.",
    DiagnosticCode.SPORTSBOOK_UNSUPPORTED: "Sportsbook is not available in The Odds API Canadian feed.",
    DiagnosticCode.PROVIDER_UNAVAILABLE: "Live odds/exchange providers are not configured or unavailable.",
    DiagnosticCode.NO_ELIGIBLE_MARKETS: "No eligible markets were found for this promotion's sportsbook.",
    DiagnosticCode.NO_STX_MARKETS: "No equivalent STX markets were found for the eligible events.",
    DiagnosticCode.INSUFFICIENT_LIQUIDITY: "Matching markets exist but cannot be fully hedged at current STX depth.",
    DiagnosticCode.UNFAVORABLE_PRICING: "Matching markets exist but pricing is unfavorable.",
    DiagnosticCode.STALE_DATA: "Quotes are stale; refresh to get current prices.",
}


@dataclass(frozen=True)
class DiscoveryResult:
    recommendations: Tuple[Recommendation, ...]
    diagnostics: Tuple[str, ...]
    status: str
    message: str
    mode: str
    bookmaker_key: Optional[str] = None


def classify_recommendation(rec: Recommendation, mode: str) -> str:
    if rec.source_type != "LIVE":
        return OpportunityStatus.SIMULATED.value
    if rec.stale:
        return OpportunityStatus.LIVE_STALE.value
    if not rec.fully_hedged:
        return OpportunityStatus.HEDGE_INCOMPLETE.value
    return OpportunityStatus.LIVE_VERIFIED.value


def _eligible(promotion, opportunities):
    """Application of promotion eligibility rules before ranking."""
    excluded = set(promotion.excluded_markets or ())
    return [o for o in opportunities if not (excluded and o.market in excluded)]


def _live_pair_for(bookmaker_key, book, exchange):
    if book is None:
        book = build_bookmaker_provider(bookmaker_key)
    if exchange is None:
        raise LiveDataUnavailable(
            "STX exchange is not configured; cannot verify executable liquidity."
        )
    return book, exchange


def _discover(promotion, mode, stake, kind, book=None, exchange=None, routes=None):
    routes = list(routes) if routes is not None else list(DEFAULT_ROUTES)
    raw = []
    if mode == "LIVE":
        resolution = resolve_bookmaker(promotion.sportsbook)
        if resolution.key is None:
            code = DiagnosticCode.SPORTSBOOK_UNKNOWN
            return DiscoveryResult((), (code.value,), OpportunityStatus.UNAVAILABLE.value, _MESSAGES[code], mode)
        if not resolution.supported:
            return DiscoveryResult(
                (), (DiagnosticCode.SPORTSBOOK_UNSUPPORTED.value,),
                OpportunityStatus.UNAVAILABLE.value,
                f"{_MESSAGES[DiagnosticCode.SPORTSBOOK_UNSUPPORTED]} ({resolution.reason})",
                mode, resolution.key,
            )
        try:
            book_provider, exchange_provider = _live_pair_for(resolution.key, book, exchange)
            raw = list(
                discover_live_opportunities(
                    CachedProvider(book_provider, ttl_seconds=30),
                    CachedProvider(exchange_provider, ttl_seconds=30),
                    routes,
                    float(stake),
                    kind=kind,
                )
            )
        except (ProviderUnavailableError, LiveDataUnavailable) as exc:
            return DiscoveryResult(
                (), (DiagnosticCode.PROVIDER_UNAVAILABLE.value,),
                OpportunityStatus.UNAVAILABLE.value, str(exc), mode, resolution.key,
            )
        result = _rank(promotion, raw, kind, mode, stake)
        return _with_bookmaker(result, resolution.key)
    # DEMO
    raw = list(discover_mock_opportunities(float(stake), kind=kind))
    result = _rank(promotion, raw, kind, mode, stake)
    return _with_bookmaker(result, None)


def _with_bookmaker(result, key):
    return DiscoveryResult(result.recommendations, result.diagnostics, result.status, result.message, result.mode, key)


def _rank(promotion, raw, kind, mode, amount):
    eligible = _eligible(promotion, raw)
    if not eligible:
        code = DiagnosticCode.NO_ELIGIBLE_MARKETS
        return DiscoveryResult((), (code.value,), OpportunityStatus.UNAVAILABLE.value, _MESSAGES[code], mode)

    if kind is BetKind.QUALIFYING:
        recommendations = rank_qualifying_bets(promotion, eligible, max_results=4)
        all_candidates = rank_qualifying_bets(promotion, eligible, max_results=1000)
    else:
        recommendations = rank_conversion_bets(amount, eligible, max_results=4)
        all_candidates = rank_conversion_bets(amount, eligible, max_results=1000)

    if not recommendations:
        # Distinguish insufficient liquidity from no matches.
        code = DiagnosticCode.INSUFFICIENT_LIQUIDITY
        return DiscoveryResult((), (code.value,), OpportunityStatus.UNAVAILABLE.value, _MESSAGES[code], mode)

    diagnostics = []
    if any(r.stale for r in recommendations):
        diagnostics.append(DiagnosticCode.STALE_DATA.value)
    status = classify_recommendation(recommendations[0], mode)
    message = "OK"
    return DiscoveryResult(
        tuple(recommendations), tuple(diagnostics), status, message, mode
    )


def discover_qualifying(promotion, mode, stake=None, book=None, exchange=None, routes=None):
    stake = stake or promotion.qualifying_stake or Decimal("10")
    return _discover(promotion, mode, stake, BetKind.QUALIFYING, book=book, exchange=exchange, routes=routes)


def discover_conversion(promotion, token_amount, mode, book=None, exchange=None, routes=None):
    return _discover(promotion, mode, token_amount, BetKind.FREE_BET_SNR, book=book, exchange=exchange, routes=routes)
