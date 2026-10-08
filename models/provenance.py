"""Data-source provenance.

Every opportunity records where it came from. LIVE status is never inferred from
a provider's *name* — adapters declare an explicit ``source_type`` and an
opportunity is only LIVE when **both** legs are LIVE.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class DataSource(str, Enum):
    MOCK = "MOCK"
    LIVE = "LIVE"
    MANUAL = "MANUAL"


@dataclass(frozen=True)
class Provenance:
    source_type: str
    sportsbook_provider: str
    exchange_provider: str
    event_id: str
    market_id: Optional[str]
    selection_id: str
    sportsbook_quote_timestamp: Optional[datetime]
    exchange_book_timestamp: Optional[datetime]
    retrieved_at: datetime
    event_match_confidence: Optional[float]
    market_match_confidence: Optional[float]
    liquidity_snapshot_id: Optional[str]
    is_live: bool

    def as_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "sportsbook_provider": self.sportsbook_provider,
            "exchange_provider": self.exchange_provider,
            "event_id": self.event_id,
            "market_id": self.market_id,
            "selection_id": self.selection_id,
            "sportsbook_quote_timestamp": self.sportsbook_quote_timestamp.isoformat()
            if self.sportsbook_quote_timestamp
            else None,
            "exchange_book_timestamp": self.exchange_book_timestamp.isoformat()
            if self.exchange_book_timestamp
            else None,
            "retrieved_at": self.retrieved_at.isoformat(),
            "event_match_confidence": self.event_match_confidence,
            "market_match_confidence": self.market_match_confidence,
            "liquidity_snapshot_id": self.liquidity_snapshot_id,
            "is_live": self.is_live,
        }
