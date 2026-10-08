from decimal import Decimal

from analytics import overview
from database import init_db, insert_bet, list_bets
from opportunities import (
    OpportunityFilter,
    discover_arbitrage,
    discover_mock_opportunities,
    rank_opportunities,
)
from providers import MockSecondSportsbookProvider, MockSportsbookProvider


def _log_opportunity(opportunity, db_path):
    return insert_bet(
        event=opportunity.event_label,
        bookmaker=opportunity.book_provider,
        exchange=opportunity.exchange_provider,
        market=opportunity.market,
        selection=opportunity.selection,
        sport=opportunity.sport,
        bet_type="Qualifying bet",
        back_stake=float(opportunity.stake),
        back_odds=float(opportunity.back_odds),
        lay_odds=float(opportunity.lay_odds),
        commission=float(opportunity.commission * 100),
        lay_stake=float(opportunity.lay_stake),
        liability=float(opportunity.lay_liability),
        profit_if_back=float(opportunity.profit_if_back),
        profit_if_lay=float(opportunity.profit_if_lay),
        db_path=db_path,
    )


def test_end_to_end_mocked_betting_pipeline(tmp_path):
    db_path = tmp_path / "integration.db"
    init_db(db_path)

    opportunities = discover_mock_opportunities(100)
    assert len(opportunities) == 9

    basketball = OpportunityFilter(sport="basketball").apply(opportunities)
    ranked = rank_opportunities(basketball)
    assert all(o.sport == "basketball" for o in ranked)
    assert ranked[0].qualifying_loss_pct <= ranked[-1].qualifying_loss_pct

    best = ranked[0]
    _log_opportunity(best, db_path)
    rows = list_bets(db_path)
    assert len(rows) == 1
    assert rows[0]["sport"] == "basketball"
    assert rows[0]["selection"] == best.selection

    result = overview(rows, [])
    assert result.lifetime_profit < 0  # a qualifying bet is a cost
    assert result.capital_deployed > 0


def test_end_to_end_arbitrage_pipeline():
    opportunities = discover_arbitrage(
        [MockSportsbookProvider(), MockSecondSportsbookProvider()],
        ["basketball", "soccer"],
        100,
    )
    assert len(opportunities) == 1
    arb = opportunities[0]
    payout = arb.total_stake + arb.profit
    for leg in arb.legs:
        assert abs(leg.stake * leg.decimal_odds - payout) < Decimal("1e-9")
