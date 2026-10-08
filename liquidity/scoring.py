"""Liquidity scoring derived from an execution estimate.

The grade is explainable: it is a deterministic function of fill ratio, order
book age, slippage and available depth beyond the required hedge.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple

from .models import DepthCompleteness, ExecutionEstimate, LiquidityGrade

ZERO = Decimal("0")

DEFAULT_MAX_BOOK_AGE_SECONDS = 30
DEFAULT_HIGH_SLIPPAGE = Decimal("0.5")
DEFAULT_MEDIUM_SLIPPAGE = Decimal("1.5")
DEFAULT_HIGH_DEPTH_RATIO = Decimal("2")


@dataclass(frozen=True)
class LiquidityScore:
    grade: LiquidityGrade
    fill_ratio: Decimal
    slippage_pct: Decimal
    levels_consumed: int
    depth_ratio: Decimal
    book_age_seconds: Optional[Decimal]
    reasons: Tuple[str, ...]

    @property
    def is_executable(self) -> bool:
        return self.grade is not LiquidityGrade.INSUFFICIENT


def score_liquidity(
    execution: ExecutionEstimate,
    max_book_age_seconds: float = DEFAULT_MAX_BOOK_AGE_SECONDS,
    high_slippage: Decimal = DEFAULT_HIGH_SLIPPAGE,
    medium_slippage: Decimal = DEFAULT_MEDIUM_SLIPPAGE,
    high_depth_ratio: Decimal = DEFAULT_HIGH_DEPTH_RATIO,
) -> LiquidityScore:
    reasons = []

    if not execution.fully_fillable:
        reasons.append("insufficient depth for the full hedge")
        grade = LiquidityGrade.INSUFFICIENT
    else:
        age = execution.book_age_seconds
        completeness = execution.completeness
        if age is not None and age > Decimal(str(max_book_age_seconds)):
            reasons.append(f"order book stale ({age:.0f}s old)")
            grade = LiquidityGrade.INSUFFICIENT
        elif completeness != DepthCompleteness.COMPLETE.value:
            # Cannot assume unobserved depth: never grade HIGH on a book that may
            # be truncated. Still executable if the observed levels cover it.
            reasons.append(f"depth completeness {completeness}")
            grade = (
                LiquidityGrade.MEDIUM
                if execution.slippage_pct <= medium_slippage
                else LiquidityGrade.LOW
            )
        elif (
            execution.slippage_pct <= high_slippage
            and execution.depth_ratio >= high_depth_ratio
        ):
            grade = LiquidityGrade.HIGH
            reasons.append("deep book, minimal slippage")
        elif execution.slippage_pct <= medium_slippage:
            grade = LiquidityGrade.MEDIUM
            reasons.append("moderate slippage")
        else:
            grade = LiquidityGrade.LOW
            reasons.append("high slippage")

    return LiquidityScore(
        grade=grade,
        fill_ratio=execution.fill_ratio,
        slippage_pct=execution.slippage_pct,
        levels_consumed=execution.levels_consumed,
        depth_ratio=execution.depth_ratio,
        book_age_seconds=execution.book_age_seconds,
        reasons=tuple(reasons),
    )
