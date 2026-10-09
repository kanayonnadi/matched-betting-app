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
from typing import Optional, Tuple

from .models import CRITICAL_FIELDS, PromotionTerms, PromotionType, RewardType

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10,
}

_REWARD_NOUN = (
    r"(?:sports\s+bonus\s+bets?|sports\s+bets?|free\s+bets?|bonus\s+bets?|"
    r"bonus\s+credits?|bets?)"
)
# Matches "five $20 free bets", "5 x $20 bonus bets", "a $20 free bet",
# "two $25 bets and one $50 bet".
_DENOM_RE = re.compile(
    r"(?P<count>\d+|one|two|three|four|five|six|seven|eight|nine|ten|a|an)"
    r"\s*(?:x|×|\*)?\s*(?:\$|cad\s*\$?)?(?P<amount>\d+(?:\.\d+)?)\s*"
    + _REWARD_NOUN
)
_EXPIRY_HOURS_RE = re.compile(r"expire[s]?\s*(?:after|within|in)?\s*(\d+)\s*hours?")
_ISSUE_AFTER_RE = re.compile(
    r"(?:issued|credited|awarded|paid|received|delivered|available)"
    r"[^.]{0,40}?(?:after|once|when)[^.]{0,40}?(?:settle|qualifying|wager)"
)


def _parse_reward_denominations(lower: str) -> Tuple[Decimal, ...]:
    """Extract individual reward amounts from "five $20 free bets" style text."""
    amounts = []
    for match in _DENOM_RE.finditer(lower):
        raw_count = match.group("count")
        if raw_count in ("a", "an"):
            count = 1
        else:
            count = _word_or_int(raw_count)
        amount = _decimal(match.group("amount"))
        if count is None or amount is None or amount <= 0:
            continue
        # Guard against capturing the qualifying "$10 bet" (singular without a
        # count word is only accepted as the implied "a $20 free bet" form).
        amounts.extend([amount] * count)
    return tuple(amounts)


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

    denominations = _parse_reward_denominations(lower)
    reward_unit_amount = None
    if denominations and all(d == denominations[0] for d in denominations):
        reward_unit_amount = denominations[0]

    reward_count = len(denominations) if denominations else None
    per_token = reward_unit_amount
    if not denominations:
        # Fallback to the original single-match form ("3 x $10 tokens").
        match = re.search(
            r"(\w+|\d+)\s*\$(\d+(?:\.\d+)?)\s*(?:tokens?|free bets?|bonus bets?|bets?)",
            lower,
        )
        if match:
            reward_count = _word_or_int(match.group(1))
            per_token = _decimal(match.group(2))
        if reward_count and per_token is not None:
            reward_unit_amount = per_token
            denominations = tuple(per_token for _ in range(int(reward_count)))

    stated_total = reward_amount
    if reward_amount is None and denominations:
        reward_amount = sum(denominations, Decimal("0"))
    reward_total_mismatch = (
        stated_total is not None
        and bool(denominations)
        and sum(denominations, Decimal("0")) != stated_total
    )

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

    reward_expiry_hours = None
    match = _EXPIRY_HOURS_RE.search(lower)
    if match:
        reward_expiry_hours = int(match.group(1))

    reward_issue_after_settlement = None
    if _ISSUE_AFTER_RE.search(lower):
        reward_issue_after_settlement = True

    reward_restrictions = []
    for phrase, label in (
        ("cannot be combined", "cannot be combined"),
        ("one per", "one per customer"),
        ("not available on", "restricted markets"),
        ("minimum odds", None),
    ):
        if phrase in lower and label:
            reward_restrictions.append(label)

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
    unknown = [name for name in CRITICAL_FIELDS if values.get(name) is None]
    if reward_total_mismatch:
        unknown.append("reward_total_mismatch")
    if (
        not denominations
        and reward_amount is not None
        and re.search(r"(?:bonus|free)\s+bets?", lower)
    ):
        unknown.append("reward_denomination")
    unknown_fields = tuple(unknown)
    found = len(CRITICAL_FIELDS) - len([n for n in unknown if n in CRITICAL_FIELDS])
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
        reward_unit_amount=reward_unit_amount,
        reward_denominations=denominations,
        reward_expiry_hours=reward_expiry_hours,
        reward_issue_after_settlement=reward_issue_after_settlement,
        reward_restrictions=tuple(reward_restrictions),
        unknown_fields=unknown_fields,
        confidence=confidence,
    )
