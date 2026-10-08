"""Automatic promotion lifecycle resolution.

The lifecycle states remain for auditability, but the app drives them itself:
given the promotion's terms, eligibility and recorded facts (qualifying bet,
settlements, reward tokens), it determines the correct state and advances
through valid transitions. Problems (incomplete terms, ineligibility, expiry)
resolve to exception states that are flagged in the UI.

Deterministic and DB-aware; ``determine_status``/``path_to`` are pure and tested.
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .from_row import promotion_from_row
from .lifecycle import ALLOWED_TRANSITIONS
from .models import EligibilityStatus, Promotion, PromotionStatus

CRITICAL_TERMS = (
    "offer_type",
    "qualifying_stake",
    "reward_amount",
    "reward_type",
    "qualifying_min_odds",
)
SNR_TYPES = ("FREE_BET_SNR", "FREE_BET_SR")


@dataclass(frozen=True)
class PromotionFacts:
    has_qualifying_bet: bool = False
    qualifying_settled: bool = False
    tokens_total: int = 0
    tokens_received: int = 0
    tokens_used: int = 0
    used_conversions_settled: int = 0


def terms_complete(promotion: Promotion) -> bool:
    for name in CRITICAL_TERMS:
        if getattr(promotion, name, None) is None:
            return False
    if (promotion.reward_count or 0) < 1:
        return False
    if promotion.reward_type in SNR_TYPES and promotion.stake_returned is None:
        return False
    return True


def determine_status(promotion: Promotion, facts: PromotionFacts, now=None) -> str:
    now = now or datetime.now(timezone.utc)

    if promotion.expires_at is not None and promotion.expires_at < now:
        return PromotionStatus.EXPIRED.value
    if promotion.eligibility in (
        EligibilityStatus.NOT_ELIGIBLE.value,
        EligibilityStatus.ALREADY_USED.value,
    ):
        return PromotionStatus.INELIGIBLE.value
    if not terms_complete(promotion):
        return PromotionStatus.NEEDS_REVIEW.value
    if promotion.eligibility != EligibilityStatus.ELIGIBLE.value:
        return PromotionStatus.TERMS_VERIFIED.value
    if not facts.has_qualifying_bet:
        return PromotionStatus.ELIGIBILITY_CONFIRMED.value
    if not facts.qualifying_settled:
        return PromotionStatus.QUALIFICATION_PENDING.value
    if facts.tokens_total == 0 or facts.tokens_received == 0:
        return PromotionStatus.REWARD_PENDING.value
    if facts.tokens_used == 0:
        return PromotionStatus.REWARD_RECEIVED.value
    if facts.used_conversions_settled < facts.tokens_used:
        return PromotionStatus.REWARD_USED.value
    return PromotionStatus.COMPLETED.value


def path_to(current: str, target: str) -> list:
    """Ordered valid transitions from ``current`` to ``target`` ([] if none)."""
    if current == target:
        return []
    queue = deque([(current, [current])])
    seen = {current}
    while queue:
        node, path = queue.popleft()
        for nxt in ALLOWED_TRANSITIONS.get(node, ()):
            if nxt in seen:
                continue
            if nxt == target:
                return path[1:] + [nxt]
            seen.add(nxt)
            queue.append((nxt, path + [nxt]))
    return []


def resolve(promotion: Promotion, facts: PromotionFacts, now=None):
    """Return ``(target_status, steps)`` without touching the database."""
    target = determine_status(promotion, facts, now)
    current = promotion.lifecycle_status or promotion.status
    return target, path_to(current, target)


# --- DB-aware helpers ---------------------------------------------------------

def _db_kwargs(db_path):
    return {} if db_path is None else {"db_path": db_path}


def gather_facts(promotion_id, bets_by_id=None, db_path=None) -> PromotionFacts:
    from database import list_bets, list_promotion_actions, list_reward_tokens

    kw = _db_kwargs(db_path)
    if bets_by_id is None:
        bets_by_id = {int(b["id"]): b for b in list_bets(**kw)}
    actions = list_promotion_actions(promotion_id, **kw)
    tokens = list_reward_tokens(promotion_id, **kw)

    qualifying = [
        bets_by_id[a["bet_id"]]
        for a in actions
        if a["action"] == "qualifying_placed" and a["bet_id"] in bets_by_id
    ]
    has_qualifying = len(qualifying) > 0
    qualifying_settled = has_qualifying and all(
        (b["status"] or "Open") != "Open" for b in qualifying
    )

    tokens_received = sum(1 for t in tokens if t["status"] in ("RECEIVED", "USED"))
    used = [t for t in tokens if t["status"] == "USED"]
    settled = 0
    for token in used:
        bet_id = token["linked_conversion_bet_id"]
        row = bets_by_id.get(int(bet_id)) if bet_id is not None else None
        if row is not None and (row["status"] or "Open") != "Open":
            settled += 1

    return PromotionFacts(
        has_qualifying_bet=has_qualifying,
        qualifying_settled=qualifying_settled,
        tokens_total=len(tokens),
        tokens_received=tokens_received,
        tokens_used=len(used),
        used_conversions_settled=settled,
    )


def sync_promotion_lifecycle(promotion_id, db_path=None) -> str:
    """Advance a promotion to the correct lifecycle state. Returns the target."""
    from database import get_promotion, set_promotion_lifecycle

    kw = _db_kwargs(db_path)
    row = get_promotion(promotion_id, **kw)
    if row is None:
        return ""
    promotion = promotion_from_row(row)
    facts = gather_facts(promotion_id, db_path=db_path)
    target, steps = resolve(promotion, facts)
    for step in steps:
        set_promotion_lifecycle(promotion_id, step, note="auto", **kw)
    return target
