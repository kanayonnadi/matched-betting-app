"""STX executable depth and liquidity engine."""

from .execution import (
    required_contracts,
    required_contracts_free_bet,
    simulate_execution,
)
from .models import (
    BookLevel,
    DepthCompleteness,
    ExecutionEstimate,
    Fill,
    LiquidityGrade,
    OrderBook,
)
from .scoring import (
    DEFAULT_HIGH_DEPTH_RATIO,
    DEFAULT_HIGH_SLIPPAGE,
    DEFAULT_MAX_BOOK_AGE_SECONDS,
    DEFAULT_MEDIUM_SLIPPAGE,
    LiquidityScore,
    score_liquidity,
)
from .stx_depth import build_order_book, build_order_book_from_ws

__all__ = [
    "BookLevel",
    "DepthCompleteness",
    "DEFAULT_HIGH_DEPTH_RATIO",
    "DEFAULT_HIGH_SLIPPAGE",
    "DEFAULT_MAX_BOOK_AGE_SECONDS",
    "DEFAULT_MEDIUM_SLIPPAGE",
    "ExecutionEstimate",
    "Fill",
    "LiquidityGrade",
    "LiquidityScore",
    "OrderBook",
    "build_order_book",
    "build_order_book_from_ws",
    "required_contracts",
    "required_contracts_free_bet",
    "score_liquidity",
    "simulate_execution",
]
