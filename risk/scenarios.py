"""Deterministic execution/bankroll stress scenarios (fixtures only)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from liquidity import (
    LiquidityGrade,
    build_order_book,
    score_liquidity,
    simulate_execution,
)
from matching import EventMatcher
from models import Event, ExchangeOdds, NormalizedOdds, Side
from opportunities import build_opportunity, discover_live_opportunities
from promotions import RewardToken, RewardTokenStatus, effective_status
from settlement import (
    ActualSettlement,
    SettledOutcome,
    prediction_from_opportunity,
    reconcile,
)

from .bankroll_reservations import available_capital, can_fund
from .exposure import compute_exposure
from .models import ScenarioResult

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
START = datetime(2026, 10, 8, 20, 0, tzinfo=timezone.utc)
BIDS = [
    {"price": "0.6200", "quantity": "461.00"},
    {"price": "0.6100", "quantity": "399.00"},
    {"price": "0.5900", "quantity": "399.00"},
]
BIDS_ALT = [
    {"price": "0.6100", "quantity": "200.00"},
    {"price": "0.6000", "quantity": "200.00"},
]


def _book_quote(odds="1.60"):
    return NormalizedOdds(
        provider="theoddsapi:betano_ca_on", event_id="b1", sport="ice_hockey", league="NHL",
        home_team="Colorado Avalanche", away_team="Winnipeg Jets", start_time=START,
        market="moneyline", selection="Colorado Avalanche", side=Side.BACK,
        decimal_odds=Decimal(odds), timestamp=NOW,
    )


def _exchange_quote():
    return ExchangeOdds(
        provider="stx", event_id="s1", sport="ice_hockey", league="NHL",
        home_team="Colorado Avalanche", away_team="Winnipeg Jets", start_time=START,
        market="moneyline", selection="Colorado Avalanche", side=Side.LAY,
        decimal_odds=Decimal("1.6129"), timestamp=NOW, available_size=Decimal("285.82"),
        commission=Decimal("0"), fee_model="per_wager", fee_factor=Decimal("0.10"),
        max_price=Decimal("1"),
    )


def _opportunity(back="1.60", bids=BIDS, stake=100, ts=NOW, completeness="COMPLETE"):
    return build_opportunity(
        _book_quote(back), _exchange_quote(), stake,
        order_book=build_order_book(bids, "1", ts, completeness=completeness), now=ts,
    )


def scenario_liquidity_disappears():
    empty = build_order_book([], "1", NOW, completeness="COMPLETE")
    execution = simulate_execution(empty, Decimal("160"), Decimal("0.10"), now=NOW)
    exposure = compute_exposure(100, "1.60", "160", [], "1")
    passed = (
        execution.fully_fillable is False
        and exposure.status == "NO_HEDGE"
        and exposure.risk == "HIGH"
    )
    return ScenarioResult(
        "liquidity_disappears", "no hedge, HIGH risk",
        f"{exposure.status}/{exposure.risk}", passed,
        {"worst_case_pnl": str(exposure.worst_case_pnl)},
    )


def scenario_partial_fill():
    exposure = compute_exposure(100, "1.60", "160", [(Decimal("0.62"), Decimal("64"))], "1")
    passed = (
        exposure.status == "INCOMPLETE"
        and exposure.risk == "HIGH"
        and exposure.unhedged_contracts == Decimal("96")
        and exposure.worst_case_pnl < 0
    )
    return ScenarioResult(
        "partial_fill", "INCOMPLETE, unhedged 96", f"{exposure.status}, {exposure.unhedged_contracts}",
        passed, {"worst_case_pnl": str(exposure.worst_case_pnl)},
    )


def scenario_sportsbook_odds_change():
    first = _opportunity(back="1.60")
    moved = _opportunity(back="1.70")
    passed = first.qualifying_loss != moved.qualifying_loss
    return ScenarioResult(
        "sportsbook_odds_change", "qualifying loss changes",
        f"{first.qualifying_loss:.2f} vs {moved.qualifying_loss:.2f}", passed, {},
    )


def scenario_exchange_odds_change():
    first = _opportunity(bids=BIDS)
    moved = _opportunity(bids=BIDS_ALT)
    passed = first.qualifying_loss != moved.qualifying_loss
    return ScenarioResult(
        "exchange_odds_change", "qualifying loss changes",
        f"{first.qualifying_loss:.2f} vs {moved.qualifying_loss:.2f}", passed, {},
    )


def scenario_stale_order_book():
    old = NOW - timedelta(seconds=120)
    execution = simulate_execution(build_order_book(BIDS, "1", old, completeness="COMPLETE"),
                                   Decimal("160"), Decimal("0.10"), now=NOW)
    score = score_liquidity(execution, max_book_age_seconds=30)
    passed = score.grade is LiquidityGrade.INSUFFICIENT
    return ScenarioResult(
        "stale_order_book", "INSUFFICIENT", score.grade.value, passed,
        {"age_seconds": str(execution.book_age_seconds)},
    )


def scenario_market_suspension():
    book_events = [
        Event(
            provider="book", event_id="b1", sport="ice_hockey", league="NHL",
            home_team="Colorado Avalanche", away_team="Winnipeg Jets", start_time=START,
        )
    ]
    matches = EventMatcher().find_matches(book_events, [])
    passed = matches == []
    return ScenarioResult("market_suspension", "no matches", f"{len(matches)} matches", passed, {})


class _FailingBook:
    source_type = "MOCK"

    def get_events(self, sport):
        raise RuntimeError("provider unavailable")


def scenario_api_failure_isolation():
    from providers import MockExchangeProvider, SportRoute

    route = SportRoute("basketball", "Basketball", ("basketball",), "basketball")
    opportunities = discover_live_opportunities(
        _FailingBook(), MockExchangeProvider(), [route], 10
    )
    passed = opportunities == []
    return ScenarioResult(
        "api_failure_isolation", "no crash, empty result", f"{len(opportunities)} opportunities",
        passed, {},
    )


def scenario_bonus_expiration():
    token = RewardToken(
        face_value=Decimal("25"), status=RewardTokenStatus.RECEIVED.value,
        expires_at=NOW - timedelta(seconds=1),
    )
    status = effective_status(token, now=NOW)
    passed = status == RewardTokenStatus.EXPIRED.value
    return ScenarioResult("bonus_expiration", "EXPIRED", status, passed, {})


def scenario_event_cancellation():
    opportunity = _opportunity()
    prediction = prediction_from_opportunity(opportunity, 1)
    actual = ActualSettlement(
        bet_id=1, outcome=SettledOutcome.VOID.value, actual_total_pnl=Decimal("0"),
        settlement_key="void", settled_at=NOW,
    )
    result = reconcile(prediction, actual)
    passed = result.status in ("EXACT_MATCH", "WITHIN_TOLERANCE")
    return ScenarioResult("event_cancellation", "void, P&L 0", result.status, passed, {})


def scenario_insufficient_sportsbook_funds():
    state = available_capital(
        [{"account": "Book A", "amount": 50}],
        [{"account": "Book A", "amount": 400}],
    )
    passed = not can_fund("Book A", Decimal("100"), state) and state.available["Book A"] < 0
    return ScenarioResult(
        "insufficient_sportsbook_funds", "cannot fund",
        f"available {state.available['Book A']}", passed, {},
    )


def scenario_double_allocation():
    state = available_capital(
        [{"account": "Exchange", "amount": 300}],
        [{"account": "Exchange", "amount": 250}],
    )
    # A second identical reservation would exceed available capital.
    passed = not can_fund("Exchange", Decimal("250"), state)
    return ScenarioResult(
        "double_allocation", "second allocation blocked",
        f"available {state.available['Exchange']}", passed, {},
    )


SCENARIOS = (
    scenario_liquidity_disappears,
    scenario_partial_fill,
    scenario_sportsbook_odds_change,
    scenario_exchange_odds_change,
    scenario_stale_order_book,
    scenario_market_suspension,
    scenario_api_failure_isolation,
    scenario_bonus_expiration,
    scenario_event_cancellation,
    scenario_insufficient_sportsbook_funds,
    scenario_double_allocation,
)


def stress_suite() -> list:
    return [scenario() for scenario in SCENARIOS]
