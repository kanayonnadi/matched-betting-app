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
    INELIGIBLE = "INELIGIBLE"
    INSUFFICIENT_LIQUIDITY = "INSUFFICIENT_LIQUIDITY"
    UNKNOWN_DEPTH = "UNKNOWN_DEPTH"
    INVALID_DEPTH = "INVALID_DEPTH"
    UNFAVORABLE_PRICING = "UNFAVORABLE_PRICING"
    STALE_DATA = "STALE_DATA"


class OpportunityStatus(str, Enum):
    LIVE_VERIFIED = "LIVE VERIFIED"
    LIVE_STALE = "LIVE STALE"
    SIMULATED = "SIMULATED"
    HEDGE_INCOMPLETE = "HEDGE INCOMPLETE"
    UNKNOWN_DEPTH = "UNKNOWN DEPTH"
    INSUFFICIENT_DEPTH = "INSUFFICIENT DEPTH"
    INVALID_DEPTH = "INVALID DEPTH"
    UNAVAILABLE = "UNAVAILABLE"


_MESSAGES = {
    DiagnosticCode.SPORTSBOOK_UNKNOWN: "Sportsbook could not be identified from the promotion.",
    DiagnosticCode.SPORTSBOOK_UNSUPPORTED: "Sportsbook is not available in The Odds API Canadian feed.",
    DiagnosticCode.PROVIDER_UNAVAILABLE: "Live odds/exchange providers are not configured or unavailable.",
    DiagnosticCode.NO_ELIGIBLE_MARKETS: "No eligible markets were found for this promotion's sportsbook.",
    DiagnosticCode.NO_STX_MARKETS: "No equivalent STX markets were found for the eligible events.",
    DiagnosticCode.INELIGIBLE: "Candidates exist but none satisfy the promotion's eligibility conditions.",
    DiagnosticCode.INSUFFICIENT_LIQUIDITY: "Matching markets exist but cannot be fully hedged at current STX depth.",
    DiagnosticCode.UNKNOWN_DEPTH: "STX depth could not be independently verified; not treated as liquid.",
    DiagnosticCode.INVALID_DEPTH: "STX order-book data was malformed and could not be interpreted.",
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
    diagnostic_recommendations: Tuple[Recommendation, ...] = ()


def classify_recommendation(rec: Recommendation, mode: str) -> str:
    if rec.source_type != "LIVE":
        return OpportunityStatus.SIMULATED.value
    if rec.stale or rec.depth_status == "STALE":
        return OpportunityStatus.LIVE_STALE.value
    depth = rec.depth_status
    if depth == "VERIFIED" and rec.fully_hedged:
        return OpportunityStatus.LIVE_VERIFIED.value
    if depth == "INSUFFICIENT":
        return OpportunityStatus.INSUFFICIENT_DEPTH.value
    if depth == "INVALID":
        return OpportunityStatus.INVALID_DEPTH.value
    if depth == "UNKNOWN" or depth == "":
        return OpportunityStatus.UNKNOWN_DEPTH.value
    return OpportunityStatus.HEDGE_INCOMPLETE.value


def _eligible(promotion, opportunities):
    """Apply promotion eligibility rules before ranking (M24.1)."""
    from promotions import evaluate_eligibility

    return [o for o in opportunities if evaluate_eligibility(promotion, o).eligible]


def _live_pair_for(bookmaker_key, book, exchange):
    if exchange is None:
        from providers import build_providers

        providers = build_providers()
        if not providers.exchange_live:
            raise LiveDataUnavailable(
                "STX exchange is not configured; cannot verify executable liquidity."
            )
        exchange = providers.exchange
    if book is None:
        book = build_bookmaker_provider(bookmaker_key)
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
            if routes:
                first = routes[0]
                book_provider.get_events(first.odds_api_keys[0])
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
    return DiscoveryResult(
        result.recommendations, result.diagnostics, result.status, result.message,
        result.mode, key, result.diagnostic_recommendations,
    )


def _rank(promotion, raw, kind, mode, amount):
    eligible = _eligible(promotion, raw)
    if not eligible:
        code = DiagnosticCode.INELIGIBLE if raw else DiagnosticCode.NO_ELIGIBLE_MARKETS
        return DiscoveryResult((), (code.value,), OpportunityStatus.UNAVAILABLE.value, _MESSAGES[code], mode)

    if kind is BetKind.QUALIFYING:
        all_candidates = rank_qualifying_bets(promotion, eligible, max_results=1000, require_hedged=False)
    else:
        all_candidates = rank_conversion_bets(
            amount, eligible, max_results=1000, promotion=promotion, require_hedged=False
        )

    if not all_candidates:
        code = DiagnosticCode.INSUFFICIENT_LIQUIDITY
        return DiscoveryResult((), (code.value,), OpportunityStatus.UNAVAILABLE.value, _MESSAGES[code], mode)

    # In LIVE mode only genuinely verified depth is a primary recommendation.
    # DEMO has no real book, so simulated hedgeability is acceptable (labeled).
    def _is_primary(r):
        if mode == "LIVE":
            return r.depth_status == "VERIFIED" and r.fully_hedged
        return r.fully_hedged

    primary = [r for r in all_candidates if _is_primary(r)]
    others = [r for r in all_candidates if not _is_primary(r)]

    if not primary:
        statuses = {r.depth_status for r in others}
        if "INVALID" in statuses:
            code = DiagnosticCode.INVALID_DEPTH
        elif "INSUFFICIENT" in statuses:
            code = DiagnosticCode.INSUFFICIENT_LIQUIDITY
        elif "STALE" in statuses:
            code = DiagnosticCode.STALE_DATA
        else:
            code = DiagnosticCode.UNKNOWN_DEPTH
        return DiscoveryResult(
            (), (code.value,), OpportunityStatus.UNAVAILABLE.value, _MESSAGES[code], mode,
            diagnostic_recommendations=tuple(others[:4]),
        )

    diagnostics = []
    if any(r.stale for r in primary):
        diagnostics.append(DiagnosticCode.STALE_DATA.value)
    status = classify_recommendation(primary[0], mode)
    return DiscoveryResult(
        tuple(primary[:4]), tuple(diagnostics), status, "OK", mode,
        diagnostic_recommendations=tuple(others[:4]),
    )


def discover_qualifying(promotion, mode, stake=None, book=None, exchange=None, routes=None):
    stake = stake or promotion.qualifying_stake or Decimal("10")
    return _discover(promotion, mode, stake, BetKind.QUALIFYING, book=book, exchange=exchange, routes=routes)


def discover_conversion(promotion, token_amount, mode, book=None, exchange=None, routes=None):
    return _discover(promotion, mode, token_amount, BetKind.FREE_BET_SNR, book=book, exchange=exchange, routes=routes)
