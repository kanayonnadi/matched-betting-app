"""Opportunity calculation engine.

Turns a matched (sportsbook back quote, exchange lay quote) pair plus a stake
into the full economics of a matched bet. All money maths delegates to
:mod:`calculators` so there is a single source of truth for the formulas; this
module only adapts normalized quotes into those services and adds the derived
fields ranking and safety checks need.

Action is never automated: this computes numbers only.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from calculators import (
    calculate_free_bet_snr,
    calculate_free_bet_snr_per_wager,
    calculate_free_bet_snr_with_execution,
    calculate_qualifying,
    calculate_qualifying_per_wager,
    calculate_qualifying_with_execution,
)
from models import ExchangeOdds, NormalizedOdds, Side

HUNDRED = Decimal("100")
ONE = Decimal("1")
ZERO = Decimal("0")


class BetKind(str, Enum):
    QUALIFYING = "qualifying"
    FREE_BET_SNR = "free_bet_snr"


@dataclass(frozen=True)
class Opportunity:
    kind: BetKind
    book_provider: str
    exchange_provider: str
    event_id: str
    event_label: str
    sport: str
    league: str
    start_time: datetime
    market: str
    selection: str
    back_odds: Decimal
    lay_odds: Decimal
    commission: Decimal
    stake: Decimal
    lay_stake: Decimal
    lay_liability: Decimal
    profit_if_back: Decimal
    profit_if_lay: Decimal
    expected_profit: Decimal
    qualifying_loss: Decimal
    qualifying_loss_pct: Decimal
    conversion_rate: Optional[Decimal]
    required_capital: Decimal
    rating_percent: Decimal
    available_size: Decimal
    liquidity_sufficient: bool
    timestamp: datetime
    fee_model: str = "commission"
    lay_fee: Optional[Decimal] = None
    fee_factor: Optional[Decimal] = None
    max_price: Optional[Decimal] = None
    event_confidence: Optional[float] = None
    market_confidence: Optional[float] = None
    swapped: bool = False
    execution: Optional[object] = None
    liquidity_grade: Optional[str] = None
    liquidity_reasons: tuple = ()
    effective_lay_odds: Optional[Decimal] = None
    lay_slippage_pct: Optional[Decimal] = None
    levels_consumed: Optional[int] = None
    fully_hedged: Optional[bool] = None
    order_book_timestamp: Optional[datetime] = None
    source_type: str = "MOCK"
    is_live: bool = False
    provenance: Optional[object] = None


def _validate_quotes(book_quote: NormalizedOdds, exchange_quote: ExchangeOdds) -> None:
    if not isinstance(book_quote, NormalizedOdds) or book_quote.side is not Side.BACK:
        raise ValueError("book_quote must be a NormalizedOdds with side=BACK")
    if not isinstance(exchange_quote, ExchangeOdds) or exchange_quote.side is not Side.LAY:
        raise ValueError("exchange_quote must be an ExchangeOdds with side=LAY")


def _rating_percent(back_odds: Decimal, lay_odds: Decimal) -> Decimal:
    """Quick quality indicator: 100% means back and lay odds are equal."""
    return back_odds / lay_odds * HUNDRED


def build_opportunity(
    book_quote: NormalizedOdds,
    exchange_quote: ExchangeOdds,
    stake,
    kind: BetKind = BetKind.QUALIFYING,
    event_label: Optional[str] = None,
    order_book=None,
    now: Optional[datetime] = None,
) -> Opportunity:
    """Build the economics for one matched selection.

    ``stake`` is the back stake for a qualifying bet, or the free-bet face value
    for an SNR free bet.
    """
    _validate_quotes(book_quote, exchange_quote)
    if not isinstance(kind, BetKind):
        kind = BetKind(kind)

    commission = exchange_quote.commission
    fee_model = getattr(exchange_quote, "fee_model", "commission")

    execution = None
    score = None

    if fee_model == "per_wager":
        factor = exchange_quote.fee_factor if exchange_quote.fee_factor is not None else ZERO
        if order_book is not None:
            from liquidity import (
                required_contracts,
                required_contracts_free_bet,
                score_liquidity,
                simulate_execution,
            )

            price = getattr(exchange_quote, "max_price", None) or ONE
            if kind is BetKind.QUALIFYING:
                contracts = required_contracts(stake, book_quote.decimal_odds, price)
                execution = simulate_execution(order_book, contracts, factor, now=now)
                result = calculate_qualifying_with_execution(
                    stake, book_quote.decimal_odds, execution
                )
                qualifying_loss = result.qualifying_loss
                qualifying_loss_pct = result.qualifying_loss_pct
                conversion_rate = None
            else:
                contracts = required_contracts_free_bet(stake, book_quote.decimal_odds, price)
                execution = simulate_execution(order_book, contracts, factor, now=now)
                result = calculate_free_bet_snr_with_execution(
                    stake, book_quote.decimal_odds, execution
                )
                qualifying_loss = ZERO
                qualifying_loss_pct = ZERO
                conversion_rate = result.conversion_rate
            score = score_liquidity(execution)
        elif kind is BetKind.QUALIFYING:
            result = calculate_qualifying_per_wager(
                stake, book_quote.decimal_odds, exchange_quote.decimal_odds, factor
            )
            qualifying_loss = result.qualifying_loss
            qualifying_loss_pct = result.qualifying_loss_pct
            conversion_rate = None
        else:
            result = calculate_free_bet_snr_per_wager(
                stake, book_quote.decimal_odds, exchange_quote.decimal_odds, factor
            )
            qualifying_loss = ZERO
            qualifying_loss_pct = ZERO
            conversion_rate = result.conversion_rate
    elif kind is BetKind.QUALIFYING:
        result = calculate_qualifying(
            stake, book_quote.decimal_odds, exchange_quote.decimal_odds, commission
        )
        qualifying_loss = result.qualifying_loss
        qualifying_loss_pct = result.qualifying_loss_pct
        conversion_rate = None
    else:
        result = calculate_free_bet_snr(
            stake, book_quote.decimal_odds, exchange_quote.decimal_odds, commission
        )
        qualifying_loss = ZERO
        qualifying_loss_pct = ZERO
        conversion_rate = result.conversion_rate

    timestamp = min(book_quote.timestamp, exchange_quote.timestamp)
    label = event_label or f"{book_quote.home_team} vs {book_quote.away_team}"

    if execution is not None:
        liquidity_sufficient = execution.fully_fillable
    else:
        liquidity_sufficient = result.lay_stake <= exchange_quote.available_size

    return Opportunity(
        kind=kind,
        book_provider=book_quote.provider,
        exchange_provider=exchange_quote.provider,
        event_id=book_quote.event_id,
        event_label=label,
        sport=book_quote.sport,
        league=book_quote.league,
        start_time=book_quote.start_time,
        market=book_quote.market,
        selection=book_quote.selection,
        back_odds=book_quote.decimal_odds,
        lay_odds=result.lay_odds if execution is not None else exchange_quote.decimal_odds,
        commission=commission,
        stake=result.back_stake if kind is BetKind.QUALIFYING else result.free_stake,
        lay_stake=result.lay_stake,
        lay_liability=result.liability,
        profit_if_back=result.profit_if_back,
        profit_if_lay=result.profit_if_lay,
        expected_profit=result.expected_profit,
        qualifying_loss=qualifying_loss,
        qualifying_loss_pct=qualifying_loss_pct,
        conversion_rate=conversion_rate,
        required_capital=result.required_capital,
        rating_percent=_rating_percent(book_quote.decimal_odds, exchange_quote.decimal_odds),
        available_size=exchange_quote.available_size,
        liquidity_sufficient=liquidity_sufficient,
        timestamp=timestamp,
        fee_model=fee_model,
        lay_fee=result.lay_fee,
        fee_factor=(
            exchange_quote.fee_factor if fee_model == "per_wager" else None
        ),
        max_price=getattr(exchange_quote, "max_price", None),
        execution=execution,
        liquidity_grade=score.grade.value if score else None,
        liquidity_reasons=tuple(score.reasons) if score else (),
        effective_lay_odds=execution.effective_lay_odds if execution else None,
        lay_slippage_pct=execution.slippage_pct if execution else None,
        levels_consumed=execution.levels_consumed if execution else None,
        fully_hedged=execution.fully_fillable if execution else None,
        order_book_timestamp=execution.book_timestamp if execution else None,
        event_confidence=None,
    )
