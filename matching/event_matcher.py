"""Event matching engine.

Matches a sportsbook event to an exchange event using, in order of influence:
team names (home/away, allowing a swapped orientation), start time and league,
with a hard sport gate. Fuzzy string similarity is the fallback signal inside
``name_similarity``. Every result carries a confidence score and a status; low
confidence matches are never treated as confirmed.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Sequence

from models import Event

from .confidence import (
    AMBIGUOUS_MARGIN,
    CONFIRMED_MATCH,
    REVIEW_MATCH,
    MatchStatus,
    classify,
)
from .normalize import name_similarity, normalize_name, normalize_sport


@dataclass(frozen=True)
class MatchComponents:
    team: float
    time: float
    league: float
    home: float
    away: float


@dataclass(frozen=True)
class EventMatch:
    sportsbook_event: Event
    exchange_event: Event
    confidence: float
    status: MatchStatus
    swapped: bool
    ambiguous: bool
    components: MatchComponents


def time_similarity(start_a: datetime, start_b: datetime, tight_minutes: float = 15.0, max_minutes: float = 180.0) -> float:
    """1.0 within ``tight_minutes``, decaying to 0.0 at ``max_minutes``."""
    delta = abs((start_a - start_b).total_seconds()) / 60.0
    if delta <= tight_minutes:
        return 1.0
    if delta >= max_minutes:
        return 0.0
    return 1.0 - (delta - tight_minutes) / (max_minutes - tight_minutes)


class EventMatcher:
    def __init__(
        self,
        confirmed: float = CONFIRMED_MATCH,
        review: float = REVIEW_MATCH,
        ambiguous_margin: float = AMBIGUOUS_MARGIN,
        tight_minutes: float = 15.0,
        max_minutes: float = 180.0,
        team_weight: float = 0.65,
        time_weight: float = 0.25,
        league_weight: float = 0.10,
    ):
        self._confirmed = confirmed
        self._review = review
        self._ambiguous_margin = ambiguous_margin
        self._tight = tight_minutes
        self._max = max_minutes
        self._team_weight = team_weight
        self._time_weight = time_weight
        self._league_weight = league_weight

    def score(self, book: Event, exchange: Event):
        """Return ``(confidence, components, swapped)`` or ``None``.

        ``None`` means the events are not comparable (different sport).
        """
        if normalize_sport(book.sport) != normalize_sport(exchange.sport):
            return None

        home = name_similarity(book.home_team, exchange.home_team)
        away = name_similarity(book.away_team, exchange.away_team)
        home_swapped = name_similarity(book.home_team, exchange.away_team)
        away_swapped = name_similarity(book.away_team, exchange.home_team)

        aligned = (home + away) / 2.0
        swapped_score = (home_swapped + away_swapped) / 2.0
        if swapped_score > aligned:
            team, swapped, home_used, away_used = swapped_score, True, home_swapped, away_swapped
        else:
            team, swapped, home_used, away_used = aligned, False, home, away

        time_score = time_similarity(book.start_time, exchange.start_time, self._tight, self._max)
        league_score = name_similarity(book.league, exchange.league)

        confidence = (
            self._team_weight * team
            + self._time_weight * time_score
            + self._league_weight * league_score
        )

        components = MatchComponents(
            team=team,
            time=time_score,
            league=league_score,
            home=home_used,
            away=away_used,
        )
        return confidence, components, swapped

    def match(self, book: Event, exchange: Event) -> Optional[EventMatch]:
        result = self.score(book, exchange)
        if result is None:
            return None
        confidence, components, swapped = result
        status = classify(confidence, self._confirmed, self._review)
        if status is None:
            return None
        return EventMatch(book, exchange, confidence, status, swapped, False, components)

    def find_matches(
        self,
        book_events: Iterable[Event],
        exchange_events: Sequence[Event],
    ):
        """Best exchange match for each book event, with ambiguity flagging."""
        matches = []
        for book in book_events:
            scored = []
            for exchange in exchange_events:
                result = self.score(book, exchange)
                if result is None:
                    continue
                confidence, components, swapped = result
                if confidence >= self._review:
                    scored.append((confidence, components, swapped, exchange))

            if not scored:
                continue

            scored.sort(key=lambda item: item[0], reverse=True)
            confidence, components, swapped, exchange = scored[0]
            ambiguous = (
                len(scored) > 1
                and (confidence - scored[1][0]) < self._ambiguous_margin
            )
            status = classify(confidence, self._confirmed, self._review)
            matches.append(
                EventMatch(book, exchange, confidence, status, swapped, ambiguous, components)
            )
        return matches
