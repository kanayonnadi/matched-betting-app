import pytest

from opportunities import (
    DEFAULT_SORT,
    discover_mock_opportunities,
    rank_opportunities,
    sort_key,
)


@pytest.fixture
def opportunities():
    return discover_mock_opportunities(100)


def test_default_sort_key():
    assert DEFAULT_SORT == "qualifying_loss_pct"


def test_default_ranking_prioritises_low_qualifying_loss(opportunities):
    ranked = rank_opportunities(opportunities)
    losses = [o.qualifying_loss_pct for o in ranked]
    assert losses == sorted(losses)


def test_rank_by_back_odds_descending(opportunities):
    ranked = rank_opportunities(opportunities, sort_by="back_odds", ascending=False)
    odds = [o.back_odds for o in ranked]
    assert odds == sorted(odds, reverse=True)


def test_rank_limit(opportunities):
    ranked = rank_opportunities(opportunities, limit=3)
    assert len(ranked) == 3


def test_unknown_sort_key_raises():
    with pytest.raises(ValueError):
        sort_key("does_not_exist")
