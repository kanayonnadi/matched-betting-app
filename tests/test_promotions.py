from decimal import Decimal

from opportunities import BetKind, discover_mock_opportunities
from promotions import (
    EligibilityStatus,
    Promotion,
    PromotionStatus,
    best_conversion,
    best_qualifying,
    parse_odds,
    parse_terms,
    promotion_from_terms,
    rank_promotions,
    sequence_promotions,
    validate_terms,
    value_promotion,
)
from promotions.parser import american_to_decimal

EXAMPLE = (
    "Bet $10 and receive $50 in bonus bets. Minimum odds -250. "
    "Bonus issued as two $25 tokens. Bonus bets expire after seven days. "
    "Stake not returned. New Ontario customers only."
)


def test_american_odds_conversion():
    assert american_to_decimal(Decimal("-250")) == Decimal("1.40")
    assert american_to_decimal(Decimal("150")) == Decimal("2.50")
    assert parse_odds("-250") == Decimal("1.40")
    assert parse_odds("1.40") == Decimal("1.40")


def test_parse_example_terms():
    terms = parse_terms(EXAMPLE)
    assert terms.offer_type == "BET_GET"
    assert terms.qualifying_stake == Decimal("10")
    assert terms.qualifying_min_odds == Decimal("1.40")
    assert terms.reward_amount == Decimal("50")
    assert terms.reward_type == "FREE_BET_SNR"
    assert terms.reward_count == 2
    assert terms.stake_returned is False
    assert terms.reward_expiry_days == 7
    assert terms.new_customer_only is True
    assert terms.jurisdiction == "Ontario"
    assert terms.unknown_fields == ()
    assert terms.confidence == 1.0


def test_parse_missing_terms_flags_review():
    terms = parse_terms("Get a free bet when you join.")
    assert terms.reward_amount is None
    status, issues = validate_terms(terms)
    assert status == PromotionStatus.NEEDS_REVIEW.value
    assert "reward amount unclear" in issues


def test_promotion_from_terms_status():
    promotion = promotion_from_terms(parse_terms(EXAMPLE), "Book A", "$10 -> $50")
    assert promotion.status == PromotionStatus.VERIFIED.value
    assert promotion.reward_token_amount == Decimal("25")
    assert promotion.eligibility == EligibilityStatus.UNKNOWN.value


def qualifying_opportunities(stake=10):
    return discover_mock_opportunities(stake)


def conversion_opportunities(stake=25):
    return discover_mock_opportunities(stake, kind=BetKind.FREE_BET_SNR)


def ready_promotion(**overrides):
    base = dict(
        id=1,
        sportsbook="Book A",
        name="$10 -> 2x$25",
        offer_type="BET_GET",
        qualifying_stake=Decimal("10"),
        qualifying_min_odds=Decimal("1.4"),
        reward_amount=Decimal("50"),
        reward_type="FREE_BET_SNR",
        reward_count=2,
        status=PromotionStatus.VERIFIED.value,
        eligibility=EligibilityStatus.ELIGIBLE.value,
        confidence=1.0,
    )
    base.update(overrides)
    return Promotion(**base)


def test_best_qualifying_ranked_by_loss():
    candidates = best_qualifying(ready_promotion(), qualifying_opportunities())
    assert candidates
    losses = [c.opportunity.qualifying_loss for c in candidates]
    assert losses == sorted(losses)


def test_best_conversion_ranked_by_value():
    candidates = best_conversion(Decimal("25"), conversion_opportunities())
    assert candidates
    values = [c.opportunity.expected_profit for c in candidates]
    assert values == sorted(values, reverse=True)


def test_value_promotion_end_to_end():
    valuation = value_promotion(
        ready_promotion(),
        qualifying_opportunities(),
        conversion_opportunities(),
        free_capital=Decimal("500"),
    )
    assert valuation.ready is True
    assert valuation.expected_net_profit > 0
    assert valuation.expected_reward_value > 0
    assert valuation.peak_capital > 0
    assert valuation.roi_on_capital > 0


def test_value_promotion_not_ready_when_not_eligible():
    valuation = value_promotion(
        ready_promotion(eligibility=EligibilityStatus.UNKNOWN.value),
        qualifying_opportunities(),
        conversion_opportunities(),
    )
    assert valuation.ready is False
    assert any("eligibility" in warning for warning in valuation.warnings)


def test_value_promotion_insufficient_capital():
    valuation = value_promotion(
        ready_promotion(),
        qualifying_opportunities(),
        conversion_opportunities(),
        free_capital=Decimal("1"),
    )
    assert valuation.ready is False
    assert any("capital" in warning for warning in valuation.warnings)


def test_valuation_does_not_double_count_fees():
    # A zero-reward promotion's net is exactly the (already fee-inclusive)
    # qualifying loss; deducting the fee again would double-count it.
    promotion = ready_promotion(reward_amount=Decimal("0"), reward_count=1)
    opportunities = discover_mock_opportunities(10)
    valuation = value_promotion(promotion, opportunities, [])
    best = best_qualifying(promotion, opportunities, limit=1)[0]
    assert valuation.expected_qualifying_loss == best.opportunity.qualifying_loss
    assert valuation.expected_net_profit == -best.opportunity.qualifying_loss


def test_ranking_and_sequencing():
    a = value_promotion(ready_promotion(id=1, name="A"), qualifying_opportunities(), conversion_opportunities())
    b = value_promotion(
        ready_promotion(id=2, name="B", reward_amount=Decimal("100"), reward_count=4),
        qualifying_opportunities(),
        conversion_opportunities(),
    )
    ranked = rank_promotions([a, b], sort_by="highest_profit")
    assert ranked[0].expected_net_profit >= ranked[1].expected_net_profit

    plan, total = sequence_promotions([a, b], bankroll=Decimal("500"))
    assert total == sum((v.expected_net_profit for v in plan), Decimal("0"))
    assert all(v.peak_capital <= 500 for v in plan)
