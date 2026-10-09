"""Complete, end-to-end promotion profitability (M25).

Combines the **qualifying leg** and the (preview) **free-bet conversion leg**
into a worst-case / best-case promotion result that is explicit about what is
verified, what is merely a current-quote preview, and what is realized.

Design constraints encoded here:

* The arithmetic mean of the two hedge outcomes is **never** labelled an
  "expected value". Outcome-specific P&L is preserved as
  ``worst_case`` (min) and ``best_case`` (max). An ``expected_value`` is only
  produced when the caller supplies an explicit probability model.
* A promotion is only "projected profitable" when the *complete* calculation is
  supported: verified terms, eligible, verified executable depth on both legs,
  and an available conversion preview. A small qualifying loss never makes a
  promotion look profitable on its own.
* Nothing here fetches data or places wagers; callers pass already-discovered
  recommendations. :func:`discover_profitability` is the read-only convenience
  wrapper that runs the existing discovery pipeline.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Callable, Mapping, Optional, Sequence, Tuple

from opportunities import BetKind

ZERO = Decimal("0")
HUNDRED = Decimal("100")

# STX exposes moneyline-style markets; other markets cannot be laid today.
SUPPORTED_STX_MARKETS = ("moneyline", "match_winner")
# Only stake-not-returned free bets have a verified conversion calculation.
SUPPORTED_CONVERSION_REWARDS = ("FREE_BET_SNR",)

EXPECTED_VALUE_NOTE = (
    "No probability model supplied; the mean of the two hedge outcomes is not "
    "an expected value and is not reported as one."
)


class ProfitabilityStatus(str, Enum):
    PROFITABLE = "PROJECTED PROFITABLE"
    NOT_PROFITABLE = "PROJECTED NOT PROFITABLE"
    UNVERIFIED_TERMS = "TERMS UNVERIFIED"
    INELIGIBLE = "INELIGIBLE"
    NO_QUALIFYING = "NO QUALIFYING BET"
    DEPTH_UNVERIFIED = "DEPTH UNVERIFIED"
    CONVERSION_UNSUPPORTED = "CONVERSION UNSUPPORTED"
    CONVERSION_UNAVAILABLE = "CONVERSION UNAVAILABLE"
    STALE = "STALE"
    INSUFFICIENT_CAPITAL = "INSUFFICIENT CAPITAL"


@dataclass(frozen=True)
class StageProfitability:
    found: bool
    opportunity: Optional[object]
    back_stake: Decimal
    back_odds: Optional[Decimal]
    lay_stake: Decimal
    lay_liability: Decimal
    lay_fee: Decimal
    worst_case: Decimal
    best_case: Decimal
    conversion_rate: Optional[Decimal]
    exchange_capital: Decimal
    sportsbook_capital: Decimal
    depth_status: str
    fully_hedged: bool
    stale: bool
    source_type: str


def _empty_stage() -> StageProfitability:
    return StageProfitability(
        found=False, opportunity=None, back_stake=ZERO, back_odds=None,
        lay_stake=ZERO, lay_liability=ZERO, lay_fee=ZERO, worst_case=ZERO,
        best_case=ZERO, conversion_rate=None, exchange_capital=ZERO,
        sportsbook_capital=ZERO, depth_status="", fully_hedged=False,
        stale=False, source_type="",
    )


def _dec(value) -> Decimal:
    if value is None:
        return ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


def stage_from_recommendation(rec, *, sportsbook_capital: bool) -> StageProfitability:
    """Reduce a discovery recommendation to an outcome-aware stage result."""
    if rec is None:
        return _empty_stage()
    opportunity = rec.opportunity
    worst = min(opportunity.profit_if_back, opportunity.profit_if_lay)
    best = max(opportunity.profit_if_back, opportunity.profit_if_lay)
    fee = getattr(opportunity, "lay_fee", None)
    return StageProfitability(
        found=True,
        opportunity=opportunity,
        back_stake=_dec(opportunity.stake),
        back_odds=_dec(opportunity.back_odds),
        lay_stake=_dec(opportunity.lay_stake),
        lay_liability=_dec(opportunity.lay_liability),
        lay_fee=_dec(fee) if fee is not None else ZERO,
        worst_case=_dec(worst),
        best_case=_dec(best),
        conversion_rate=getattr(opportunity, "conversion_rate", None),
        exchange_capital=_dec(opportunity.lay_liability),
        sportsbook_capital=_dec(opportunity.stake) if sportsbook_capital else ZERO,
        depth_status=getattr(rec, "depth_status", "") or "",
        fully_hedged=bool(getattr(rec, "fully_hedged", False)),
        stale=bool(getattr(rec, "stale", False)),
        source_type=getattr(rec, "source_type", "MOCK"),
    )


@dataclass(frozen=True)
class BankrollRequirement:
    sportsbook_account: str
    exchange_account: str
    sportsbook_required: Decimal
    exchange_required: Decimal
    peak_capital: Decimal
    turnover: Decimal
    sportsbook_available: Optional[Decimal] = None
    exchange_available: Optional[Decimal] = None
    sportsbook_shortfall: Decimal = ZERO
    exchange_shortfall: Decimal = ZERO
    sufficient: Optional[bool] = None


@dataclass(frozen=True)
class PromotionProfitability:
    promotion_id: Optional[int]
    sportsbook: str
    name: str
    mode: str
    reward_type: str
    reward_amount: Decimal
    reward_count: int
    stake_returned: Optional[bool]
    terms_verified: bool
    eligible: bool
    eligibility_state: str
    market_equivalence_verified: bool
    conversion_supported: bool
    conversion_note: str
    conversion_preview: bool
    reward_confirmed: bool
    qualifying: StageProfitability
    conversion: StageProfitability
    qualifying_capital: Decimal
    conversion_capital: Decimal
    peak_capital: Decimal
    turnover: Decimal
    worst_case_total: Optional[Decimal]
    best_case_total: Optional[Decimal]
    projected_total: Optional[Decimal]
    expected_value: Optional[Decimal]
    expected_value_note: str
    roi_on_capital: Optional[Decimal]
    projected_profitable: bool
    status: str
    confidence: float
    warnings: Tuple[str, ...]
    provenance: Optional[object]

    def to_dict(self) -> dict:
        return {
            "promotion_id": self.promotion_id,
            "sportsbook": self.sportsbook,
            "name": self.name,
            "mode": self.mode,
            "reward_type": self.reward_type,
            "reward_amount": str(self.reward_amount),
            "reward_count": self.reward_count,
            "stake_returned": self.stake_returned,
            "terms_verified": self.terms_verified,
            "eligible": self.eligible,
            "eligibility_state": self.eligibility_state,
            "conversion_supported": self.conversion_supported,
            "conversion_note": self.conversion_note,
            "conversion_preview": self.conversion_preview,
            "reward_confirmed": self.reward_confirmed,
            "status": self.status,
            "projected_profitable": self.projected_profitable,
            "qualifying": _stage_dict(self.qualifying),
            "conversion": _stage_dict(self.conversion),
            "qualifying_capital": str(self.qualifying_capital),
            "conversion_capital": str(self.conversion_capital),
            "peak_capital": str(self.peak_capital),
            "turnover": str(self.turnover),
            "worst_case_total": _opt(self.worst_case_total),
            "best_case_total": _opt(self.best_case_total),
            "projected_total": _opt(self.projected_total),
            "expected_value": _opt(self.expected_value),
            "roi_on_capital": _opt(self.roi_on_capital),
            "warnings": list(self.warnings),
        }


def _opt(value) -> Optional[str]:
    return None if value is None else str(value)


def _stage_dict(stage: StageProfitability) -> dict:
    return {
        "found": stage.found,
        "back_stake": str(stage.back_stake),
        "back_odds": _opt(stage.back_odds),
        "lay_stake": str(stage.lay_stake),
        "lay_liability": str(stage.lay_liability),
        "lay_fee": str(stage.lay_fee),
        "worst_case": str(stage.worst_case),
        "best_case": str(stage.best_case),
        "conversion_rate": _opt(stage.conversion_rate),
        "exchange_capital": str(stage.exchange_capital),
        "depth_status": stage.depth_status,
        "fully_hedged": stage.fully_hedged,
        "stale": stage.stale,
        "source_type": stage.source_type,
    }


def conversion_support(promotion) -> Tuple[bool, str]:
    """Whether a reward of this type can be converted on supported STX markets."""
    reward_type = promotion.reward_type or ""
    if reward_type not in SUPPORTED_CONVERSION_REWARDS:
        return (
            False,
            f"Reward type {reward_type or 'unknown'} has no verified conversion "
            "calculation; only stake-not-returned free bets are supported.",
        )
    if not promotion.reward_token_amount or promotion.reward_token_amount <= ZERO:
        return False, "Reward amount is unknown; conversion cannot be estimated."
    eligible_markets = set(promotion.eligible_markets or ())
    if eligible_markets and not eligible_markets.intersection(SUPPORTED_STX_MARKETS):
        return (
            False,
            "Reward restricts betting to markets not available on STX "
            f"({', '.join(sorted(eligible_markets))}); cannot convert.",
        )
    return True, "Conversion supported (stake-not-returned free bet on moneyline)."


def bankroll_requirement(
    profitability: PromotionProfitability,
    balances: Optional[Mapping[str, Decimal]] = None,
    sportsbook_account: Optional[str] = None,
    exchange_account: str = "STX",
) -> BankrollRequirement:
    """Compute the capital the promotion needs and check it against balances.

    ``balances`` maps account -> available funds. The sportsbook stake and the
    exchange collateral are tracked separately; funds are never assumed
    transferable between them.
    """
    qualifying = profitability.qualifying
    conversion = profitability.conversion
    sb_required = qualifying.sportsbook_capital if qualifying.found else ZERO
    # Only verified exchange collateral counts; a missing conversion preview does
    # not create a fictitious requirement.
    ex_required = qualifying.exchange_capital if qualifying.found else ZERO
    if conversion.found:
        ex_required += conversion.exchange_capital * Decimal(profitability.reward_count)
    turnover = qualifying.back_stake + qualifying.lay_stake
    if conversion.found:
        turnover += conversion.lay_stake * Decimal(profitability.reward_count)
    peak = sb_required + ex_required

    sb_available = ex_available = None
    short_sb = short_ex = ZERO
    sufficient: Optional[bool] = None
    if balances is not None:
        account = sportsbook_account or profitability.sportsbook
        # Only evaluate against accounts we actually track; an unknown account is
        # reported as "unknown", never as zero, to avoid false insufficiency.
        if account in balances and exchange_account in balances:
            sb_available = _dec(balances.get(account, ZERO))
            ex_available = _dec(balances.get(exchange_account, ZERO))
            short_sb = max(sb_required - sb_available, ZERO)
            short_ex = max(ex_required - ex_available, ZERO)
            sufficient = short_sb == ZERO and short_ex == ZERO

    return BankrollRequirement(
        sportsbook_account=sportsbook_account or profitability.sportsbook,
        exchange_account=exchange_account,
        sportsbook_required=sb_required,
        exchange_required=ex_required,
        peak_capital=peak,
        turnover=turnover,
        sportsbook_available=sb_available,
        exchange_available=ex_available,
        sportsbook_shortfall=short_sb,
        exchange_shortfall=short_ex,
        sufficient=sufficient,
    )


def _value_model(model: Optional[Callable[[object], Decimal]], opportunity) -> Decimal:
    if model is None or opportunity is None:
        return ZERO
    return _dec(model(opportunity))


def evaluate_profitability(
    promotion,
    qualifying_rec,
    conversion_rec=None,
    *,
    mode: str = "LIVE",
    reward_confirmed: bool = False,
    conversion_preview: bool = True,
    balances: Optional[Mapping[str, Decimal]] = None,
    sportsbook_account: Optional[str] = None,
    exchange_account: str = "STX",
    expected_value_model: Optional[Callable[[object], Decimal]] = None,
    now=None,
) -> PromotionProfitability:
    """Evaluate the complete projected profitability of one promotion."""
    from promotions import EligibilityStatus, evaluate_eligibility
    from promotions.lifecycle import is_terms_verified

    count = max(promotion.reward_count or 1, 1)
    terms_verified = is_terms_verified(promotion.status)
    eligibility = evaluate_eligibility(
        promotion, qualifying_rec.opportunity if qualifying_rec else None
    )
    # Both the per-opportunity evaluation AND the promotion's own confirmation
    # must agree; discovery already filters candidates on the same rules.
    eligible = bool(eligibility.eligible) and (
        promotion.eligibility == EligibilityStatus.ELIGIBLE.value
    )

    qualifying = stage_from_recommendation(qualifying_rec, sportsbook_capital=True)
    conversion = stage_from_recommendation(conversion_rec, sportsbook_capital=False)

    provenance = getattr(qualifying.opportunity, "provenance", None) if qualifying.found else None
    market_equivalence_verified = getattr(qualifying.opportunity, "provenance", None) is not None

    supported, support_note = conversion_support(promotion)
    conversion_available = supported and conversion.found and conversion.fully_hedged
    # Only an SNR free bet over the reward face value is a valid conversion leg.
    if conversion.found and conversion.opportunity is not None:
        if getattr(conversion.opportunity, "kind", None) is not BetKind.FREE_BET_SNR:
            conversion_available = False
            supported = False
            support_note = "Conversion candidate is not a stake-not-returned free bet."

    qualifying_capital = qualifying.sportsbook_capital + qualifying.exchange_capital
    conversion_capital = (
        conversion.exchange_capital * Decimal(count) if conversion.found else ZERO
    )
    peak_capital = qualifying_capital + conversion_capital
    turnover = qualifying.back_stake + qualifying.lay_stake
    if conversion.found:
        turnover += conversion.lay_stake * Decimal(count)

    worst_total = best_total = None
    if conversion_available:
        worst_total = qualifying.worst_case + conversion.worst_case * Decimal(count)
        best_total = qualifying.best_case + conversion.best_case * Decimal(count)

    expected_value = None
    if expected_value_model is not None:
        expected_value = _value_model(expected_value_model, qualifying.opportunity)
        if conversion_available:
            expected_value += (
                _value_model(expected_value_model, conversion.opportunity) * Decimal(count)
            )

    roi = (
        worst_total / peak_capital * HUNDRED
        if (worst_total is not None and peak_capital > ZERO)
        else None
    )

    warnings = []
    if not terms_verified:
        warnings.append(f"promotion status {promotion.status}")
    if not eligible:
        warnings.append(f"eligibility {promotion.eligibility}")
    if qualifying.found and not qualifying.fully_hedged:
        warnings.append("qualifying hedge not verified")
    if not supported:
        warnings.append(support_note)
    elif not conversion.found:
        warnings.append("no conversion candidate found at current prices")
    elif not conversion.fully_hedged:
        warnings.append("conversion hedge not verified")
    if conversion_preview:
        warnings.append("conversion is a current-price preview, not a guarantee")

    if not terms_verified:
        status = ProfitabilityStatus.UNVERIFIED_TERMS.value
    elif not eligible:
        status = ProfitabilityStatus.INELIGIBLE.value
    elif not qualifying.found:
        status = ProfitabilityStatus.NO_QUALIFYING.value
    elif not qualifying.fully_hedged:
        status = ProfitabilityStatus.DEPTH_UNVERIFIED.value
    elif not supported:
        status = ProfitabilityStatus.CONVERSION_UNSUPPORTED.value
    elif not conversion.found:
        status = ProfitabilityStatus.CONVERSION_UNAVAILABLE.value
    elif not conversion.fully_hedged:
        status = ProfitabilityStatus.DEPTH_UNVERIFIED.value
    elif qualifying.stale or conversion.stale:
        status = ProfitabilityStatus.STALE.value
    else:
        requirement = bankroll_requirement(
            _shell(promotion, qualifying, conversion, count), balances,
            sportsbook_account=sportsbook_account, exchange_account=exchange_account,
        )
        if balances is not None and requirement.sufficient is False:
            status = ProfitabilityStatus.INSUFFICIENT_CAPITAL.value
            warnings.append("insufficient available bankroll")
        elif worst_total is not None and worst_total > ZERO:
            status = ProfitabilityStatus.PROFITABLE.value
        else:
            status = ProfitabilityStatus.NOT_PROFITABLE.value

    projected_profitable = status == ProfitabilityStatus.PROFITABLE.value

    return PromotionProfitability(
        promotion_id=promotion.id,
        sportsbook=promotion.sportsbook,
        name=promotion.name,
        mode=mode,
        reward_type=promotion.reward_type or "",
        reward_amount=promotion.reward_token_amount,
        reward_count=count,
        stake_returned=promotion.stake_returned,
        terms_verified=terms_verified,
        eligible=eligible,
        eligibility_state=eligibility.state,
        market_equivalence_verified=market_equivalence_verified,
        conversion_supported=supported,
        conversion_note=support_note,
        conversion_preview=conversion_preview and not reward_confirmed,
        reward_confirmed=reward_confirmed,
        qualifying=qualifying,
        conversion=conversion,
        qualifying_capital=qualifying_capital,
        conversion_capital=conversion_capital,
        peak_capital=peak_capital,
        turnover=turnover,
        worst_case_total=worst_total,
        best_case_total=best_total,
        projected_total=worst_total,
        expected_value=expected_value,
        expected_value_note=EXPECTED_VALUE_NOTE if expected_value is None else "caller-supplied probability model",
        roi_on_capital=roi,
        projected_profitable=projected_profitable,
        status=status,
        confidence=promotion.confidence,
        warnings=tuple(warnings),
        provenance=provenance,
    )


def _shell(promotion, qualifying, conversion, count) -> PromotionProfitability:
    """Lightweight object so bankroll_requirement can be reused during status."""
    return PromotionProfitability(
        promotion_id=promotion.id, sportsbook=promotion.sportsbook, name=promotion.name,
        mode="", reward_type=promotion.reward_type or "",
        reward_amount=promotion.reward_token_amount, reward_count=count,
        stake_returned=promotion.stake_returned, terms_verified=True, eligible=True,
        eligibility_state="", market_equivalence_verified=False,
        conversion_supported=True, conversion_note="", conversion_preview=True,
        reward_confirmed=False, qualifying=qualifying, conversion=conversion,
        qualifying_capital=ZERO, conversion_capital=ZERO, peak_capital=ZERO,
        turnover=ZERO, worst_case_total=None, best_case_total=None,
        projected_total=None, expected_value=None, expected_value_note="",
        roi_on_capital=None, projected_profitable=False, status="", confidence=0.0,
        warnings=(), provenance=None,
    )


def rank_profitabilities(items: Sequence[PromotionProfitability]) -> list:
    """Rank complete promotions using the M25 priority order.

    Verified eligibility and a fully-supported (positive) worst-case result come
    first; ties break on lower qualifying loss, lower capital, better conversion
    rate and lower fees/slippage.
    """
    def key(item: PromotionProfitability):
        worst = item.worst_case_total if item.worst_case_total is not None else Decimal("-999999")
        conv_rate = item.conversion.conversion_rate or ZERO
        slippage = ZERO
        for stage in (item.qualifying, item.conversion):
            opp = stage.opportunity
            if opp is not None and getattr(opp, "lay_slippage_pct", None) is not None:
                slippage += _dec(opp.lay_slippage_pct)
        return (
            0 if item.eligible else 1,
            0 if item.market_equivalence_verified else 1,
            0 if item.conversion_supported and item.conversion.found else 1,
            0 if item.projected_profitable else 1,
            -worst,
            -item.qualifying.worst_case,
            item.peak_capital,
            -conv_rate,
            slippage,
        )

    return sorted(items, key=key)


def discover_profitability(
    promotion,
    mode: str = "LIVE",
    *,
    stake=None,
    book=None,
    exchange=None,
    routes=None,
    balances: Optional[Mapping[str, Decimal]] = None,
    reward_confirmed: bool = False,
    expected_value_model: Optional[Callable[[object], Decimal]] = None,
    now=None,
) -> Tuple[PromotionProfitability, object, object]:
    """Run discovery for both legs and return the combined evaluation.

    Returns ``(profitability, qualifying_result, conversion_result)`` so callers
    can still surface the detailed diagnostics behind expandable sections.
    """
    from .discovery import discover_conversion, discover_qualifying

    qualifying_result = discover_qualifying(
        promotion, mode, stake=stake, book=book, exchange=exchange, routes=routes
    )
    qualifying_rec = (
        qualifying_result.recommendations[0] if qualifying_result.recommendations else None
    )
    token_amount = promotion.reward_token_amount
    conversion_result = discover_conversion(
        promotion, token_amount, mode, book=book, exchange=exchange, routes=routes
    )
    conversion_rec = (
        conversion_result.recommendations[0] if conversion_result.recommendations else None
    )
    evaluation = evaluate_profitability(
        promotion,
        qualifying_rec,
        conversion_rec,
        mode=mode,
        reward_confirmed=reward_confirmed,
        balances=balances,
        expected_value_model=expected_value_model,
        now=now,
    )
    return evaluation, qualifying_result, conversion_result
