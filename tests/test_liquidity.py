from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from liquidity import (
    LiquidityGrade,
    build_order_book,
    build_order_book_from_ws,
    required_contracts,
    required_contracts_free_bet,
    score_liquidity,
    simulate_execution,
)

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)

# Observed live STX Colorado Avalanche book (max_price = 1).
AVALANCHE_BIDS = [
    {"price": "0.6200", "quantity": "461.00"},
    {"price": "0.6100", "quantity": "399.00"},
    {"price": "0.5900", "quantity": "399.00"},
]


def book(bids=None, max_price="1", timestamp=NOW, completeness="COMPLETE"):
    return build_order_book(
        bids if bids is not None else AVALANCHE_BIDS, max_price, timestamp, completeness=completeness
    )


def test_order_book_sorts_best_first():
    unsorted = [
        {"price": "0.5900", "quantity": "10"},
        {"price": "0.6200", "quantity": "10"},
        {"price": "0.6100", "quantity": "10"},
    ]
    ob = build_order_book(unsorted, "1")
    assert [level.price for level in ob.levels] == [
        Decimal("0.6200"),
        Decimal("0.6100"),
        Decimal("0.5900"),
    ]


def test_build_from_ws_ob():
    ob = build_order_book_from_ws({"b": [{"p": 0.62, "q": 461.0, "l": 285.82}]}, "1", NOW)
    assert len(ob.levels) == 1
    assert ob.levels[0].price == Decimal("0.62")
    assert ob.levels[0].contracts == Decimal("461.0")


def test_build_skips_malformed_levels():
    bids = [
        {"price": "0", "quantity": "10"},
        {"price": "-1", "quantity": "10"},
        {"price": "1.0", "quantity": "10"},  # >= max_price
        {"price": "0.5", "quantity": ""},
        {"price": "0.5", "quantity": "0"},
        {"price": "0.5", "quantity": "10"},
    ]
    ob = build_order_book(bids, "1")
    assert len(ob.levels) == 1


def test_required_contracts_helpers():
    assert required_contracts(300, "1.60", "1") == Decimal("480.00")
    assert required_contracts_free_bet(50, "4.0", "1") == Decimal("150.0")


def test_single_level_fill():
    result = simulate_execution(book(), 165, Decimal("0.10"))
    assert result.fully_fillable is True
    assert result.levels_consumed == 1
    assert result.lay_stake == Decimal("102.30")  # 165 * 0.62
    assert result.liability == Decimal("62.70")   # 165 * 0.38
    assert result.estimated_fee == Decimal("3.89")  # ceil(0.10*165*0.62*0.38 = 3.8874)
    assert result.slippage_pct == Decimal("0")


def test_multi_level_fill():
    result = simulate_execution(book(), 480, Decimal("0.10"))
    assert result.levels_consumed == 2
    assert result.fully_fillable is True
    assert result.fillable_contracts == Decimal("480")
    assert result.fills[0].contracts == Decimal("461")
    assert result.fills[1].contracts == Decimal("19")
    assert result.lay_stake == Decimal("297.41")   # 461*0.62 + 19*0.61
    assert result.liability == Decimal("182.59")   # 1*480 - 297.41
    assert result.estimated_fee == Decimal("11.32")
    assert result.effective_lay_odds > result.best_lay_odds  # slippage up in odds


def test_exact_liquidity():
    result = simulate_execution(book(), 461, Decimal("0.10"))
    assert result.fully_fillable is True
    assert result.shortfall_contracts == Decimal("0")
    assert result.levels_consumed == 1


def test_excess_liquidity():
    result = simulate_execution(book(), 100, Decimal("0.10"))
    assert result.levels_consumed == 1
    assert result.depth_ratio == Decimal("1259") / Decimal("100")


def test_insufficient_liquidity():
    result = simulate_execution(book(), 2000, Decimal("0.10"))
    assert result.fully_fillable is False
    assert result.shortfall_contracts == Decimal("741")  # 2000 - 1259
    assert result.fill_ratio < 1
    assert result.levels_consumed == 3


def test_empty_book():
    result = simulate_execution(build_order_book([], "1", NOW), 100, Decimal("0.10"))
    assert result.fully_fillable is False
    assert result.fills == ()
    assert result.lay_stake == Decimal("0")
    assert result.effective_lay_odds is None
    assert result.best_lay_odds is None


def test_zero_fee_factor():
    result = simulate_execution(book(), 165, Decimal("0"))
    assert result.estimated_fee == Decimal("0")


def test_tiny_and_large_hedge():
    tiny = simulate_execution(book(), Decimal("0.5"), Decimal("0.10"))
    assert tiny.fillable_contracts == Decimal("0.5")
    assert tiny.levels_consumed == 1
    large = simulate_execution(book(), 1259, Decimal("0.10"))
    assert large.fully_fillable is True
    assert large.levels_consumed == 3


def test_book_age_and_stale_score():
    old = NOW - timedelta(seconds=45)
    result = simulate_execution(book(timestamp=old), 165, Decimal("0.10"), now=NOW)
    assert result.book_age_seconds == Decimal("45")
    score = score_liquidity(result, max_book_age_seconds=30)
    assert score.grade is LiquidityGrade.INSUFFICIENT
    assert "stale" in " ".join(score.reasons)


def test_liquidity_grades():
    deep = score_liquidity(simulate_execution(book(), 100, Decimal("0.10"), now=NOW))
    assert deep.grade is LiquidityGrade.HIGH
    insufficient = score_liquidity(simulate_execution(book(), 2000, Decimal("0.10"), now=NOW))
    assert insufficient.grade is LiquidityGrade.INSUFFICIENT


def test_invalid_required_contracts():
    with pytest.raises(ValueError):
        simulate_execution(book(), 0, Decimal("0.10"))
    with pytest.raises(ValueError):
        required_contracts(0, "2.0", "1")
    with pytest.raises(ValueError):
        required_contracts(100, "1.0", "1")
