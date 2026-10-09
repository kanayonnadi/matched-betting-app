"""Deterministic promotion-terms parser.

Regex/heuristic only — no LLM. Anything not explicit in the text stays ``None``
and is reported in ``unknown_fields``. An LLM may be layered on top later, but
this deterministic pass is the source of truth.

Odds are normalised to decimal: American ``-250`` -> ``1.40``, ``+150`` -> ``2.50``,
and decimal values (``1.40``, ``2.00``) are used as-is (ambiguous ``1.x`` values
are treated as decimal, ``>2`` integers as decimal).
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

from .models import CRITICAL_FIELDS, PromotionTerms, PromotionType, RewardType

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10,
}


def _word_or_int(token: str) -> Optional[int]:
    token = (token or "").strip().lower()
    if token in WORD_NUMBERS:
        return WORD_NUMBERS[token]
    if token.isdigit():
        return int(token)
    return None


def american_to_decimal(value: Decimal) -> Decimal:
    if value > 0:
        return Decimal("1") + value / Decimal("100")
    return Decimal("1") + Decimal("100") / (-value)


def parse_odds(raw: str) -> Optional[Decimal]:
    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError):
        return None
    if value <= -100 or value >= 100:
        return american_to_decimal(value).quantize(Decimal("0.01"))
    if value > Decimal("1"):
        return value
    return None


def _decimal(raw: str) -> Optional[Decimal]:
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError):
        return None


def _detect_offer_type(text: str) -> Optional[str]:
    t = text.lower()
    if re.search(r"deposit\s*(?:match|bonus)|\d+\s*%\s*(?:deposit|match)", t):
        return PromotionType.DEPOSIT_MATCH.value
    if "risk" in t and "free" in t:
        return PromotionType.RISK_FREE.value
    if "profit boost" in t:
        return PromotionType.PROFIT_BOOST.value
    if "odds boost" in t:
        return PromotionType.ODDS_BOOST.value
    if "insurance" in t or "second chance" in t:
        return PromotionType.BET_INSURANCE.value
    if "reload" in t:
        return PromotionType.RELOAD.value
    if re.search(r"bet\s*\$?\s*\d+(?:\.\d+)?\b.*?(?:receive|get|win|earn)", t):
        return PromotionType.BET_GET.value
    if "free bet" in t or "bonus bet" in t:
        return PromotionType.FREE_BET_SNR.value
    return None


def _detect_reward_type(text: str, stake_returned: Optional[bool]) -> Optional[str]:
    t = text.lower()
    if "bonus bet" in t or "free bet" in t:
        if stake_returned is False:
            return RewardType.FREE_BET_SNR.value
        if stake_returned is True:
            return RewardType.FREE_BET_SR.value
        return RewardType.FREE_BET_SNR.value
    if "cash" in t:
        return RewardType.CASH.value
    if "bonus" in t:
        return RewardType.BONUS.value
    return None


def parse_terms(text: str) -> PromotionTerms:
    text = text or ""
    lower = text.lower()

    qualifying_stake = None
    match = re.search(r"\bbet\s*\$?\s*(\d+(?:\.\d+)?)", lower)
    if match:
        qualifying_stake = _decimal(match.group(1))

    reward_amount = None
    match = re.search(
        r"(?:receive|get|win|earn)\s+(?:a\s+|an\s+|up to\s+)?\$?\s*(\d+(?:\.\d+)?)", lower
    )
    if match:
        reward_amount = _decimal(match.group(1))

    reward_count = None
    per_token = None
    match = re.search(
        r"(\w+|\d+)\s*\$(\d+(?:\.\d+)?)\s*(?:tokens?|free bets?|bonus bets?|bets?)",
        lower,
    )
    if match:
        reward_count = _word_or_int(match.group(1))
        per_token = _decimal(match.group(2))
    if reward_amount is None and reward_count and per_token is not None:
        reward_amount = per_token * Decimal(reward_count)

    min_odds = None
    match = re.search(r"(?:minimum|min)\s*odds?\s*[:\s]*([+-]?\d+(?:\.\d+)?)", lower)
    if match:
        min_odds = parse_odds(match.group(1))

    reward_expiry_days = None
    match = re.search(
        r"expire[s]?\s*(?:after|within|in)\s*(\w+|\d+)\s*days", lower
    )
    if match:
        reward_expiry_days = _word_or_int(match.group(1))

    stake_returned = None
    if re.search(r"stake\s+not\s+returned", lower):
        stake_returned = False
    elif re.search(r"stake\s+returned", lower):
        stake_returned = True

    new_customer_only = None
    if "new customer" in lower or "new ontario customer" in lower or "new customers" in lower:
        new_customer_only = True
    elif "existing customer" in lower:
        new_customer_only = False

    jurisdiction = None
    for region in ("Ontario", "Canada", "Alberta", "British Columbia"):
        if region.lower() in lower:
            jurisdiction = region
            break

    offer_type = _detect_offer_type(text)
    reward_type = _detect_reward_type(text, stake_returned)

    values = {
        "offer_type": offer_type,
        "qualifying_stake": qualifying_stake,
        "reward_amount": reward_amount,
        "reward_type": reward_type,
    }
    unknown_fields = tuple(name for name in CRITICAL_FIELDS if values.get(name) is None)
    found = len(CRITICAL_FIELDS) - len(unknown_fields)
    confidence = round(found / len(CRITICAL_FIELDS), 2)

    return PromotionTerms(
        offer_type=offer_type,
        qualifying_stake=qualifying_stake,
        qualifying_min_odds=min_odds,
        reward_amount=reward_amount,
        reward_type=reward_type,
        reward_count=reward_count or 1,
        stake_returned=stake_returned,
        reward_expiry_days=reward_expiry_days,
        new_customer_only=new_customer_only,
        jurisdiction=jurisdiction,
        unknown_fields=unknown_fields,
        confidence=confidence,
    )
