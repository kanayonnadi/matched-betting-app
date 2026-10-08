"""Reconciliation engine.

``reconcile`` compares the *matched* predicted outcome against actual settlement
and reports differences with clear statuses. Missing data is never reconciled.
The STX fee formula is independently re-derived from the fill vector.
"""

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Optional, Sequence

from .models import (
    ActualSettlement,
    Difference,
    FeeValidation,
    PredictionSnapshot,
    ReconciliationResult,
    ReconciliationStatus,
    SettledOutcome,
)

CENT = Decimal("0.01")
ZERO = Decimal("0")

# The STX fee formula derives from the operator fee schedule supplied by the
# user; it has NOT been confirmed against official documentation or real
# transactions.
FEE_MODEL_STATUS = "EXTERNALLY_UNVERIFIED"

FEE_MODEL_ASSUMPTIONS = (
    "STX per-wager fee: ceil(factor / max_price * sum(contracts_i * price_i * "
    "(max_price - price_i))), rounded up to the cent once per order across all "
    "fills. Taker factor 0.10; maker 0.00. price and max_price are dollars; "
    "contracts are contract counts."
)


@dataclass(frozen=True)
class Tolerances:
    pnl: Decimal = Decimal("0.01")
    fee: Decimal = Decimal("0.01")
    liability: Decimal = Decimal("0.01")
    contracts: Decimal = Decimal("0")


def stx_fee_from_fills(fills, max_price, fee_factor) -> Optional[Decimal]:
    if max_price is None or fee_factor is None or not fills:
        return None
    total = sum((contracts * price * (max_price - price) for price, contracts in fills), ZERO)
    raw = fee_factor / max_price * total
    return raw.quantize(CENT, rounding=ROUND_CEILING) if raw > ZERO else ZERO


def validate_prediction_fee(prediction: PredictionSnapshot) -> FeeValidation:
    computed = stx_fee_from_fills(
        prediction.predicted_fills, prediction.max_price, prediction.fee_factor
    )
    declared = prediction.predicted_exchange_fee
    if computed is None:
        return FeeValidation(None, declared, None, None, "insufficient fill/model data")
    difference = computed - declared
    return FeeValidation(
        computed_fee=computed,
        declared_fee=declared,
        difference=difference,
        matches=difference == ZERO,
        note="independent recomputation from fill vector",
    )


def predicted_pnl_for(outcome: str, prediction: PredictionSnapshot) -> Optional[Decimal]:
    if outcome == SettledOutcome.BACK_WON.value:
        return prediction.predicted_back_win_pnl
    if outcome == SettledOutcome.LAY_WON.value:
        return prediction.predicted_lay_win_pnl
    if outcome in (
        SettledOutcome.VOID.value,
        SettledOutcome.PUSH.value,
        SettledOutcome.CANCELLED.value,
    ):
        return ZERO
    return None


def _difference(predicted, actual) -> Optional[Decimal]:
    if predicted is None or actual is None:
        return None
    return actual - predicted


def _collect_differences(prediction, actual) -> tuple:
    pairs = (
        ("pnl", predicted_pnl_for(actual.outcome, prediction), actual.actual_total_pnl),
        ("fee", prediction.predicted_exchange_fee, actual.actual_exchange_fee),
        ("liability", prediction.predicted_liability, actual.actual_liability),
        ("contracts", prediction.predicted_lay_contracts, actual.actual_lay_contracts),
        ("back_stake", prediction.predicted_back_stake, actual.actual_back_stake),
        ("back_odds", prediction.predicted_back_odds, actual.actual_back_odds),
    )
    return tuple(
        Difference(metric, predicted, actual_value, _difference(predicted, actual_value))
        for metric, predicted, actual_value in pairs
    )


