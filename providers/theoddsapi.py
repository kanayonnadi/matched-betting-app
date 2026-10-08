"""The Odds API sportsbook adapter.

The Odds API (https://the-odds-api.com) is a legitimate, documented odds
aggregator with a free tier. This adapter maps its ``h2h`` (moneyline /
match-winner) markets into the normalized models. It was only ever configured
for BACK quotes; exchange lay odds must come from a separate exchange adapter.

No logins are automated and no bookmaker controls are circumvented. Supply your
own API key via configuration.
"""

from datetime import datetime, timezone
from typing import Optional, Sequence

import requests

from models import Event, Market, MarketType, NormalizedOdds, Side

from .base import EventNotFoundError, OddsProvider, ProviderUnavailableError

THE_ODDS_API_BASE = "https://api.the-odds-api.com/v4"

CA_BOOKMAKERS = (
    "bet99_ca_on",
    "betano_ca_on",
    "betmgm_ca_on",
    "betrivers_ca_on",
    "playnow_ca",
    "pointsbetca",
    "proline_ca_on",
    "sportsinteraction_ca_on",
)

SPORT_KEY_TO_SPORT = {
    "basketball_nba": "basketball",
    "basketball_wnba": "basketball",
    "basketball_ncaab": "basketball",
    "baseball_mlb": "baseball",
    "icehockey_nhl": "ice_hockey",
    "americanfootball_nfl": "american_football",
    "americanfootball_ncaaf": "american_football",
    "soccer_epl": "soccer",
    "soccer_uefa_champs_league": "soccer",
    "soccer_usa_mls": "soccer",
}

_TWO_WAY = MarketType.MONEYLINE
_THREE_WAY = MarketType.THREE_WAY


def normalize_sport_key(sport_key: str) -> str:
    return SPORT_KEY_TO_SPORT.get(sport_key, sport_key)


def parse_time(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _market_name_and_type(outcome_count: int):
    if outcome_count == 2:
        return "moneyline", _TWO_WAY
    if outcome_count == 3:
        return "match_winner", _THREE_WAY
    return None, None


def _requests_json(url, params):
    from .quota import get_usage_tracker

    tracker = get_usage_tracker()
    try:
        response = requests.get(url, params=params, timeout=15)
    except requests.RequestException as exc:
        tracker.record_error()
        raise ProviderUnavailableError(f"The Odds API request failed: {exc}") from exc

    tracker.record(getattr(response, "headers", None) or {}, response.status_code)

    if response.status_code in (401, 403):
        raise ProviderUnavailableError("The Odds API rejected the provided API key")
    if response.status_code == 429:
        raise ProviderUnavailableError("The Odds API rate limit / quota exhausted")
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise ProviderUnavailableError(f"The Odds API error: {exc}") from exc
    return response.json()


class TheOddsApiProvider(OddsProvider):
    """Sportsbook BACK odds from a single configured bookmaker."""

    source_type = "LIVE"

    def __init__(
        self,
        api_key: str,
        bookmaker: str,
        region: str = "us",
        markets: Sequence[str] = ("h2h",),
        base_url: str = THE_ODDS_API_BASE,
        http_get=None,
        clock=None,
        sport_map=None,
    ):
        if not api_key:
            raise ValueError("api_key is required")
        if not bookmaker:
            raise ValueError("bookmaker is required")
        self._api_key = api_key
        self._bookmaker = bookmaker
        self._region = region
        self._markets = tuple(markets)
        self._base_url = base_url.rstrip("/")
        self._http_get = http_get or _requests_json
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sport_map = sport_map or SPORT_KEY_TO_SPORT
        self.name = f"theoddsapi:{bookmaker}"
        self._events = {}

    def _sport(self, sport_key: str) -> str:
        return self._sport_map.get(sport_key, sport_key)

    def _fetch(self, sport_key: str):
        params = {
            "apiKey": self._api_key,
            "regions": self._region,
            "markets": ",".join(self._markets),
            "oddsFormat": "decimal",
            "bookmakers": self._bookmaker,
        }
        data = self._http_get(f"{self._base_url}/sports/{sport_key}/odds", params)
        if not isinstance(data, list):
            raise ProviderUnavailableError("unexpected payload from The Odds API")
        return data

    def _bookmaker_entry(self, event):
        for bookmaker in event.get("bookmakers", []):
            if bookmaker.get("key") == self._bookmaker:
                return bookmaker
        return None

    def _load(self, sport_key: str) -> None:
        for event in self._fetch(sport_key):
            event_id = event.get("id")
            if not event_id:
                continue
            if self._bookmaker_entry(event) is None:
                continue
            self._events[event_id] = event

    def _require(self, event_id: str):
        event = self._events.get(event_id)
        if event is None:
            raise EventNotFoundError(
                f"{self.name}: event {event_id!r} is not loaded; call get_events first"
            )
        return event

    def get_events(self, sport: str) -> Sequence[Event]:
        self._load(sport)
        events = []
        for event in self._events.values():
            if event.get("sport_key") != sport:
                continue
            home = event.get("home_team")
            away = event.get("away_team")
            commence = event.get("commence_time")
            if not home or not away or not commence:
                continue
            events.append(
                Event(
                    provider=self.name,
                    event_id=event["id"],
                    sport=self._sport(event.get("sport_key", sport)),
                    league=event.get("sport_title") or event.get("sport_key", sport),
                    home_team=home,
                    away_team=away,
                    start_time=parse_time(commence),
                )
            )
        return tuple(events)

    def _h2h_markets(self, event):
        bookmaker = self._bookmaker_entry(event)
        if bookmaker is None:
            return []
        result = []
        for market in bookmaker.get("markets", []):
            if market.get("key") != "h2h":
                continue
            name, market_type = _market_name_and_type(len(market.get("outcomes", [])))
            if name is None:
                continue
            result.append((name, market_type, market))
        return result

    def get_markets(self, event_id: str) -> Sequence[Market]:
        event = self._require(event_id)
        markets = []
        for name, market_type, market in self._h2h_markets(event):
            markets.append(
                Market(
                    provider=self.name,
                    event_id=event_id,
                    name=name,
                    market_type=market_type,
                    selections=tuple(outcome["name"] for outcome in market["outcomes"]),
                )
            )
        return tuple(markets)

    def get_odds(self, event_id: str, market: str) -> Sequence[NormalizedOdds]:
        event = self._require(event_id)
        sport = self._sport(event.get("sport_key", ""))
        league = event.get("sport_title") or event.get("sport_key", "")
        start_time = parse_time(event["commence_time"])
        quotes = []
        for name, _market_type, raw_market in self._h2h_markets(event):
            if name != market:
                continue
            if raw_market.get("last_update"):
                timestamp = parse_time(raw_market["last_update"])
            else:
                timestamp = self._clock()
            for outcome in raw_market["outcomes"]:
                price = outcome.get("price")
                if price is None:
                    continue
                quotes.append(
                    NormalizedOdds(
                        provider=self.name,
                        event_id=event_id,
                        sport=sport,
                        league=league,
                        home_team=event["home_team"],
                        away_team=event["away_team"],
                        start_time=start_time,
                        market=name,
                        selection=outcome["name"],
                        side=Side.BACK,
                        decimal_odds=price,
                        timestamp=timestamp,
                    )
                )
        return tuple(quotes)
