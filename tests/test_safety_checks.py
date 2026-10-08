from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bankroll import summarize
from matching import EventMatcher
from models import Event
from monitoring import ODDS_STALE_SECONDS, is_stale
from opportunities import build_opportunity
from providers import MockExchangeProvider, MockSportsbookProvider

START = datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc)


def _quotes(liquidity=None):
    kwargs = {} if liquidity is None else {"liquidity": Decimal(str(liquidity))}
    book = MockSportsbookProvider()
    exchange = MockExchangeProvider(**kwargs)
    book_event = book.get_events("basketball")[0]
    exchange_event = exchange.get_events("basketball")[0]
    book_quotes = {q.selection: q for q in book.get_odds(book_event.event_id, "moneyline")}
    exchange_quotes = {q.selection: q for q in exchange.get_odds(exchange_event.event_id, "moneyline")}
    return book_quotes, exchange_quotes


def test_stale_odds_flagged():
    now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    assert is_stale(now - timedelta(seconds=ODDS_STALE_SECONDS + 5), now=now)
    assert not is_stale(now - timedelta(seconds=5), now=now)


def test_insufficient_liquidity_flagged():
    book_quotes, exchange_quotes = _quotes(liquidity="50")
    opportunity = build_opportunity(book_quotes["Toronto Raptors"], exchange_quotes["Raptors"], 100)
    assert opportunity.liquidity_sufficient is False


def test_sufficient_liquidity():
    book_quotes, exchange_quotes = _quotes()
    opportunity = build_opportunity(book_quotes["Toronto Raptors"], exchange_quotes["Raptors"], 100)
    assert opportunity.liquidity_sufficient is True


def test_insufficient_bankroll():
    summary = summarize(
        [{"account": "Book", "amount": 50.0}],
        [{"status": "Open", "back_stake": 100.0, "liability": 200.0}],
    )
    assert summary.capital_committed == Decimal("300")
    assert summary.free_capital < 0


def test_odds_change_between_calculations():
    book_quotes, exchange_quotes = _quotes()
    first = build_opportunity(book_quotes["Toronto Raptors"], exchange_quotes["Raptors"], 100)
    moved = build_opportunity(
        book_quotes["Toronto Raptors"],
        replace(exchange_quotes["Raptors"], decimal_odds=Decimal("2.50")),
        100,
    )
    assert first.qualifying_loss != moved.qualifying_loss


def test_cancelled_event_has_no_matches():
    book = MockSportsbookProvider()
    assert EventMatcher().find_matches(book.get_events("basketball"), []) == []


def _event(provider, event_id):
    return Event(
        provider=provider,
        event_id=event_id,
        sport="basketball",
        league="NBA",
        home_team="Knicks",
        away_team="Raptors",
        start_time=START,
    )


def test_duplicate_events_flagged_ambiguous():
    matches = EventMatcher().find_matches(
        [_event("book", "1")],
        [_event("exchange", "2"), _event("exchange", "3")],
    )
    assert len(matches) == 1
    assert matches[0].ambiguous is True
