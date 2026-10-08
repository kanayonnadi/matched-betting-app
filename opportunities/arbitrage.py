"""Arbitrage detection across sportsbooks.

Reuses the normalized odds and event naming so the same fixture from different
books is grouped. Best odds per selection are combined; if their inverse odds sum
below 1, an arbitrage is returned with optimal stakes. Deliberately kept separate
from promotional matched-betting opportunities.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from calculators import calculate_arbitrage
from matching import normalize_name
from models import MarketType

EXPECTED_SELECTIONS = {
    MarketType.MONEYLINE: 2,
    MarketType.TWO_WAY: 2,
    MarketType.THREE_WAY: 3,
}


@dataclass(frozen=True)
class ArbitrageLeg:
    selection: str
    decimal_odds: Decimal
    provider: str
    stake: Decimal


@dataclass(frozen=True)
class ArbitrageOpportunity:
    event_label: str
    sport: str
    league: str
    market: str
    market_type: MarketType
    total_stake: Decimal
    sum_inverses: Decimal
    profit: Decimal
    roi_percent: Decimal
    legs: tuple
    timestamp: datetime


def _event_key(sport, event, market):
    return (
        normalize_name(sport),
        normalize_name(event.home_team),
        normalize_name(event.away_team),
        normalize_name(market.name),
        event.start_time.date().isoformat(),
    )


def discover_arbitrage(book_providers, sports, total_stake):
    groups = {}
    for provider in book_providers:
        for sport in sports:
            for event in provider.get_events(sport):
                for market in provider.get_markets(event.event_id):
                    if market.market_type not in EXPECTED_SELECTIONS:
                        continue
                    key = _event_key(sport, event, market)
                    bucket = groups.setdefault(
                        key, {"event": event, "market": market, "best": {}}
                    )
                    for quote in provider.get_odds(event.event_id, market.name):
                        best = bucket["best"]
                        current = best.get(quote.selection)
                        if current is None or quote.decimal_odds > current.decimal_odds:
                            best[quote.selection] = quote

    results = []
    for bucket in groups.values():
        market = bucket["market"]
        best = bucket["best"]
        expected = EXPECTED_SELECTIONS.get(market.market_type, 0)
        if len(best) != expected:
            continue

        selections = list(best.keys())
        odds = [best[selection].decimal_odds for selection in selections]
        arbitrage = calculate_arbitrage(odds, total_stake)
        if arbitrage is None:
            continue

        legs = tuple(
            ArbitrageLeg(
                selection=selection,
                decimal_odds=arbitrage.outcome_odds[index],
                provider=best[selection].provider,
                stake=arbitrage.stakes[index],
            )
            for index, selection in enumerate(selections)
        )
        event = bucket["event"]
        timestamp = min(quote.timestamp for quote in best.values())
        results.append(
            ArbitrageOpportunity(
                event_label=f"{event.home_team} vs {event.away_team}",
                sport=event.sport,
                league=event.league,
                market=market.name,
                market_type=market.market_type,
                total_stake=arbitrage.total_stake,
                sum_inverses=arbitrage.sum_inverses,
                profit=arbitrage.profit,
                roi_percent=arbitrage.roi_percent,
                legs=legs,
                timestamp=timestamp,
            )
        )
    return results
