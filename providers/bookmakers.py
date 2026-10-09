"""Ontario sportsbook -> The Odds API bookmaker mapping.

A promotion from Bet365 must only ever use Bet365 odds. Never substitute another
bookmaker. Unknown or non-Ontario books resolve to ``supported = False`` with a
reason, so discovery can return an explicit "unsupported bookmaker" result.
"""

from dataclasses import dataclass
from typing import Optional

from matching.normalize import normalize_name

# The Odds API bookmaker keys available in the ``ca`` region.
CA_BOOKMAKER_KEYS = (
    "bet99_ca_on",
    "betano_ca_on",
    "betmgm_ca_on",
    "betrivers_ca_on",
    "playnow_ca",
    "pointsbetca",
    "proline_ca_on",
    "sportsinteraction_ca_on",
)

# Display name / alias (normalized) -> bookmaker key.
ALIASES = {
    "bet99": "bet99_ca_on",
    "bet99 ontario": "bet99_ca_on",
    "betano": "betano_ca_on",
    "betano ca": "betano_ca_on",
    "betmgm": "betmgm_ca_on",
    "betmgm ontario": "betmgm_ca_on",
    "betmgm ca": "betmgm_ca_on",
    "betrivers": "betrivers_ca_on",
    "betrivers ontario": "betrivers_ca_on",
    "playnow": "playnow_ca",
    "playnow ca": "playnow_ca",
    "pointsbet": "pointsbetca",
    "pointsbet ca": "pointsbetca",
    "pointsbet ontario": "pointsbetca",
    "proline": "proline_ca_on",
    "proline plus": "proline_ca_on",
    "olg": "proline_ca_on",
    "olg proline": "proline_ca_on",
    "sports interaction": "sportsinteraction_ca_on",
    "sportsinteraction": "sportsinteraction_ca_on",
    "sports interaction ontario": "sportsinteraction_ca_on",
    # Known sportsbooks that are not currently in the CA feed.
    "bet365": "bet365",
    "fanduel": "fanduel",
    "draftkings": "draftkings",
    "caesars": "williamhill_us",
    "thescore bet": "espnbet",
}


@dataclass(frozen=True)
class BookmakerResolution:
    input_name: str
    key: Optional[str]
    supported: bool
    reason: str = ""


def resolve_bookmaker(name) -> BookmakerResolution:
    if not name or not str(name).strip():
        return BookmakerResolution(str(name or ""), None, False, "no sportsbook identified")
    normalized = normalize_name(name)
    key = ALIASES.get(normalized)
    if key is None:
        return BookmakerResolution(str(name), None, False, f"unknown sportsbook '{name}'")
    if key not in CA_BOOKMAKER_KEYS:
        return BookmakerResolution(
            str(name), key, False,
            f"'{name}' is not currently available in The Odds API Canadian feed",
        )
    return BookmakerResolution(str(name), key, True, "")
