"""Market and selection matching.

Matching events is not enough: the exact market must correspond, with the same
settlement conditions. This module therefore:

* gates on market type (two-way vs three-way), so different handicaps / totals
  can never be paired;
* normalizes market-name synonyms (``moneyline`` ~ ``match odds`` ~ ``h2h``);
* pairs selection labels with the best permutation and rejects the market if any
  selection is below the selection threshold, so uncertain equivalence is never
  inferred.

Spreads, totals and player props are intentionally unsupported until the basic
engine is reliable.
"""

from dataclasses import dataclass
from itertools import permutations
from typing import Iterable, Optional, Sequence

from models import Market, MarketType

from .confidence import (
    AMBIGUOUS_MARGIN,
    CONFIRMED_MATCH,
    REVIEW_MATCH,
    MatchStatus,
    classify,
)
from .normalize import name_similarity, normalize_name

SUPPORTED_TYPES = (MarketType.MONEYLINE, MarketType.TWO_WAY, MarketType.THREE_WAY)

EXPECTED_SELECTIONS = {
    MarketType.MONEYLINE: 2,
    MarketType.TWO_WAY: 2,
    MarketType.THREE_WAY: 3,
}

MARKET_ALIASES = {
    "moneyline": "moneyline",
    "money line": "moneyline",
    "match odds": "moneyline",
    "match winner": "moneyline",
    "head to head": "moneyline",
    "h2h": "moneyline",
    "winner": "moneyline",
    "to win": "moneyline",
    "1x2": "three_way",
    "three way": "three_way",
    "3 way": "three_way",
    "draw no bet": "two_way",
}


def canonical_market_name(name) -> str:
    normalized = normalize_name(name)
    return MARKET_ALIASES.get(normalized, normalized)


@dataclass(frozen=True)
class SelectionMatch:
    sportsbook_selection: str
    exchange_selection: str
    confidence: float


@dataclass(frozen=True)
class MarketMatch:
    sportsbook_market: Market
    exchange_market: Market
    market_type: MarketType
    confidence: float
    status: MatchStatus
    ambiguous: bool
    selection_matches: tuple


class MarketMatcher:
    def __init__(
        self,
        confirmed: float = CONFIRMED_MATCH,
        review: float = REVIEW_MATCH,
        selection_threshold: float = REVIEW_MATCH,
        ambiguous_margin: float = AMBIGUOUS_MARGIN,
        name_weight: float = 0.5,
        selection_weight: float = 0.5,
    ):
        self._confirmed = confirmed
        self._review = review
        self._selection_threshold = selection_threshold
        self._ambiguous_margin = ambiguous_margin
        self._name_weight = name_weight
        self._selection_weight = selection_weight

    def _market_name_score(self, book: Market, exchange: Market) -> float:
        book_name = canonical_market_name(book.name)
        exchange_name = canonical_market_name(exchange.name)
        if book_name and book_name == exchange_name:
            return 1.0
        return name_similarity(book.name, exchange.name)

    def _best_pairing(self, book_selections, exchange_selections):
        count = len(book_selections)
        best_perm = None
        best_total = -1.0
        second_total = -1.0
        for perm in permutations(range(count)):
            total = sum(
                name_similarity(book_selections[i], exchange_selections[perm[i]])
                for i in range(count)
            )
            if total > best_total:
                second_total = best_total
                best_total = total
                best_perm = perm
            elif total > second_total:
                second_total = total
        average = best_total / count
        ambiguous = (
            count > 1
            and second_total >= 0
            and (best_total - second_total) / count < self._ambiguous_margin
        )
        return best_perm, average, ambiguous

    def score(self, book: Market, exchange: Market):
        """Return match details or ``None`` when the markets are not equivalent."""
        if book.market_type not in SUPPORTED_TYPES or exchange.market_type not in SUPPORTED_TYPES:
            return None
        if book.market_type != exchange.market_type:
            return None

        book_selections = tuple(book.selections)
        exchange_selections = tuple(exchange.selections)
        expected = EXPECTED_SELECTIONS[book.market_type]
        if len(book_selections) != expected or len(exchange_selections) != expected:
            return None

        best_perm, average, ambiguous = self._best_pairing(
            book_selections, exchange_selections
        )
        selection_matches = tuple(
            SelectionMatch(
                sportsbook_selection=book_selections[i],
                exchange_selection=exchange_selections[best_perm[i]],
                confidence=name_similarity(
                    book_selections[i], exchange_selections[best_perm[i]]
                ),
            )
            for i in range(len(book_selections))
        )

        minimum = min(m.confidence for m in selection_matches)
        if minimum < self._selection_threshold:
            return None

        name_score = self._market_name_score(book, exchange)
        confidence = (
            self._name_weight * name_score + self._selection_weight * average
        )
        return confidence, name_score, average, selection_matches, ambiguous

    def match(self, book: Market, exchange: Market) -> Optional[MarketMatch]:
        result = self.score(book, exchange)
        if result is None:
            return None
        confidence, _name_score, _average, selection_matches, ambiguous = result
        status = classify(confidence, self._confirmed, self._review)
        if status is None:
            return None
        if ambiguous:
            status = MatchStatus.REVIEW
        return MarketMatch(
            sportsbook_market=book,
            exchange_market=exchange,
            market_type=book.market_type,
            confidence=confidence,
            status=status,
            ambiguous=ambiguous,
            selection_matches=selection_matches,
        )

    def find_matches(
        self,
        book_markets: Iterable[Market],
        exchange_markets: Sequence[Market],
    ):
        matches = []
        for book in book_markets:
            scored = []
            for exchange in exchange_markets:
                result = self.score(book, exchange)
                if result is None:
                    continue
                confidence = result[0]
                if confidence >= self._review:
                    scored.append((confidence, exchange, result))
            if not scored:
                continue
            scored.sort(key=lambda item: item[0], reverse=True)
            confidence, exchange, result = scored[0]
            _confidence, _name, _avg, selection_matches, ambiguous = result
            ambiguous = ambiguous or (
                len(scored) > 1 and (confidence - scored[1][0]) < self._ambiguous_margin
            )
            status = classify(confidence, self._confirmed, self._review)
            if status is None:
                continue
            if ambiguous:
                status = MatchStatus.REVIEW
            matches.append(
                MarketMatch(
                    sportsbook_market=book,
                    exchange_market=exchange,
                    market_type=book.market_type,
                    confidence=confidence,
                    status=status,
                    ambiguous=ambiguous,
                    selection_matches=selection_matches,
                )
            )
        return matches
