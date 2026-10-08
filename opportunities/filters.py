"""Filtering for opportunities.

Kept independent of Streamlit so it can be unit tested and reused by the
monitoring layer.
"""

from dataclasses import dataclass
from typing import Optional

from matching import CONFIRMED_MATCH

from .engine import BetKind, Opportunity


@dataclass
class OpportunityFilter:
    sport: Optional[str] = None
    league: Optional[str] = None
    bookmaker: Optional[str] = None
    exchange: Optional[str] = None
    kind: Optional[BetKind] = None
    min_back_odds: Optional[float] = None
    max_back_odds: Optional[float] = None
    min_lay_odds: Optional[float] = None
    max_lay_odds: Optional[float] = None
    max_qualifying_loss: Optional[float] = None
    max_qualifying_loss_pct: Optional[float] = None
    max_liability: Optional[float] = None
    min_liquidity: Optional[float] = None
    min_match_confidence: Optional[float] = None
    only_liquid: bool = False
    confirmed_only: bool = False

    def matches(self, opportunity: Opportunity) -> bool:
        if self.sport is not None and opportunity.sport != self.sport:
            return False
        if self.league is not None and opportunity.league != self.league:
            return False
        if self.bookmaker is not None and opportunity.book_provider != self.bookmaker:
            return False
        if self.exchange is not None and opportunity.exchange_provider != self.exchange:
            return False
        if self.kind is not None:
            kind = self.kind
            if not isinstance(kind, BetKind):
                kind = BetKind(kind)
            if opportunity.kind is not kind:
                return False
        if self.min_back_odds is not None and opportunity.back_odds < self.min_back_odds:
            return False
        if self.max_back_odds is not None and opportunity.back_odds > self.max_back_odds:
            return False
        if self.min_lay_odds is not None and opportunity.lay_odds < self.min_lay_odds:
            return False
        if self.max_lay_odds is not None and opportunity.lay_odds > self.max_lay_odds:
            return False
        if (
            self.max_qualifying_loss is not None
            and opportunity.qualifying_loss > self.max_qualifying_loss
        ):
            return False
        if (
            self.max_qualifying_loss_pct is not None
            and opportunity.qualifying_loss_pct > self.max_qualifying_loss_pct
        ):
            return False
        if self.max_liability is not None and opportunity.lay_liability > self.max_liability:
            return False
        if self.min_liquidity is not None and opportunity.available_size < self.min_liquidity:
            return False
        if (
            self.min_match_confidence is not None
            and (opportunity.event_confidence or 0.0) < self.min_match_confidence
        ):
            return False
        if self.only_liquid and not opportunity.liquidity_sufficient:
            return False
        if self.confirmed_only and (opportunity.event_confidence or 0.0) < CONFIRMED_MATCH:
            return False
        return True

    def apply(self, opportunities) -> list:
        return [opportunity for opportunity in opportunities if self.matches(opportunity)]
