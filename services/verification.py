"""Independent STX hedge verification engine (M27).

Recalculates every proposed hedge from **raw** sportsbook and STX inputs — the
exchange order book, the sportsbook quote and the market's ``max_price`` — rather
than trusting the optimizer's own outputs. It then compares the two and flags any
disagreement beyond rounding tolerance.

Model (from the STX docs / Ontario fee schedule):

* A market is a binary contract settling at ``max_price`` (``M``) or ``$0``.
* Laying = **selling**; sell-order liability is ``(M − p) × contracts``.
* Required contracts for a matched bet are fixed by stake and odds:
  qualifying ``Q = stake × back_odds / M``; SNR ``Q = free_outlay × (back_odds−1) / M``.
* Order-level taker fee: ``ceil(f × Σ Cᵢ × pᵢ × (M − pᵢ) / M)`` (documented, and
  scale-invariant in the environment's price units).

Nothing here places orders. Statuses describe *executability at the observed
quote*, never acceptance.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from enum import Enum
from typing import Optional, Sequence, Tuple

from matching import canonical_market_name, normalize_name

ONE = Decimal("1")
ZERO = Decimal("0")
HUNDRED = Decimal("100")
CENT = Decimal("0.01")


class VerificationStatus(str, Enum):
    EXECUTION_VERIFIED = "EXECUTION VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY VERIFIED"
    BLOCKED = "BLOCKED"


def _dec(value) -> Decimal:
    if value is None:
        return ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _ceil_cent(value: Decimal) -> Decimal:
    if value <= ZERO:
        return ZERO
    return value.quantize(CENT, rounding=ROUND_CEILING)


# --- fee model ---------------------------------------------------------------

TIERS = {
    "rookie": (Decimal("0.00"), Decimal("0.10")),
    "veteran": (Decimal("0.00"), Decimal("0.08")),
    "all-star": (Decimal("0.00"), Decimal("0.06")),
    "mvp": (Decimal("0.00"), Decimal("0.04")),
    "hall of fame": (Decimal("0.00"), Decimal("0.00")),
}


@dataclass(frozen=True)
class FeeSchedule:
    """Applicable maker/taker fee factors.

    ``status`` is ``VERIFIED`` only when the account's own rate/tier was read
    from the authenticated API; otherwise ``ESTIMATED`` (a documented default) or
    ``UNVERIFIED``.
    """

    taker_factor: Decimal = Decimal("0.10")
    maker_factor: Decimal = Decimal("0.00")
    tier: str = "Rookie"
    side: str = "taker"
    status: str = "ESTIMATED"
    source: str = "STX Ontario fee schedule (Rookie taker 0.10)"

    @property
    def verified(self) -> bool:
        return self.status == "VERIFIED"

    def factor(self) -> Decimal:
        return self.maker_factor if self.side == "maker" else self.taker_factor

    def order_fee(self, fills: Sequence[Tuple[Decimal, Decimal]], max_price) -> Decimal:
        """Documented order-level fee, rounded up to the cent once.

        ``fills`` is a sequence of ``(price, contracts)`` in the environment's
        dollar units; ``max_price`` is ``M``.
        """
        M = _dec(max_price)
        if M <= ZERO:
            return ZERO
        gross = sum((c * p * (M - p) for p, c in fills), ZERO)
        return _ceil_cent(self.factor() * gross / M)


# --- independent execution walk ----------------------------------------------

def walk_levels(levels, required, max_price) -> dict:
    """Independently walk lay-side levels best-price-first.

    ``levels`` is a sequence of objects with ``price`` and ``contracts``.
    """
    M = _dec(max_price)
    remaining = _dec(required)
    fills = []
    for level in levels:
        if remaining <= ZERO:
            break
        take = min(remaining, _dec(level.contracts))
        if take <= ZERO:
            continue
        fills.append((_dec(level.price), take))
        remaining -= take
    filled = _dec(required) - remaining
    lay_stake = sum((p * c for p, c in fills), ZERO)
    liability = sum((c * (M - p) for p, c in fills), ZERO)
    avg_price = lay_stake / filled if filled > ZERO else None
    return {
        "fills": tuple(fills),
        "filled": filled,
        "shortfall": remaining,
        "lay_stake": lay_stake,
        "liability": liability,
        "avg_price": avg_price,
        "effective_lay_odds": (M / avg_price) if avg_price and avg_price > ZERO else None,
    }


def required_contracts(kind, stake, back_odds, max_price) -> Decimal:
    stake = _dec(stake)
    b = _dec(back_odds)
    M = _dec(max_price)
    if kind == "free_bet_snr":
        return stake * (b - ONE) / M
    return stake * b / M


def outcome_pnl(kind, stake, back_odds, walk, fee) -> dict:
    """Independent per-outcome P&L from actual fills."""
    stake = _dec(stake)
    b = _dec(back_odds)
    lay_stake = walk["lay_stake"]
    liability = walk["liability"]
    if kind == "free_bet_snr":
        profit_if_back = stake * (b - ONE) - liability - fee
        profit_if_lay = lay_stake - fee
    else:
        profit_if_back = stake * (b - ONE) - liability - fee
        profit_if_lay = lay_stake - stake - fee
    return {
        "profit_if_back": profit_if_back,
        "profit_if_lay": profit_if_lay,
        "worst_case": min(profit_if_back, profit_if_lay),
        "best_case": max(profit_if_back, profit_if_lay),
    }


# --- market equivalence ------------------------------------------------------

@dataclass(frozen=True)
class EquivalenceResult:
    equivalent: bool
    settlement_rules_known: bool
    checks: Tuple[Tuple[str, bool, str], ...]
    reason: str


def _same_participants(a, b) -> bool:
    set_a = {normalize_name(getattr(a, "home_team", "")), normalize_name(getattr(a, "away_team", ""))}
    set_b = {normalize_name(getattr(b, "home_team", "")), normalize_name(getattr(b, "away_team", ""))}
    return set_a == set_b and all(set_a)


def verify_market_equivalence(book_quote, exchange_quote, tolerance_minutes=30) -> EquivalenceResult:
    checks = []
    if book_quote is None or exchange_quote is None:
        return EquivalenceResult(
            False, False, (("raw_quotes", False, "missing sportsbook or exchange quote"),),
            "market equivalence cannot be confirmed without both quotes",
        )
    checks.append(("participants", _same_participants(book_quote, exchange_quote),
                   f"{getattr(book_quote,'home_team','')} vs {getattr(book_quote,'away_team','')}"))
    checks.append(("selection", normalize_name(book_quote.selection) == normalize_name(exchange_quote.selection),
                   f"{book_quote.selection} / {exchange_quote.selection}"))
    checks.append(("market_type", canonical_market_name(book_quote.market) == canonical_market_name(exchange_quote.market),
                   f"{canonical_market_name(book_quote.market)} vs {canonical_market_name(exchange_quote.market)}"))
    dt = abs((book_quote.start_time - exchange_quote.start_time).total_seconds())
    checks.append(("start_time", dt <= tolerance_minutes * 60, f"Δ{dt/60:.1f} min"))
    # Settlement rules (void/tie/postponement/overtime) are not machine-readable
    # from the price feed; only claim them if a caller confirms them.
    equivalent = all(passed for _, passed, _ in checks)
    reason = "events/market/selection/time match" if equivalent else "sportsbook and STX market do not match"
    return EquivalenceResult(equivalent, False, tuple(checks), reason)


# --- verification result -----------------------------------------------------

@dataclass(frozen=True)
class Tolerances:
    money: Decimal = Decimal("0.01")
    contracts: Decimal = Decimal("0")
    odds: Decimal = Decimal("0.005")


@dataclass(frozen=True)
class VerificationResult:
    status: str
    executable: bool
    reasons: Tuple[str, ...]
    depth_status: str
    quote_fresh: bool
    fee_status: str
    settlement_rules_known: bool
    max_price: Decimal
    independent: dict
    optimizer: dict
    discrepancies: Tuple[str, ...]
    equivalence: EquivalenceResult
    checked_at: datetime

    @property
    def verified(self) -> bool:
        return self.status == VerificationStatus.EXECUTION_VERIFIED.value

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "executable": self.executable,
            "reasons": list(self.reasons),
            "depth_status": self.depth_status,
            "quote_fresh": self.quote_fresh,
            "fee_status": self.fee_status,
            "settlement_rules_known": self.settlement_rules_known,
            "discrepancies": list(self.discrepancies),
            "independent": {k: (str(v) if isinstance(v, (Decimal,)) else v) for k, v in self.independent.items() if k != "fills"},
            "optimizer": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in self.optimizer.items()},
        }


def _diff(name, independent, optimizer, tol, discrepancies):
    if optimizer is None:
        discrepancies.append(f"{name}: optimizer value missing")
        return
    if abs(_dec(independent) - _dec(optimizer)) > _dec(tol):
        discrepancies.append(f"{name}: independent {_dec(independent)} vs optimizer {_dec(optimizer)}")


def verify_opportunity(
    opportunity,
    *,
    fee_schedule: Optional[FeeSchedule] = None,
    tolerances: Optional[Tolerances] = None,
    equivalence: Optional[EquivalenceResult] = None,
    settlement_rules_confirmed: bool = False,
    now: Optional[datetime] = None,
    max_book_age_seconds: int = 30,
    max_quote_age_seconds: int = 300,
) -> VerificationResult:
    """Independently verify one opportunity from raw inputs."""
    schedule = fee_schedule or FeeSchedule()
    tol = tolerances or Tolerances()
    now = now or datetime.now(timezone.utc)
    kind = getattr(getattr(opportunity, "kind", None), "value", None) or str(getattr(opportunity, "kind", "qualifying"))

    raw_book = getattr(opportunity, "order_book", None)
    book_quote = getattr(opportunity, "book_quote", None)
    ex_quote = getattr(opportunity, "exchange_quote", None)
    equivalence = equivalence or verify_market_equivalence(book_quote, ex_quote)
    settlement_rules_known = bool(settlement_rules_confirmed or equivalence.settlement_rules_known)

    if raw_book is None or book_quote is None:
        return _blocked(
            "raw STX order book or sportsbook quote unavailable for independent verification",
            equivalence, schedule, now,
        )

    M = _dec(raw_book.max_price)
    stake = _dec(opportunity.stake)
    back_odds = _dec(book_quote.decimal_odds)
    required = required_contracts(kind, stake, back_odds, M)
    walk = walk_levels(raw_book.levels, required, M)
    fee = schedule.order_fee(walk["fills"], M)
    pnl = outcome_pnl(kind, stake, back_odds, walk, fee)

    independent = {
        "contracts": required,
        "filled": walk["filled"],
        "shortfall": walk["shortfall"],
        "average_price": walk["avg_price"],
        "effective_lay_odds": walk["effective_lay_odds"],
        "lay_stake": walk["lay_stake"],
        "liability": walk["liability"],
        "fee": fee,
        "required_capital": (stake + walk["liability"]) if kind != "free_bet_snr" else walk["liability"],
        "profit_if_back": pnl["profit_if_back"],
        "profit_if_lay": pnl["profit_if_lay"],
        "worst_case": pnl["worst_case"],
        "best_case": pnl["best_case"],
    }
    optimizer = {
        "contracts": getattr(opportunity, "requested_contracts", None),
        "filled": getattr(opportunity, "executable_contracts", None),
        "lay_stake": getattr(opportunity, "lay_stake", None),
        "liability": getattr(opportunity, "lay_liability", None),
        "fee": getattr(opportunity, "lay_fee", None),
        "profit_if_back": getattr(opportunity, "profit_if_back", None),
        "profit_if_lay": getattr(opportunity, "profit_if_lay", None),
    }

    discrepancies = []
    _diff("contracts", independent["contracts"], optimizer["contracts"], tol.contracts, discrepancies)
    _diff("filled", independent["filled"], optimizer["filled"], tol.contracts, discrepancies)
    _diff("lay_stake", independent["lay_stake"], optimizer["lay_stake"], tol.money, discrepancies)
    _diff("liability", independent["liability"], optimizer["liability"], tol.money, discrepancies)
    _diff("fee", independent["fee"], optimizer["fee"], tol.money, discrepancies)
    _diff("profit_if_back", independent["profit_if_back"], optimizer["profit_if_back"], tol.money, discrepancies)
    _diff("profit_if_lay", independent["profit_if_lay"], optimizer["profit_if_lay"], tol.money, discrepancies)

    depth_status = getattr(opportunity, "depth_status", None) or "UNKNOWN"

    # Freshness (source-aware).
    book_ts = getattr(raw_book, "timestamp", None)
    quote_fresh = True
    if book_ts is not None:
        quote_fresh = (now - book_ts).total_seconds() <= max_book_age_seconds
    if book_quote is not None and getattr(book_quote, "timestamp", None) is not None:
        quote_fresh = quote_fresh and (now - book_quote.timestamp).total_seconds() <= max_quote_age_seconds

    reasons = []
    status = VerificationStatus.EXECUTION_VERIFIED.value

    if not equivalence.equivalent:
        return _blocked(f"market equivalence not confirmed: {equivalence.reason}", equivalence, schedule, now,
                        independent=independent, optimizer=optimizer, discrepancies=tuple(discrepancies))
    if depth_status in ("INVALID", "STALE"):
        return _blocked(f"STX depth {depth_status}", equivalence, schedule, now,
                        independent=independent, optimizer=optimizer, discrepancies=tuple(discrepancies))
    if walk["shortfall"] > ZERO:
        return _blocked(
            f"insufficient executable depth: {walk['shortfall']} of {required} contracts unfilled",
            equivalence, schedule, now,
            independent=independent, optimizer=optimizer, discrepancies=tuple(discrepancies),
            depth_status=depth_status,
        )
    if discrepancies:
        return _blocked(
            "optimizer output disagrees with independent recalculation: " + "; ".join(discrepancies),
            equivalence, schedule, now, independent=independent, optimizer=optimizer,
            discrepancies=tuple(discrepancies), depth_status=depth_status,
        )
    if not quote_fresh:
        return _blocked(
            "sportsbook quote or STX order book is stale",
            equivalence, schedule, now, independent=independent, optimizer=optimizer,
            discrepancies=tuple(discrepancies), depth_status=depth_status,
        )
    if not schedule.verified:
        reasons.append(f"account fee not verified; using estimated {schedule.tier} taker factor {schedule.factor()}")
        status = VerificationStatus.PARTIALLY_VERIFIED.value
    if not settlement_rules_known:
        reasons.append("settlement period/overtime/void rules not confirmed")
        status = VerificationStatus.PARTIALLY_VERIFIED.value

    return VerificationResult(
        status=status,
        executable=True,
        reasons=tuple(reasons),
        depth_status=depth_status or "VERIFIED",
        quote_fresh=quote_fresh,
        fee_status=schedule.status,
        settlement_rules_known=settlement_rules_known,
        max_price=M,
        independent=independent,
        optimizer=optimizer,
        discrepancies=tuple(discrepancies),
        equivalence=equivalence,
        checked_at=now,
    )


def _blocked(reason, equivalence, schedule, now, *, independent=None, optimizer=None,
             discrepancies=(), depth_status="UNKNOWN"):
    return VerificationResult(
        status=VerificationStatus.BLOCKED.value,
        executable=False,
        reasons=(reason,),
        depth_status=depth_status,
        quote_fresh=False,
        fee_status=schedule.status,
        settlement_rules_known=False,
        max_price=ZERO,
        independent=independent or {},
        optimizer=optimizer or {},
        discrepancies=tuple(discrepancies),
        equivalence=equivalence,
        checked_at=now,
    )


# --- post-execution reconciliation -------------------------------------------

@dataclass(frozen=True)
class ExecutionReconciliation:
    required_contracts: Decimal
    actual_contracts: Decimal
    partial: bool
    actual_lay_stake: Decimal
    actual_liability: Decimal
    actual_fee: Decimal
    exposure: Decimal
    per_outcome: dict
    status: str
    reasons: Tuple[str, ...]


def reconcile_execution(
    opportunity,
    actual_fills: Sequence[Tuple[Decimal, Decimal]],
    *,
    actual_back_stake=None,
    actual_back_odds=None,
    actual_fee=None,
    fee_schedule: Optional[FeeSchedule] = None,
) -> ExecutionReconciliation:
    """Reconcile independent fill data against the predicted hedge.

    Never marks a hedge executed from a simulated fill; this consumes *actual*
    fills the user recorded (or, when authorized, STX account fills).
    """
    schedule = fee_schedule or FeeSchedule()
    raw_book = getattr(opportunity, "order_book", None)
    book_quote = getattr(opportunity, "book_quote", None)
    M = _dec(getattr(raw_book, "max_price", ONE) if raw_book is not None else ONE)
    kind = getattr(getattr(opportunity, "kind", None), "value", None) or "qualifying"
    stake = _dec(actual_back_stake if actual_back_stake is not None else opportunity.stake)
    back_odds = _dec(actual_back_odds if actual_back_odds is not None else getattr(book_quote, "decimal_odds", opportunity.back_odds))
    required = required_contracts(kind, stake, back_odds, M)
    walk = walk_levels(
        [type("L", (), {"price": p, "contracts": c})() for p, c in actual_fills], required, M
    )
    fee = _dec(actual_fee) if actual_fee is not None else schedule.order_fee(walk["fills"], M)
    pnl = outcome_pnl(kind, stake, back_odds, walk, fee)
    partial = walk["filled"] < required

    reasons = []
    exposure = ZERO
    status = "HEDGED"
    if partial:
        status = "PARTIAL_HEDGE"
        reasons.append(f"partial fill: {walk['filled']} of {required} contracts")
        # Material unhedged exposure: the difference between the two outcomes.
        exposure = abs(pnl["profit_if_back"] - pnl["profit_if_lay"])
    if not actual_fills:
        status = "UNVERIFIED"
        reasons.append("no actual fills supplied")
    return ExecutionReconciliation(
        required_contracts=required,
        actual_contracts=walk["filled"],
        partial=partial,
        actual_lay_stake=walk["lay_stake"],
        actual_liability=walk["liability"],
        actual_fee=fee,
        exposure=exposure,
        per_outcome=pnl,
        status=status,
        reasons=tuple(reasons),
    )
