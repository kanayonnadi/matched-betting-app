from datetime import datetime, timezone
from decimal import Decimal

import pytest

from providers import ProviderUnavailableError, TheOddsApiProvider
from providers.base import EventNotFoundError
from providers.theoddsapi import parse_time

BASKETBALL_EVENT = {
    "id": "evt-1",
    "sport_key": "basketball_nba",
    "sport_title": "NBA",
    "commence_time": "2026-11-01T19:30:00Z",
    "home_team": "New York Knicks",
    "away_team": "Toronto Raptors",
    "bookmakers": [
        {
            "key": "fanduel",
            "title": "FanDuel",
            "markets": [
                {
                    "key": "h2h",
                    "last_update": "2026-11-01T12:00:00Z",
                    "outcomes": [
                        {"name": "New York Knicks", "price": 1.85},
                        {"name": "Toronto Raptors", "price": 2.10},
                    ],
                },
                {
                    "key": "spreads",
                    "outcomes": [{"name": "New York Knicks", "price": 1.9, "point": -3.5}],
                },
            ],
        },
        {"key": "draftkings", "title": "DraftKings", "markets": []},
    ],
}

SOCCER_EVENT = {
    "id": "evt-2",
    "sport_key": "soccer_epl",
    "sport_title": "Premier League",
    "commence_time": "2026-11-02T15:00:00Z",
    "home_team": "Arsenal",
    "away_team": "Chelsea",
    "bookmakers": [
        {
            "key": "fanduel",
            "title": "FanDuel",
            "last_update": "2026-11-02T10:00:00Z",
            "markets": [
                {
                    "key": "h2h",
                    "last_update": "2026-11-02T10:00:00Z",
                    "outcomes": [
                        {"name": "Arsenal", "price": 2.20},
                        {"name": "Draw", "price": 3.40},
                        {"name": "Chelsea", "price": 3.10},
                    ],
                }
            ],
        }
    ],
}


class FakeTransport:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def __call__(self, url, params):
        self.calls.append((url, params))
        return self.payload


def provider(payload, **overrides):
    options = dict(
        api_key="secret",
        bookmaker="fanduel",
        http_get=FakeTransport(payload),
        clock=lambda: datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc),
    )
    options.update(overrides)
    return TheOddsApiProvider(**options)


def test_name_includes_bookmaker():
    assert provider([BASKETBALL_EVENT]).name == "theoddsapi:fanduel"


def test_missing_credentials_rejected():
    with pytest.raises(ValueError):
        TheOddsApiProvider(api_key="", bookmaker="fanduel")
    with pytest.raises(ValueError):
        TheOddsApiProvider(api_key="secret", bookmaker="")


def test_get_events_normalizes_fields():
    events = provider([BASKETBALL_EVENT]).get_events("basketball_nba")
    assert len(events) == 1
    event = events[0]
    assert event.provider == "theoddsapi:fanduel"
    assert event.event_id == "evt-1"
    assert event.sport == "basketball"
    assert event.league == "NBA"
    assert event.home_team == "New York Knicks"
    assert event.away_team == "Toronto Raptors"
    assert event.start_time == datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc)


def test_request_parameters_are_correct():
    transport = FakeTransport([BASKETBALL_EVENT])
    provider([BASKETBALL_EVENT], http_get=transport).get_events("basketball_nba")
    url, params = transport.calls[0]
    assert url.endswith("/sports/basketball_nba/odds")
    assert params["apiKey"] == "secret"
    assert params["bookmakers"] == "fanduel"
    assert params["markets"] == "h2h"
    assert params["oddsFormat"] == "decimal"


def test_sportsbook_without_configured_bookmaker_is_skipped():
    assert provider([BASKETBALL_EVENT], bookmaker="betmgm").get_events("basketball_nba") == ()


def test_get_markets_returns_only_h2h():
    instance = provider([BASKETBALL_EVENT])
    instance.get_events("basketball_nba")
    markets = instance.get_markets("evt-1")
    assert len(markets) == 1
    assert markets[0].name == "moneyline"
    assert markets[0].selections == ("New York Knicks", "Toronto Raptors")


def test_get_odds_are_back_decimals_with_source_timestamp():
    instance = provider([BASKETBALL_EVENT])
    instance.get_events("basketball_nba")
    quotes = instance.get_odds("evt-1", "moneyline")
    assert len(quotes) == 2
    assert all(q.side.value == "back" for q in quotes)
    assert quotes[0].decimal_odds == Decimal("1.85")
    assert quotes[0].timestamp == datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def test_three_way_soccer_market():
    instance = provider([SOCCER_EVENT])
    instance.get_events("soccer_epl")
    markets = instance.get_markets("evt-2")
    assert markets[0].market_type.value == "three_way"
    assert markets[0].selections == ("Arsenal", "Draw", "Chelsea")
    assert len(instance.get_odds("evt-2", "match_winner")) == 3


def test_unknown_event_raises():
    instance = provider([BASKETBALL_EVENT])
    with pytest.raises(EventNotFoundError):
        instance.get_odds("missing", "moneyline")


def test_clock_used_when_no_last_update():
    event = dict(BASKETBALL_EVENT)
    event["bookmakers"] = [
        {"key": "fanduel", "markets": [{"key": "h2h", "outcomes": BASKETBALL_EVENT["bookmakers"][0]["markets"][0]["outcomes"]}]}
    ]
    instance = provider([event])
    instance.get_events("basketball_nba")
    quotes = instance.get_odds("evt-1", "moneyline")
    assert quotes[0].timestamp == datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def test_unexpected_payload_rejected():
    instance = provider({"not": "a list"})
    with pytest.raises(ProviderUnavailableError):
        instance.get_events("basketball_nba")


def test_invalid_prices_are_skipped():
    event = dict(BASKETBALL_EVENT)
    event["bookmakers"] = [
        {"key": "fanduel", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Bad", "price": 1.0}, {"name": "Good", "price": 2.5},
        ]}]}
    ]
    instance = provider([event])
    instance.get_events("basketball_nba")
    quotes = instance.get_odds("evt-1", "moneyline")
    assert len(quotes) == 1
    assert quotes[0].selection == "Good"


def test_parse_time_handles_z_suffix():
    parsed = parse_time("2026-11-01T19:30:00Z")
    assert parsed.tzinfo is not None
    assert parsed == datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc)


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code

    def raise_for_status(self):
        raise AssertionError("raise_for_status should not be reached")

    def json(self):
        return []


def test_default_transport_rejects_bad_credentials(monkeypatch):
    monkeypatch.setattr(
        "providers.theoddsapi.requests.get", lambda *a, **k: _FakeResponse(401)
    )
    instance = TheOddsApiProvider(api_key="bad", bookmaker="fanduel")
    with pytest.raises(ProviderUnavailableError):
        instance.get_events("basketball_nba")


def test_default_transport_handles_rate_limit(monkeypatch):
    monkeypatch.setattr(
        "providers.theoddsapi.requests.get", lambda *a, **k: _FakeResponse(429)
    )
    instance = TheOddsApiProvider(api_key="ok", bookmaker="fanduel")
    with pytest.raises(ProviderUnavailableError):
        instance.get_events("basketball_nba")
