from decimal import Decimal

import pytest

from offers import (
    ALL_STATUSES,
    OfferStatus,
    advance,
    can_transition,
    expected_value,
    is_active,
    summarize,
)


def offer(**overrides):
    base = {
        "id": 1,
        "bookmaker": "Book A",
        "name": "Sign up",
        "offer_type": "Sign-up",
        "qualifying_stake": 100.0,
        "min_odds": 2.0,
        "max_qualifying_loss": 5.0,
        "reward": 50.0,
        "reward_conversion": 75.0,
        "expiry": "2026-12-31",
        "status": OfferStatus.AVAILABLE.value,
        "notes": None,
        "bet_id": None,
    }
    base.update(overrides)
    return base


def test_all_statuses_present():
    assert len(ALL_STATUSES) == 7
    assert "Reward received" in ALL_STATUSES


def test_is_active():
    assert is_active(OfferStatus.AVAILABLE.value)
    assert not is_active(OfferStatus.COMPLETED.value)
    assert not is_active(OfferStatus.EXPIRED.value)


def test_transitions():
    assert can_transition(OfferStatus.AVAILABLE.value, OfferStatus.QUALIFYING_PLACED.value)
    assert can_transition(OfferStatus.QUALIFYING_PLACED.value, OfferStatus.QUALIFIED.value)
    assert can_transition(OfferStatus.REWARD_RECEIVED.value, OfferStatus.COMPLETED.value)
    assert not can_transition(OfferStatus.AVAILABLE.value, OfferStatus.COMPLETED.value)
    assert advance(OfferStatus.AVAILABLE.value, OfferStatus.QUALIFYING_PLACED.value) == (
        OfferStatus.QUALIFYING_PLACED.value
    )


def test_advance_rejects_invalid_and_unknown():
    with pytest.raises(ValueError):
        advance(OfferStatus.AVAILABLE.value, OfferStatus.COMPLETED.value)
    with pytest.raises(ValueError):
        advance(OfferStatus.AVAILABLE.value, "Bogus")


def test_expected_value():
    assert expected_value(50, 75, 5) == Decimal("32.5")
    # default conversion of 75% when unspecified
    assert expected_value(100, None, 0) == Decimal("75.0")


def test_summarize_available_offer_has_no_incurred_costs():
    summary = summarize([offer()])
    assert summary.active_offers == 1
    assert summary.expected_value == Decimal("32.5")
    assert summary.qualifying_losses_incurred == Decimal("0")
    assert summary.rewards_received == Decimal("0")
    assert summary.net_profit == Decimal("0")


def test_summarize_qualified_counts_expected_qualifying_loss():
    summary = summarize([offer(status=OfferStatus.QUALIFIED.value)])
    assert summary.active_offers == 1
    assert summary.qualifying_losses_incurred == Decimal("5.0")


def test_summarize_reward_received():
    summary = summarize([offer(status=OfferStatus.REWARD_RECEIVED.value)])
    assert summary.rewards_received == Decimal("50.0")
    assert summary.converted_profit == Decimal("37.5")
    assert summary.net_profit == Decimal("32.5")


def test_summarize_completed_not_active():
    summary = summarize([offer(status=OfferStatus.COMPLETED.value)])
    assert summary.active_offers == 0
    assert summary.net_profit == Decimal("32.5")


def test_linked_bet_overrides_expected_qualifying_loss():
    bet = {"profit_if_back": -4.0, "profit_if_lay": -4.0}
    summary = summarize(
        [offer(status=OfferStatus.QUALIFIED.value, bet_id=1)],
        bets_by_id={1: bet},
    )
    assert summary.qualifying_losses_incurred == Decimal("4.0")
