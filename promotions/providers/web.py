"""Web promotion provider (terms fetch only).

Fetches promotion page text through an injected fetcher and returns it as raw.
There is **no** automatic verification and no scraping of account areas or
protection circumvention — the caller supplies pages it is permitted to read.
Parsed/validated downstream like any other raw promotion.
"""

from typing import Sequence

from .base import PromotionProvider, RawPromotion


class WebPromotionProvider(PromotionProvider):
    name = "web"

    def __init__(self, sources=(), fetcher=None):
        # sources: sequence of (sportsbook, name, url) or RawPromotion
        self._sources = list(sources)
        self._fetcher = fetcher

    def discover(self) -> Sequence[RawPromotion]:
        if self._fetcher is None:
            return ()
        results = []
        for source in self._sources:
            if isinstance(source, RawPromotion):
                results.append(source)
                continue
            sportsbook, name, url = source
            text = self._fetcher(url)
            if text:
                results.append(RawPromotion(sportsbook=sportsbook, name=name, text=text, source_url=url))
        return tuple(results)
