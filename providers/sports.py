"""Routing between The Odds API sport keys and STX sport names.

Both feeds use different sport vocabularies, so live discovery iterates these
routes: fetch book events for each ``odds_api_keys`` entry and STX events for the
single ``stx_sport`` label, then match.
"""

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class SportRoute:
    canonical: str
    label: str
    odds_api_keys: Tuple[str, ...]
    stx_sport: str


DEFAULT_ROUTES = (
    SportRoute("basketball", "Basketball", ("basketball_nba", "basketball_wnba"), "Basketball"),
    SportRoute("ice_hockey", "Ice Hockey", ("icehockey_nhl",), "Hockey"),
    SportRoute("baseball", "Baseball", ("baseball_mlb",), "Baseball"),
    SportRoute("american_football", "American Football", ("americanfootball_nfl",), "Football"),
)


def routes_by_canonical(routes=DEFAULT_ROUTES) -> dict:
    return {route.canonical: route for route in routes}


def select_routes(canonicals, routes=DEFAULT_ROUTES):
    mapping = routes_by_canonical(routes)
    return tuple(mapping[name] for name in canonicals if name in mapping)
