"""Guided promotion workflow state machine (persistent).

Maps the M23 guided states onto facts already recorded (terms, eligibility,
qualifying/settlement actions, reward tokens). The state is persisted on the
promotion row (``workflow_state``) while the detailed audit trail stays in
``promotion_lifecycle_history``.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from database import get_promotion, update_promotion
from promotions import (
    EligibilityStatus,
    PromotionFacts,
    gather_facts,
    promotion_from_row,
    terms_complete,
)


class WorkflowState(str, Enum):
    DRAFT = "DRAFT"
    QUALIFYING_FOUND = "QUALIFYING_FOUND"
    QUALIFYING_PLACED = "QUALIFYING_PLACED"
    QUALIFYING_SETTLED = "QUALIFYING_SETTLED"
    REWARD_RECEIVED = "REWARD_RECEIVED"
    CONVERSION_FOUND = "CONVERSION_FOUND"
    CONVERSION_PLACED = "CONVERSION_PLACED"
    COMPLETED = "COMPLETED"
    PARTIAL_HEDGE = "PARTIAL_HEDGE"
    VOIDED = "VOIDED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    INELIGIBLE = "INELIGIBLE"


# Forward order for progress display.
PROGRESS_ORDER = (
    WorkflowState.DRAFT,
    WorkflowState.QUALIFYING_FOUND,
    WorkflowState.QUALIFYING_PLACED,
    WorkflowState.QUALIFYING_SETTLED,
    WorkflowState.REWARD_RECEIVED,
    WorkflowState.CONVERSION_FOUND,
    WorkflowState.CONVERSION_PLACED,
    WorkflowState.COMPLETED,
)

FAILURE_STATES = (
    WorkflowState.PARTIAL_HEDGE,
    WorkflowState.VOIDED,
    WorkflowState.CANCELLED,
    WorkflowState.EXPIRED,
    WorkflowState.INELIGIBLE,
)


def derive_state(
    promotion,
    facts: PromotionFacts,
    qualifying_found: bool = False,
    conversion_found: bool = False,
    partial_hedge: bool = False,
    now=None,
) -> WorkflowState:
    now = now or datetime.now(timezone.utc)

    if promotion.expires_at is not None and promotion.expires_at < now:
        return WorkflowState.EXPIRED
    if promotion.eligibility in (
        EligibilityStatus.NOT_ELIGIBLE.value,
        EligibilityStatus.ALREADY_USED.value,
    ):
        return WorkflowState.INELIGIBLE
    if not terms_complete(promotion):
        return WorkflowState.DRAFT
    if promotion.eligibility != EligibilityStatus.ELIGIBLE.value:
        return WorkflowState.DRAFT

    if not facts.has_qualifying_bet:
        return WorkflowState.QUALIFYING_FOUND if qualifying_found else WorkflowState.DRAFT
    if partial_hedge:
        return WorkflowState.PARTIAL_HEDGE
    if not facts.qualifying_settled:
        return WorkflowState.QUALIFYING_PLACED
    if facts.tokens_received == 0:
        return WorkflowState.QUALIFYING_SETTLED
    if facts.tokens_used == 0:
        return WorkflowState.CONVERSION_FOUND if conversion_found else WorkflowState.REWARD_RECEIVED
    if facts.used_conversions_settled < facts.tokens_used:
        return WorkflowState.CONVERSION_PLACED
    return WorkflowState.COMPLETED


def progress_index(state: str) -> int:
    try:
        return PROGRESS_ORDER.index(WorkflowState(state))
    except ValueError:
        return 0


def sync_workflow(
    promotion_id,
    qualifying_found: bool = False,
    conversion_found: bool = False,
    partial_hedge: bool = False,
    db_path=None,
) -> str:
    from promotions.auto_lifecycle import _db_kwargs

    kw = _db_kwargs(db_path)
    row = get_promotion(promotion_id, **kw)
    if row is None:
        return ""
    promotion = promotion_from_row(row)
    facts = gather_facts(promotion_id, db_path=db_path)
    state = derive_state(
        promotion, facts,
        qualifying_found=qualifying_found,
        conversion_found=conversion_found,
        partial_hedge=partial_hedge,
    )
    if (promotion.workflow_state or "") != state.value:
        update_promotion(promotion_id, workflow_state=state.value, **kw)
    return state.value
