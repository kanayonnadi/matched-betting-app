"""Build immutable prediction snapshots from opportunities."""

import json
from datetime import datetime, timezone
from decimal import Decimal

from .models import PredictionSnapshot

ZERO = Decimal("0")


def _decimal(value):
    return None if value is None else Decimal(str(value))


def prediction_from_row(row) -> PredictionSnapshot:
    try:
        payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
    except (ValueError, KeyError):
        payload = {}
    fills = tuple(
        (Decimal(str(price)), Decimal(str(contracts)))
        for price, contracts in payload.get("predicted_fills", [])
    )
    return PredictionSnapshot(
        bet_id=int(row["bet_id"]),
        sportsbook=row["sportsbook"] or "",
        exchange=row["exchange"] or "",
        event=row["event"] or "",
        market=row["market"] or "",
        selection=row["selection"] or "",
        predicted_back_stake=_decimal(row["predicted_back_stake"]) or ZERO,
        predicted_back_odds=_decimal(row["predicted_back_odds"]) or ZERO,
        predicted_liability=_decimal(row["predicted_liability"]) or ZERO,
        predicted_exchange_fee=_decimal(row["predicted_exchange_fee"]) or ZERO,
        predicted_back_win_pnl=_decimal(row["predicted_back_win_pnl"]) or ZERO,
        predicted_lay_win_pnl=_decimal(row["predicted_lay_win_pnl"]) or ZERO,
        predicted_total_capital=_decimal(row["predicted_total_capital"]) or ZERO,
        predicted_qualifying_loss=_decimal(row["predicted_qualifying_loss"]) or ZERO,
        predicted_lay_contracts=_decimal(row["predicted_lay_contracts"]),
        predicted_lay_odds=_decimal(row["predicted_lay_odds"]),
        predicted_fills=fills,
        max_price=_decimal(row["max_price"]),
        fee_factor=_decimal(row["fee_factor"]),
        calculation_version=row["calculation_version"] or "m19.0",
        fee_model_version=row["fee_model_version"] or "stx-per-wager-v1",
    )


def prediction_from_opportunity(opportunity, bet_id, timestamp=None) -> PredictionSnapshot:
    fills = ()
    contracts = None
    execution = getattr(opportunity, "execution", None)
    if execution is not None:
        fills = tuple((fill.price, fill.contracts) for fill in execution.fills)
        contracts = sum((fill.contracts for fill in execution.fills), ZERO)
    return PredictionSnapshot(
        bet_id=int(bet_id),
        sportsbook=opportunity.book_provider,
        exchange=opportunity.exchange_provider,
        event=opportunity.event_label,
        market=opportunity.market,
        selection=opportunity.selection,
        predicted_back_stake=opportunity.stake,
        predicted_back_odds=opportunity.back_odds,
        predicted_liability=opportunity.lay_liability,
        predicted_exchange_fee=opportunity.lay_fee if opportunity.lay_fee is not None else ZERO,
        predicted_back_win_pnl=opportunity.profit_if_back,
        predicted_lay_win_pnl=opportunity.profit_if_lay,
        predicted_total_capital=opportunity.required_capital,
        predicted_qualifying_loss=opportunity.qualifying_loss,
        predicted_lay_contracts=contracts,
        predicted_lay_odds=(
            opportunity.effective_lay_odds
            if getattr(opportunity, "effective_lay_odds", None) is not None
            else opportunity.lay_odds
        ),
        predicted_fills=fills,
        max_price=getattr(opportunity, "max_price", None),
        fee_factor=getattr(opportunity, "fee_factor", None),
        timestamp=timestamp or datetime.now(timezone.utc),
        source_provenance=getattr(opportunity, "provenance", None),
    )
