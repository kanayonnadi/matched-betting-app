"""Read-only benchmarks (M24.1, Phases 4-5).

* ``benchmark`` measures the sportsbook<->STX overlap across stake sizes.
* ``run_promotion_benchmark`` follows the exact production discovery pipeline
  (services.discovery) for one promotion and reports where candidates are
  eliminated.

Never places wagers, never substitutes mock data for LIVE. Results are written
timestamped to ``data/``. No keys/account data are stored.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from monitoring import is_stale
from opportunities import discover_live_opportunities
from providers import DEFAULT_ROUTES


def _hedged(opportunity) -> bool:
    if getattr(opportunity, "fully_hedged", None) is not None:
        return bool(opportunity.fully_hedged)
    return bool(opportunity.liquidity_sufficient)


def _median(values):
    values = [Decimal(str(v)) for v in values if v is not None]
    if not values:
        return None
    return sorted(values)[len(values) // 2]


def _source_mode(provider) -> str:
    return "LIVE" if getattr(provider, "source_type", "MOCK") == "LIVE" else "DEMO"


def benchmark(stakes, book, exchange, routes=DEFAULT_ROUTES, now=None) -> dict:
    rows = []
    for stake in stakes:
        opportunities = list(
            discover_live_opportunities(book, exchange, list(routes), float(stake))
        )
        fully = [o for o in opportunities if _hedged(o)]
        executions = [o.execution for o in fully if getattr(o, "execution", None) is not None]
        slippages = [o.lay_slippage_pct for o in fully if getattr(o, "lay_slippage_pct", None) is not None]
        rows.append(
            {
                "stake": str(stake),
                "opportunities": len(opportunities),
                "fully_hedgeable": len(fully),
                "rejected_insufficient_depth": len(opportunities) - len(fully),
                "rejected_stale": sum(1 for o in opportunities if is_stale(o.timestamp)),
                "median_depth": str(_median([e.fillable_contracts for e in executions])) if executions else None,
                "median_slippage_pct": str(_median(slippages)) if slippages else None,
                "max_slippage_pct": str(max(slippages)) if slippages else None,
                "median_qualifying_loss": str(_median([o.qualifying_loss for o in fully])) if fully else None,
                "median_liability": str(_median([o.lay_liability for o in fully])) if fully else None,
                "liquidity_grades": {
                    grade: sum(1 for o in fully if o.liquidity_grade == grade)
                    for grade in ("HIGH", "MEDIUM", "LOW")
                },
            }
        )
    return {
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "provider_mode": _source_mode(book),
        "exchange_mode": _source_mode(exchange),
        "provenance": "live providers" if _source_mode(book) == "LIVE" else "simulated providers",
        "stakes": rows,
    }


def _write(result: dict, name: str, output_dir="data"):
    out_dir = Path(__file__).resolve().parent.parent / output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"{name}-{stamp}.json"
    path.write_text(json.dumps(result, indent=2))
    result["path"] = str(path)
    return result


def run_live_benchmark(stakes=(10, 25, 50, 100), output_dir="data") -> dict:
    from env import load_env
    from providers import CachedProvider, build_providers

    load_env()
    from providers.base import ProviderUnavailableError

    providers = build_providers()
    if not (providers.live and providers.exchange_live):
        return {"status": "BLOCKED", "reason": "Live feeds are not configured (missing credentials)."}
    # Preflight so a rejected/unavailable provider is reported BLOCKED, not
    # silently as "zero opportunities".
    try:
        providers.sportsbook.get_events("basketball_nba")
    except ProviderUnavailableError as exc:
        return {"status": "BLOCKED", "reason": f"sportsbook provider unavailable: {exc}"}
    try:
        providers.exchange.get_events("Basketball")
    except Exception as exc:  # noqa: BLE001
        return {"status": "BLOCKED", "reason": f"exchange provider unavailable: {exc}"}
    book = CachedProvider(providers.sportsbook, ttl_seconds=120)
    exchange = CachedProvider(providers.exchange, ttl_seconds=120)
    result = benchmark(stakes, book, exchange)
    result["status"] = "OK"
    return _write(result, "benchmark", output_dir)


def run_promotion_benchmark(
    promotion, mode="LIVE", book=None, exchange=None, routes=None, output_dir="data", save=True
) -> dict:
    """Run one promotion through the production discovery pipeline."""
    from services.discovery import discover_qualifying

    try:
        result = discover_qualifying(promotion, mode, book=book, exchange=exchange, routes=routes)
    except Exception as exc:  # noqa: BLE001
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "promotion": promotion.name,
            "sportsbook": promotion.sportsbook,
            "mode": mode,
            "status": "BLOCKED",
            "reason": str(exc),
        }
        return _write(payload, "promotion-benchmark", output_dir) if save else payload

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "promotion": promotion.name,
        "sportsbook": promotion.sportsbook,
        "mode": mode,
        "status": result.status,
        "bookmaker_key": result.bookmaker_key,
        "diagnostics": list(result.diagnostics),
        "message": result.message,
        "recommendations": [
            {
                "event": r.opportunity.event_label,
                "selection": r.opportunity.selection,
                "book": r.opportunity.book_provider,
                "back_odds": str(r.opportunity.back_odds),
                "effective_lay_odds": str(r.opportunity.effective_lay_odds),
                "worst_case_pnl": str(r.worst_case_pnl),
                "capital": str(r.capital),
                "liquidity_grade": r.liquidity_grade,
                "fully_hedged": r.fully_hedged,
                "stale": r.stale,
                "eligibility": r.eligibility,
                "source_mode": r.provenance.get("source_mode"),
                "depth_completeness": r.provenance.get("depth_completeness"),
            }
            for r in result.recommendations
        ],
    }
    return _write(payload, "promotion-benchmark", output_dir) if save else payload


if __name__ == "__main__":
    print(json.dumps(run_live_benchmark(), indent=2))
