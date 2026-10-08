"""Match confidence thresholds and classification.

Only matches at or above ``CONFIRMED_MATCH`` may be presented as confirmed
opportunities. Matches between ``REVIEW_MATCH`` and ``CONFIRMED_MATCH`` must be
shown as needing manual review. Anything lower is not a match.
"""

from enum import Enum

CONFIRMED_MATCH = 0.95
REVIEW_MATCH = 0.80
AMBIGUOUS_MARGIN = 0.02


class MatchStatus(str, Enum):
    CONFIRMED = "confirmed"
    REVIEW = "review"


def classify(confidence: float, confirmed: float = CONFIRMED_MATCH, review: float = REVIEW_MATCH):
    """Return a :class:`MatchStatus`, or ``None`` if below the review threshold."""
    if confidence >= confirmed:
        return MatchStatus.CONFIRMED
    if confidence >= review:
        return MatchStatus.REVIEW
    return None


def is_confirmed(status) -> bool:
    return status is MatchStatus.CONFIRMED or status == MatchStatus.CONFIRMED
