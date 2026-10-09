"""Unified promotion workflow service.

Reuses the existing providers, matching, calculators, promotion verification,
liquidity/risk and bankroll components. UI calls these functions rather than
duplicating business logic.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from analytics import expected_profit
from database import (
    add_promotion_source,
    add_promotion_terms,
    create_promotion,
    create_reward_token,
    get_promotion,
    insert_bet,
    list_bets,
    list_promotion_actions,
    list_reward_tokens,
    record_promotion_action,
    save_prediction_snapshot,
    save_reconciliation,
    set_promotion_lifecycle,
    update_bet_status,
    update_reward_token,
    record_settlement,
)
from monitoring import is_stale
from opportunities import BetKind, discover_live_opportunities, discover_mock_opportunities
from promotions import (
    EligibilityStatus,
    Promotion,
    PromotionStatus,
    PromotionTerms,
    RewardToken,
    RewardTokenStatus,
    best_qualifying,
    outstanding_face_value,
    parse_terms,
    promotion_from_terms,
    realized_value,
    terms_hash,
    validate_terms,
)
from providers import DEFAULT_ROUTES, CachedProvider, build_providers
from risk import release_for_bet, reserve_for_bet
from settlement import ActualSettlement, prediction_from_opportunity, reconcile

MODES = ("DEMO", "LIVE")


class LiveDataUnavailable(RuntimeError):
    """Raised when LIVE mode is requested but feeds are not configured."""


@dataclass(frozen=True)
class Recommendation:
    opportunity: object
    worst_case_pnl: Decimal
    capital: Decimal
    liquidity_grade: Optional[str]
    fully_hedged: bool
    slippage_pct: Optional[Decimal]
    timestamp: Optional[datetime]
    source_type: str
    stale: bool
    eligibility: str = ""
    depth_status: str = ""
    available_contracts: Optional[Decimal] = None
    requested_contracts: Optional[Decimal] = None
    executable_contracts: Optional[Decimal] = None
    effective_price: Optional[Decimal] = None
    provenance: dict = field(default_factory=dict)


def _kw(db_path):
    return {} if db_path is None else {"db_path": db_path}


def _live_pair():
    providers = build_providers()
    if not (providers.live and providers.exchange_live):
        raise LiveDataUnavailable(
            "Live feeds are not configured (missing The Odds API key or STX credentials)."
        )
    return (
        CachedProvider(providers.sportsbook, ttl_seconds=30),
        CachedProvider(providers.exchange, ttl_seconds=30),
    )


# --- promotion parsing / persistence -----------------------------------------

def parse_promotion(text: str, overrides: Optional[dict] = None) -> PromotionTerms:
    terms = parse_terms(text or "")
    if overrides:
        terms = _apply_overrides(terms, overrides)
    return terms


def _apply_overrides(terms: PromotionTerms, overrides: dict) -> PromotionTerms:
    from dataclasses import replace

    return replace(
        terms,
        offer_type=overrides.get("offer_type", terms.offer_type),
        qualifying_stake=overrides.get("qualifying_stake", terms.qualifying_stake),
        qualifying_min_odds=overrides.get("qualifying_min_odds", terms.qualifying_min_odds),
        reward_amount=overrides.get("reward_amount", terms.reward_amount),
        reward_type=overrides.get("reward_type", terms.reward_type),
        reward_count=overrides.get("reward_count", terms.reward_count),
        stake_returned=overrides.get("stake_returned", terms.stake_returned),
    )


def save_promotion(
    terms: PromotionTerms,
    sportsbook: str,
    name: str,
    official_url=None,
    terms_url=None,
    terms_text=None,
    jurisdiction=None,
    db_path=None,
) -> int:
    status, _ = validate_terms(terms)
    promotion = promotion_from_terms(terms, sportsbook, name, terms_url, terms_text)
    promotion = promotion.__class__(
        **{
            **promotion.__dict__,
            "status": PromotionStatus.DISCOVERED.value,
            "lifecycle_status": PromotionStatus.DISCOVERED.value,
            "official_source_url": official_url,
            "terms_source_url": terms_url,
            "jurisdiction": jurisdiction or promotion.jurisdiction,
        }
    )
    promotion_id = create_promotion(promotion, **_kw(db_path))
    add_promotion_source(
        promotion_id,
        url=official_url or terms_url,
        text=terms_text,
        source_type="OFFICIAL" if official_url else "MANUAL",
        jurisdiction=jurisdiction,
        version_hash=terms_hash(terms_text or ""),
        **_kw(db_path),
    )
    add_promotion_terms(promotion_id, source="manual", structured=str(terms), **_kw(db_path))
    return promotion_id


# --- opportunity discovery / ranking -----------------------------------------

def find_qualifying_bets(promotion: Promotion, mode: str, stake=None, force=False):
    stake = float(stake or promotion.qualifying_stake or Decimal("10"))
    if mode == "LIVE":
        book, exchange = _live_pair()
        return list(
            discover_live_opportunities(book, exchange, list(DEFAULT_ROUTES), stake, kind=BetKind.QUALIFYING)
        )
    return list(discover_mock_opportunities(stake, kind=BetKind.QUALIFYING))


def find_conversion_bets(promotion: Promotion, token_amount, mode: str, force=False):
    stake = float(token_amount or promotion.reward_token_amount or Decimal("25"))
    if mode == "LIVE":
        book, exchange = _live_pair()
        return list(
            discover_live_opportunities(book, exchange, list(DEFAULT_ROUTES), stake, kind=BetKind.FREE_BET_SNR)
        )
    return list(discover_mock_opportunities(stake, kind=BetKind.FREE_BET_SNR))


def _provenance_summary(opportunity) -> dict:
    prov = getattr(opportunity, "provenance", None)
    execution = getattr(opportunity, "execution", None)
    summary = {"source_type": getattr(opportunity, "source_type", "MOCK")}
    if prov is not None:
        summary.update(
            {
                "source_mode": "LIVE" if getattr(prov, "is_live", False) else "DEMO",
                "sportsbook_provider": getattr(prov, "sportsbook_provider", None),
                "exchange_provider": getattr(prov, "exchange_provider", None),
                "market_id": getattr(prov, "market_id", None),
                "selection_id": getattr(prov, "selection_id", None),
                "quote_timestamp": str(getattr(prov, "sportsbook_quote_timestamp", None)),
                "order_book_timestamp": str(getattr(prov, "exchange_book_timestamp", None)),
                "liquidity_snapshot_id": getattr(prov, "liquidity_snapshot_id", None),
            }
        )
    if execution is not None:
        summary.update(
            {
                "depth_levels_consumed": getattr(execution, "levels_consumed", None),
                "effective_lay_odds": str(getattr(execution, "effective_lay_odds", None)),
                "estimated_fee": str(getattr(execution, "estimated_fee", None)),
                "depth_completeness": getattr(execution, "completeness", "UNKNOWN"),
                "depth_status": getattr(execution, "depth_status", "UNKNOWN"),
                "available_contracts": str(getattr(execution, "total_depth_contracts", None)),
                "requested_contracts": str(getattr(execution, "requested_contracts", None)),
                "executable_contracts": str(getattr(execution, "fillable_contracts", None)),
                "book_age_seconds": str(getattr(execution, "book_age_seconds", None)),
                "fee_model": "per_wager",
                "fee_model_status": "EXTERNALLY_UNVERIFIED",
            }
        )
    return summary


def _opportunity_stale(opportunity, exchange_max_age=30, book_max_age=300) -> bool:
    """Source-aware freshness: exchange books 30s, sportsbook quotes 300s."""
    provenance = getattr(opportunity, "provenance", None)
    now = datetime.now(timezone.utc)
    if provenance is not None:
        exchange_ts = getattr(provenance, "exchange_book_timestamp", None)
        book_ts = getattr(provenance, "sportsbook_quote_timestamp", None)
        if exchange_ts is None and book_ts is None:
            return is_stale(opportunity.timestamp)
        if exchange_ts is not None and (now - exchange_ts).total_seconds() > exchange_max_age:
            return True
        if book_ts is not None and (now - book_ts).total_seconds() > book_max_age:
            return True
        return False
    return is_stale(opportunity.timestamp)


def _recommendation(opportunity, promotion=None) -> Recommendation:
    eligibility = ""
    if promotion is not None:
        from promotions import evaluate_eligibility

        eligibility = evaluate_eligibility(promotion, opportunity).state
    return Recommendation(
        opportunity=opportunity,
        worst_case_pnl=min(opportunity.profit_if_back, opportunity.profit_if_lay),
        capital=opportunity.required_capital,
        liquidity_grade=getattr(opportunity, "liquidity_grade", None),
        fully_hedged=bool(
            opportunity.fully_hedged
            if getattr(opportunity, "fully_hedged", None) is not None
            else opportunity.liquidity_sufficient
        ),
        slippage_pct=getattr(opportunity, "lay_slippage_pct", None),
        timestamp=opportunity.timestamp,
        source_type=getattr(opportunity, "source_type", "MOCK"),
        stale=_opportunity_stale(opportunity),
        eligibility=eligibility,
        depth_status=getattr(opportunity, "depth_status", "") or "",
        available_contracts=getattr(opportunity, "available_contracts", None),
        requested_contracts=getattr(opportunity, "requested_contracts", None),
        executable_contracts=getattr(opportunity, "executable_contracts", None),
        effective_price=getattr(opportunity, "effective_lay_odds", None),
        provenance=_provenance_summary(opportunity),
    )


def rank_qualifying_bets(promotion: Promotion, opportunities, max_results=4, require_hedged=True):
    candidates = [
        c.opportunity
        for c in best_qualifying(promotion, opportunities, limit=100)
    ]
    if require_hedged:
        # Reject insufficient-liquidity opportunities.
        candidates = [o for o in candidates if _recommendation(o, promotion).fully_hedged]
    recommendations = [_recommendation(o, promotion) for o in candidates]
    recommendations.sort(
        key=lambda r: (
            r.opportunity.qualifying_loss,
            r.capital,
            r.slippage_pct if r.slippage_pct is not None else Decimal("0"),
        )
    )
    return recommendations[:max_results]


def rank_conversion_bets(token_amount, opportunities, max_results=4, promotion=None, require_hedged=True):
    def conversion_value(opportunity):
        return opportunity.expected_profit

    candidates = [o for o in opportunities if o.kind is BetKind.FREE_BET_SNR]
    if require_hedged:
        candidates = [o for o in candidates if _recommendation(o, promotion).fully_hedged]
    recommendations = [_recommendation(o, promotion) for o in candidates]
    recommendations.sort(key=lambda r: conversion_value(r.opportunity), reverse=True)
    return recommendations[:max_results]


# --- manual confirmation / persistence ---------------------------------------

def record_qualifying_placed(promotion_id, opportunity, db_path=None) -> int:
    kw = _kw(db_path)
    bet_id = insert_bet(
        event=opportunity.event_label, bookmaker=opportunity.book_provider,
        exchange=opportunity.exchange_provider, market=opportunity.market,
        selection=opportunity.selection, sport=opportunity.sport,
        bet_type="Qualifying bet", back_stake=float(opportunity.stake),
        back_odds=float(opportunity.back_odds), lay_odds=float(opportunity.lay_odds),
        commission=float(opportunity.commission * 100), lay_stake=float(opportunity.lay_stake),
        liability=float(opportunity.lay_liability), profit_if_back=float(opportunity.profit_if_back),
        profit_if_lay=float(opportunity.profit_if_lay),
        source_type=getattr(opportunity, "source_type", None),
        is_live=getattr(opportunity, "is_live", None),
        source="promotion", **kw,
    )
    save_prediction_snapshot(prediction_from_opportunity(opportunity, bet_id), **kw)
    record_promotion_action(promotion_id, "qualifying_placed", bet_id=bet_id, **kw)
    reserve_for_bet(
        bet_id, opportunity.book_provider, opportunity.exchange_provider,
        opportunity.stake, opportunity.lay_liability, db_path=db_path,
    )
    return bet_id


def record_conversion_placed(promotion_id, token_id, opportunity, db_path=None) -> int:
    kw = _kw(db_path)
    from database import get_reward_token
    from promotions import reward_canonical_status

    token_row = get_reward_token(int(token_id), **kw)
    if token_row is not None:
        current = reward_canonical_status(token_row["status"])
        if current in ("REDEEMED", "SETTLED"):
            raise ValueError(f"reward {token_id} has already been redeemed")
    bet_id = insert_bet(
        event=opportunity.event_label, bookmaker=opportunity.book_provider,
        exchange=opportunity.exchange_provider, market=opportunity.market,
        selection=opportunity.selection, sport=opportunity.sport,
        bet_type="Free bet (stake not returned)", back_stake=float(opportunity.stake),
        back_odds=float(opportunity.back_odds), lay_odds=float(opportunity.lay_odds),
        commission=float(opportunity.commission * 100), lay_stake=float(opportunity.lay_stake),
        liability=float(opportunity.lay_liability), profit_if_back=float(opportunity.profit_if_back),
        profit_if_lay=float(opportunity.profit_if_lay),
        source_type=getattr(opportunity, "source_type", None),
        is_live=getattr(opportunity, "is_live", None),
        source="promotion", **kw,
    )
    save_prediction_snapshot(prediction_from_opportunity(opportunity, bet_id), **kw)
    record_promotion_action(promotion_id, "conversion_placed", bet_id=bet_id, **kw)
    update_reward_token(
        int(token_id), linked_conversion_bet_id=bet_id,
        status=RewardTokenStatus.REDEEMED.value,
        used_at=datetime.now(timezone.utc).isoformat(), **kw,
    )
    reserve_for_bet(bet_id, None, opportunity.exchange_provider, 0, opportunity.lay_liability, db_path=db_path)
    return bet_id


_OUTCOME_TO_STATUS = {
    "BACK_WON": "Won at bookmaker",
    "LAY_WON": "Won at exchange",
    "VOID": "Void",
    "CANCELLED": "Void",
    "PUSH": "Void",
}


def confirm_qualifying_settlement(bet_id, outcome, actual=None, db_path=None):
    kw = _kw(db_path)
    from database import get_prediction_snapshot

    row = get_prediction_snapshot(bet_id, **kw)
    if row is None:
        raise ValueError("no prediction snapshot for bet")
    from settlement import prediction_from_row

    actual = actual or ActualSettlement(bet_id=bet_id, outcome=outcome)
    record_settlement(actual, **kw)
    result = reconcile(prediction_from_row(row), actual)
    save_reconciliation(result, prediction_id=row["id"], **kw)
    new_status = _OUTCOME_TO_STATUS.get(outcome)
    if new_status:
        update_bet_status(bet_id, new_status, **kw)
    release_for_bet(bet_id, db_path=db_path)
    _settle_linked_rewards(bet_id, actual, result, db_path)
    return result


def _settle_linked_rewards(bet_id, actual, result, db_path):
    """Move any reward converted by this bet to SETTLED and record realized P&L."""
    from promotions import reward_canonical_status

    kw = _kw(db_path)
    now = datetime.now(timezone.utc).isoformat()
    for token in list_reward_tokens(**kw):
        if token["linked_conversion_bet_id"] != int(bet_id):
            continue
        if reward_canonical_status(token["status"]) != "REDEEMED":
            continue
        realized = None
        if actual is not None and actual.actual_total_pnl is not None:
            realized = actual.actual_total_pnl
        elif getattr(result, "actual_pnl", None) is not None:
            realized = result.actual_pnl
        update_reward_token(
            token["id"], status=RewardTokenStatus.SETTLED.value,
            settled_at=now,
            realized_pnl=realized if realized is not None else token["face_value"],
            **kw,
        )


def confirm_reward_received(promotion_id, token_ids, expiry_hours=None, db_path=None) -> None:
    kw = _kw(db_path)
    now = datetime.now(timezone.utc)
    expires_at = None
    if expiry_hours:
        from datetime import timedelta

        expires_at = (now + timedelta(hours=int(expiry_hours))).isoformat()
    for token_id in token_ids:
        fields = dict(
            status=RewardTokenStatus.CREDITED.value,
            received_at=now.isoformat(),
        )
        if expires_at is not None:
            fields["expires_at"] = expires_at
        update_reward_token(int(token_id), **fields, **kw)


def issue_reward_tokens(promotion, collection=None, db_path=None) -> list:
    """Create the individual reward tokens for a promotion (idempotent).

    Stable ``token_key`` values (``promotion:<id>:<reward_id>``) mean restarting
    the app never duplicates or reissues rewards.
    """
    from promotions import collection_from_promotion

    kw = _kw(db_path)
    collection = collection or collection_from_promotion(promotion)
    ids = []
    for index, unit in enumerate(collection.rewards):
        token = RewardToken(
            promotion_id=promotion.id,
            token_key=f"promotion:{promotion.id}:{unit.reward_id}",
            face_value=unit.amount,
            reward_type=collection.reward_type,
            status=RewardTokenStatus.PENDING.value,
            sequence=index,
            group_key=f"promotion:{promotion.id}",
        )
        ids.append(create_reward_token(token, **kw))
    return ids


# --- realized / projected profit ---------------------------------------------

def calculate_net_profit(promotion_id, db_path=None) -> dict:
    kw = _kw(db_path)
    actions = list_promotion_actions(promotion_id, **kw)
    bets_by_id = {int(b["id"]): b for b in list_bets(**kw)}
    qualifying = [
        bets_by_id[a["bet_id"]] for a in actions
        if a["action"] == "qualifying_placed" and a["bet_id"] in bets_by_id
    ]
    conversions = [
        bets_by_id[a["bet_id"]] for a in actions
        if a["action"] == "conversion_placed" and a["bet_id"] in bets_by_id
    ]
    tokens = list_reward_tokens(promotion_id, **kw)

    def settled(bet):
        return (bet["status"] or "Open") != "Open"

    qualifying_loss = sum((expected_profit(b) for b in qualifying), Decimal("0"))
    realized_conversion = sum((expected_profit(b) for b in conversions if settled(b)), Decimal("0"))
    pending_conversion = sum((expected_profit(b) for b in conversions if not settled(b)), Decimal("0"))
    return {
        "qualifying_loss": qualifying_loss,
        "realized_conversion": realized_conversion,
        "pending_conversion": pending_conversion,
        "realized_net": qualifying_loss + realized_conversion,
        "outstanding_liability": sum(
            (Decimal(str(b["liability"] or 0)) for b in qualifying + conversions if not settled(b)),
            Decimal("0"),
        ),
        "token_outstanding": outstanding_face_value(
            [_token_from_row(t) for t in tokens]
        ),
        "token_realized": realized_value([_token_from_row(t) for t in tokens], bets_by_id),
    }


def _token_from_row(row):
    from datetime import datetime as _dt

    expires = None
    if row["expires_at"]:
        try:
            expires = _dt.fromisoformat(row["expires_at"])
        except (ValueError, TypeError):
            expires = None
    realized = row["realized_value"]
    return RewardToken(
        id=row["id"], promotion_id=row["promotion_id"],
        face_value=Decimal(str(row["face_value"] or 0)),
        reward_type=row["reward_type"] or "FREE_BET_SNR",
        expires_at=expires, status=row["status"] or "PENDING",
        linked_conversion_bet_id=row["linked_conversion_bet_id"],
        realized_value=Decimal(str(realized)) if realized is not None else None,
    )
