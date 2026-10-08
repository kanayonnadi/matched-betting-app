"""Promotion discovery providers."""

from .base import PromotionProvider, RawPromotion
from .manual import ManualPromotionProvider
from .web import WebPromotionProvider

__all__ = [
    "ManualPromotionProvider",
    "PromotionProvider",
    "RawPromotion",
    "WebPromotionProvider",
]
