"""Reusable, Streamlit-independent matched-betting calculations."""

from .decimal_utils import (
    MONEY_QUANTUM,
    ODDS_QUANTUM,
    PERCENT_QUANTUM,
    quantize_money,
    quantize_odds,
    to_decimal,
)
from .arbitrage import ArbitrageResult, calculate_arbitrage
from .depth import (
    calculate_free_bet_snr_with_execution,
    calculate_qualifying_with_execution,
)
from .freebet import FreeBetResult, calculate_free_bet_snr
from .per_wager import (
    calculate_free_bet_snr_per_wager,
    calculate_qualifying_per_wager,
    fee_rate,
)
from .qualifying import QualifyingResult, calculate_qualifying

__all__ = [
    "ArbitrageResult",
    "FreeBetResult",
    "QualifyingResult",
    "calculate_arbitrage",
    "calculate_free_bet_snr",
    "calculate_free_bet_snr_per_wager",
    "calculate_free_bet_snr_with_execution",
    "calculate_qualifying",
    "calculate_qualifying_per_wager",
    "calculate_qualifying_with_execution",
    "fee_rate",
    "quantize_money",
    "quantize_odds",
    "to_decimal",
    "MONEY_QUANTUM",
    "ODDS_QUANTUM",
    "PERCENT_QUANTUM",
]
