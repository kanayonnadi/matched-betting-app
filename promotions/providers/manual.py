"""Manual promotion provider.

The safe default: promotions are entered by the user (or imported from a saved
list) rather than scraped. Any discovered text is treated as raw until parsed
and validated.
"""

from typing import Sequence

from .base import PromotionProvider, RawPromotion


class ManualPromotionProvider(PromotionProvider):
    name = "manual"

    def __init__(self, entries=()):
        self._entries = [
            entry if isinstance(entry, RawPromotion) else RawPromotion(**entry)
            for entry in entries
        ]

    def discover(self) -> Sequence[RawPromotion]:
        return tuple(self._entries)
