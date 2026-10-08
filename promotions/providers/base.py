"""Promotion discovery provider interface.

Discovery returns *raw* promotions (sportsbook, name, terms text, source). A
discovered promotion is never automatically verified — it must be parsed,
validated and (if needed) reviewed.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class RawPromotion:
    sportsbook: str
    name: str
    text: str
    source_url: Optional[str] = None
    jurisdiction: Optional[str] = None


class PromotionProvider(ABC):
    name = "base"

    @abstractmethod
    def discover(self) -> Sequence[RawPromotion]:
        """Return raw promotions; never verified."""
