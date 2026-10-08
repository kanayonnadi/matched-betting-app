from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from matching import (
    CONFIRMED_MATCH,
    REVIEW_MATCH,
    EventMatcher,
    MatchStatus,
    classify,
    name_similarity,
    normalize_name,
    time_similarity,
)
from models import Event
from providers import MockExchangeProvider, MockSportsbookProvider

START = datetime(2026, 11, 1, 19, 30, tzinfo=timezone.utc)


def event(provider, event_id, home, away, sport="basketball", league="NBA", start=START):
    return Event(
        provider=provider,
        event_id=event_id,
        sport=sport,
        league=league,
        home_team=home,
        away_team=away,
        start_time=start,
    )


# --- normalize ---------------------------------------------------------------

def test_normalize_basic():
    assert normalize_name("New York Knicks") == "new york knicks"


def test_normalize_strips_accents_and_punctuation():
    assert normalize_name("Atlético Madrid") == "atletico madrid"
    assert normalize_name("Paris Saint-Germain") == "paris saint germain"


def test_normalize_drops_noise_tokens():
    assert normalize_name("Arsenal FC") == "arsenal"
    assert normalize_name("The Draw") == "draw"


def test_normalize_none_is_empty():
    assert normalize_name(None) == ""


def test_similarity_exact():
    assert name_similarity("Toronto Raptors", "toronto raptors") == 1.0


def test_similarity_nickname_subset_is_high():
    assert name_similarity("New York Knicks", "Knicks") >= 0.90


def test_similarity_different_teams_is_low():
    assert name_similarity("New York Knicks", "New York Rangers") < 0.80


def test_similarity_empty_is_zero():
    assert name_similarity("", "Knicks") == 0.0


# --- confidence --------------------------------------------------------------

@pytest.mark.parametrize(
    "confidence,expected",
    [
        (0.99, MatchStatus.CONFIRMED),
        (0.95, MatchStatus.CONFIRMED),
        (0.94, MatchStatus.REVIEW),
        (0.80, MatchStatus.REVIEW),
        (0.79, None),
        (0.0, None),
    ],
)
def test_classify_thresholds(confidence, expected):
    assert classify(confidence) is expected


def test_threshold_constants():
    assert CONFIRMED_MATCH == 0.95
    assert REVIEW_MATCH == 0.80


def test_time_similarity_curve():
    assert time_similarity(START, START) == 1.0
    assert time_similarity(START, START + timedelta(minutes=10)) == 1.0
    assert time_similarity(START, START + timedelta(minutes=180)) == 0.0
    partial = time_similarity(START, START + timedelta(minutes=90))
    assert 0.0 < partial < 1.0


# --- matcher -----------------------------------------------------------------

def test_exact_event_is_confirmed():
    book = event("book", "1", "New York Knicks", "Toronto Raptors")
    exchange = event("exchange", "2", "New York Knicks", "Toronto Raptors")
    result = EventMatcher().match(book, exchange)
    assert result is not None
    assert result.status is MatchStatus.CONFIRMED
    assert result.confidence == pytest.approx(1.0)
    assert result.swapped is False


def test_nickname_only_exchange_is_confirmed():
    book = event("book", "1", "New York Knicks", "Toronto Raptors")
    exchange = event("exchange", "2", "Knicks", "Raptors")
    result = EventMatcher().match(book, exchange)
    assert result is not None
    assert result.status is MatchStatus.CONFIRMED
    assert result.confidence >= 0.95


def test_different_sport_returns_none():
    book = event("book", "1", "Knicks", "Raptors", sport="basketball")
    exchange = event("exchange", "2", "Knicks", "Raptors", sport="soccer")
    assert EventMatcher().match(book, exchange) is None


def test_far_apart_events_not_matched():
    book = event("book", "1", "Knicks", "Raptors", start=START)
    exchange = event("exchange", "2", "Knicks", "Raptors", start=START + timedelta(hours=12))
    assert EventMatcher().match(book, exchange) is None


def test_swapped_home_away_detected():
    book = event("book", "1", "New York Knicks", "Toronto Raptors")
    exchange = event("exchange", "2", "Raptors", "Knicks")
    result = EventMatcher().match(book, exchange)
    assert result is not None
    assert result.swapped is True
    assert result.status is MatchStatus.CONFIRMED


def test_mismatched_teams_is_not_a_match():
    book = event("book", "1", "New York Knicks", "Toronto Raptors")
    exchange = event("exchange", "2", "Boston Celtics", "Miami Heat")
    assert EventMatcher().match(book, exchange) is None


def test_league_mismatch_forces_review():
    book = event("book", "1", "Knicks", "Raptors", league="NBA")
    exchange = event("exchange", "2", "Knicks", "Raptors", league="EuroLeague")
    result = EventMatcher().match(book, exchange)
    assert result is not None
    assert result.status is MatchStatus.REVIEW


def test_find_matches_pairs_all_mock_events():
    book = MockSportsbookProvider()
    exchange = MockExchangeProvider()
    matches = EventMatcher().find_matches(
        book.get_events("basketball"), exchange.get_events("basketball")
    )
    assert len(matches) == 3
    assert all(m.status is MatchStatus.CONFIRMED for m in matches)
    assert all(not m.ambiguous for m in matches)
    pairs = {(m.sportsbook_event.event_id, m.exchange_event.event_id) for m in matches}
    assert pairs == {
        ("kb-1001", "kx-1001"),
        ("kb-1002", "kx-1002"),
        ("kb-1003", "kx-1003"),
    }


def test_find_matches_flags_ambiguity_on_duplicates():
    book = event("book", "1", "Knicks", "Raptors")
    exchange = [
        event("exchange", "2", "Knicks", "Raptors"),
        event("exchange", "3", "Knicks", "Raptors"),
    ]
    matches = EventMatcher().find_matches([book], exchange)
    assert len(matches) == 1
    assert matches[0].ambiguous is True
