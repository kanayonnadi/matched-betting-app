"""Ranking for opportunities.

The default view prioritizes LOW qualifying loss (as a percentage of stake),
not simply the highest odds.
"""

from typing import Callable, Iterable, Optional

from .engine import Opportunity

DEFAULT_SORT = "qualifying_loss_pct"


def _match_confidence(opportunity: Opportunity) -> float:
    return opportunity.event_confidence if opportunity.event_confidence is not None else 0.0


SORT_KEYS: dict = {
    "qualifying_loss_pct": lambda o: o.qualifying_loss_pct,
    "qualifying_loss": lambda o: o.qualifying_loss,
    "expected_profit": lambda o: o.expected_profit,
    "back_odds": lambda o: o.back_odds,
    "lay_odds": lambda o: o.lay_odds,
    "required_liability": lambda o: o.lay_liability,
    "required_capital": lambda o: o.required_capital,
    "rating_percent": lambda o: o.rating_percent,
    "available_size": lambda o: o.available_size,
    "bookmaker": lambda o: o.book_provider,
    "exchange": lambda o: o.exchange_provider,
    "sport": lambda o: o.sport,
    "league": lambda o: o.league,
    "start_time": lambda o: o.start_time,
    "selection": lambda o: o.selection,
    "match_confidence": _match_confidence,
    "conversion_rate": lambda o: o.conversion_rate if o.conversion_rate is not None else 0,
}


def sort_key(name: str) -> Callable[[Opportunity], object]:
    try:
        return SORT_KEYS[name]
    except KeyError:
        raise ValueError(
            f"unknown sort key {name!r}; expected one of {sorted(SORT_KEYS)}"
        ) from None


def rank_opportunities(
    opportunities: Iterable[Opportunity],
    sort_by: str = DEFAULT_SORT,
    ascending: bool = True,
    limit: Optional[int] = None,
) -> list:
    ordered = sorted(opportunities, key=sort_key(sort_by), reverse=not ascending)
    if limit is not None:
        return ordered[:limit]
    return ordered
