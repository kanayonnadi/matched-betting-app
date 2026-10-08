from datetime import datetime, timezone

import pytest

from matching import (
    MarketMatcher,
    MatchStatus,
    canonical_market_name,
)
from models import Market, MarketType
from providers import MockExchangeProvider, MockSportsbookProvider

START = datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc)


def market(name, selections, market_type=MarketType.MONEYLINE, provider="book", event_id="1"):
    return Market(
        provider=provider,
        event_id=event_id,
        name=name,
        market_type=market_type,
        selections=tuple(selections),
    )


def test_canonical_market_name_synonyms():
    assert canonical_market_name("Match Odds") == "moneyline"
    assert canonical_market_name("H2H") == "moneyline"
    assert canonical_market_name("Head To Head") == "moneyline"
    assert canonical_market_name("1X2") == "three_way"
    assert canonical_market_name("Totals") == "totals"


def test_exact_two_way_market_is_confirmed():
    book = market("moneyline", ["New York Knicks", "Toronto Raptors"])
    exchange = market("moneyline", ["New York Knicks", "Toronto Raptors"])
    result = MarketMatcher().match(book, exchange)
    assert result is not None
    assert result.status is MatchStatus.CONFIRMED
    assert result.market_type is MarketType.MONEYLINE


def test_market_name_synonyms_still_match():
    book = market("match odds", ["Knicks", "Raptors"])
    exchange = market("moneyline", ["Knicks", "Raptors"])
    result = MarketMatcher().match(book, exchange)
    assert result is not None
    assert result.status is MatchStatus.CONFIRMED


def test_different_market_type_rejected():
    book = market("moneyline", ["Knicks", "Raptors"], MarketType.MONEYLINE)
    exchange = market("match winner", ["Knicks", "Draw", "Raptors"], MarketType.THREE_WAY)
    assert MarketMatcher().match(book, exchange) is None


def test_selection_count_mismatch_rejected():
    book = market("moneyline", ["Knicks", "Raptors"], MarketType.MONEYLINE)
    exchange = market("moneyline", ["Knicks", "Draw", "Raptors"], MarketType.MONEYLINE)
    assert MarketMatcher().match(book, exchange) is None


def test_different_selections_rejected():
    book = market("moneyline", ["New York Knicks", "Toronto Raptors"])
    exchange = market("moneyline", ["Boston Celtics", "Miami Heat"])
    assert MarketMatcher().match(book, exchange) is None


def test_three_way_market_matches_with_draw_synonym():
    book = market("match_winner", ["Arsenal", "Draw", "Chelsea"], MarketType.THREE_WAY)
    exchange = market("match winner", ["Arsenal", "The Draw", "Chelsea"], MarketType.THREE_WAY)
    result = MarketMatcher().match(book, exchange)
    assert result is not None
    assert result.status is MatchStatus.CONFIRMED
    mapping = {s.sportsbook_selection: s.exchange_selection for s in result.selection_matches}
    assert mapping["Draw"] == "The Draw"


def test_swapped_selection_order_pairs_correctly():
    book = market("moneyline", ["New York Knicks", "Toronto Raptors"])
    exchange = market("moneyline", ["Raptors", "Knicks"])
    result = MarketMatcher().match(book, exchange)
    assert result is not None
    mapping = {s.sportsbook_selection: s.exchange_selection for s in result.selection_matches}
    assert mapping["New York Knicks"] == "Knicks"
    assert mapping["Toronto Raptors"] == "Raptors"


def test_ambiguous_pairing_forces_review():
    book = market("moneyline", ["Knicks", "New York Knicks"])
    exchange = market("moneyline", ["New York Knicks", "Knicks"])
    result = MarketMatcher(ambiguous_margin=0.1).match(book, exchange)
    assert result is not None
    assert result.ambiguous is True
    assert result.status is MatchStatus.REVIEW


def test_end_to_end_event_then_market_matching():
    from matching import EventMatcher

    book_provider = MockSportsbookProvider()
    exchange_provider = MockExchangeProvider()
    event_matches = EventMatcher().find_matches(
        book_provider.get_events("basketball"),
        exchange_provider.get_events("basketball"),
    )
    target = next(m for m in event_matches if m.sportsbook_event.event_id == "kb-1001")

    book_markets = book_provider.get_markets(target.sportsbook_event.event_id)
    exchange_markets = exchange_provider.get_markets(target.exchange_event.event_id)
    market_match = MarketMatcher().match(book_markets[0], exchange_markets[0])

    assert market_match is not None
    assert market_match.status is MatchStatus.CONFIRMED
    mapping = {
        s.sportsbook_selection: s.exchange_selection
        for s in market_match.selection_matches
    }
    assert mapping["Toronto Raptors"] == "Raptors"
