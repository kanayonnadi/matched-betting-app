"""Read-only live benchmark (M24, Phase 8).

Measures the sportsbook<->STX overlap across stake sizes using actual provider
responses. Never places wagers, never substitutes mock data. Results are written
timestamped to ``data/`` for reproducibility. API keys are never included.
"""

import json
import statistics
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

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


def benchmark(stakes, book, exchange, routes=DEFAULT_ROUTES, now=None) -> dict:
    rows = []
    for stake in stakes:
        opportunities = list(
            discover_live_opportunities(book, exchange, list(routes), float(stake))
        )
        fully = [o for o in opportunities if _hedged(o)]
        executions = [o.execution for o in fully if getattr(o, "execution", None) is not None]
        rows.append(
            {
                "stake": str(stake),
                "opportunities": len(opportunities),
                "fully_hedgeable": len(fully),
                "median_depth": str(_median([e.fillable_contracts for e in executions])) if executions else None,
                "median_slippage_pct": str(_median([o.lay_slippage_pct for o in fully])) if fully else None,
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
        "stakes": rows,
    }


def run_live_benchmark(stakes=(10, 25, 50, 100), output_dir="data") -> dict:
    from env import load_env
    from providers import CachedProvider, build_providers

    load_env()
    providers = build_providers()
    if not (providers.live and providers.exchange_live):
        return {"status": "BLOCKED", "reason": "Live feeds are not configured (missing credentials)."}
    # Cache once and reuse across stakes so we fetch each market a single time.
    book = CachedProvider(providers.sportsbook, ttl_seconds=120)
    exchange = CachedProvider(providers.exchange, ttl_seconds=120)
    result = benchmark(stakes, book, exchange)
    result["status"] = "OK"
    out_dir = Path(__file__).resolve().parent.parent / output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"benchmark-{stamp}.json"
    path.write_text(json.dumps(result, indent=2))
    result["path"] = str(path)
    return result


if __name__ == "__main__":
    print(json.dumps(run_live_benchmark(), indent=2))