def reconcile(
    prediction: PredictionSnapshot,
    actual: Optional[ActualSettlement] = None,
    tolerances: Optional[Tolerances] = None,
) -> ReconciliationResult:
    tol = tolerances or Tolerances()

    if actual is None:
        return ReconciliationResult(
            bet_id=prediction.bet_id,
            status=ReconciliationStatus.UNVERIFIED.value,
            predicted_outcome=SettledOutcome.UNKNOWN.value,
            predicted_pnl=None,
            actual_pnl=None,
            pnl_difference=None,
            fee_difference=None,
            liability_difference=None,
            contracts_difference=None,
            flags=("no actual settlement recorded",),
            message="UNVERIFIED: awaiting actual settlement",
        )

    predicted_pnl = predicted_pnl_for(actual.outcome, prediction)
    actual_pnl = actual.actual_total_pnl
    if actual_pnl is None and (
        actual.actual_sportsbook_settlement is not None
        or actual.actual_exchange_settlement is not None
    ):
        actual_pnl = (actual.actual_sportsbook_settlement or ZERO) + (
            actual.actual_exchange_settlement or ZERO
        )

    flags = []
    if actual.outcome == SettledOutcome.UNKNOWN.value:
        flags.append("settled outcome unknown")
    if (
        prediction.predicted_lay_contracts is not None
        and actual.actual_lay_contracts is not None
        and actual.actual_lay_contracts < prediction.predicted_lay_contracts
    ):
        flags.append("partial fill: fewer contracts than predicted")
    if (
        actual.actual_fill_prices
        and prediction.predicted_fills
        and tuple(actual.actual_fill_prices) != tuple(prediction.predicted_fills)
    ):
        flags.append("execution prices differ from prediction")
    if actual.outcome == SettledOutcome.VOID.value:
        flags.append("void bet")
    if actual.outcome == SettledOutcome.CANCELLED.value:
        flags.append("cancelled bet")
    if actual.outcome == SettledOutcome.PUSH.value:
        flags.append("push")
    if actual.actual_sportsbook_settlement is None:
        flags.append("sportsbook settlement missing")
    if actual.actual_exchange_settlement is None:
        flags.append("exchange settlement missing")

    terminal = actual.outcome in (
        SettledOutcome.VOID.value,
        SettledOutcome.PUSH.value,
        SettledOutcome.CANCELLED.value,
    )

    missing = []
    if actual.outcome == SettledOutcome.UNKNOWN.value:
        missing.append("settled outcome unknown")
    if predicted_pnl is None:
        missing.append("predicted scenario unavailable for outcome")
    if actual_pnl is None:
        missing.append("actual P&L missing")
    if not terminal and actual.actual_exchange_fee is None:
        missing.append("actual exchange fee missing")
    if not terminal and actual.actual_liability is None:
        missing.append("actual liability missing")

    differences = _collect_differences(prediction, actual)
    pnl_difference = _difference(predicted_pnl, actual_pnl)

    if terminal:
        # Voided/pushed/cancelled bets release the hedge; only P&L is compared.
        fee_difference = None
        liability_difference = None
        contracts_difference = None
    else:
        fee_difference = _difference(prediction.predicted_exchange_fee, actual.actual_exchange_fee)
        liability_difference = _difference(prediction.predicted_liability, actual.actual_liability)
        contracts_difference = _difference(
            prediction.predicted_lay_contracts, actual.actual_lay_contracts
        )

    if missing:
        status = ReconciliationStatus.MISSING_DATA.value
        message = "MISSING_DATA: " + "; ".join(missing)
    elif terminal:
        if pnl_difference == ZERO:
            status = ReconciliationStatus.EXACT_MATCH.value
            message = "voided/pushed: predicted and actual P&L match"
        elif abs(pnl_difference) <= tol.pnl:
            status = ReconciliationStatus.WITHIN_TOLERANCE.value
            message = "voided/pushed: within tolerance"
        else:
            status = ReconciliationStatus.DISCREPANCY.value
            message = "voided/pushed: P&L discrepancy"
    elif (
        pnl_difference == ZERO
        and fee_difference == ZERO
        and liability_difference == ZERO
        and (contracts_difference or ZERO) == ZERO
    ):
        status = ReconciliationStatus.EXACT_MATCH.value
        message = "predicted and actual match exactly"
    elif (
        abs(pnl_difference) <= tol.pnl
        and abs(fee_difference) <= tol.fee
        and abs(liability_difference) <= tol.liability
        and abs(contracts_difference or ZERO) <= tol.contracts
    ):
        status = ReconciliationStatus.WITHIN_TOLERANCE.value
        message = "within tolerance"
    else:
        status = ReconciliationStatus.DISCREPANCY.value
        message = "discrepancy exceeds tolerance"

    return ReconciliationResult(
        bet_id=prediction.bet_id,
        status=status,
        predicted_outcome=actual.outcome,
        predicted_pnl=predicted_pnl,
        actual_pnl=actual_pnl,
        pnl_difference=pnl_difference,
        fee_difference=fee_difference,
        liability_difference=liability_difference,
        contracts_difference=contracts_difference,
        differences=differences,
        flags=tuple(flags),
        message=message,
    )
