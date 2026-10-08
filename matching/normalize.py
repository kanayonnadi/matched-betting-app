"""Name normalization and fuzzy similarity for event matching.

Providers spell the same team many ways ("New York Knicks", "NY Knicks",
"Knicks"). Normalization folds accents, case and punctuation and drops
structural noise tokens; ``name_similarity`` combines exact, subset, nickname
(last-token) and fuzzy signals into a 0..1 score.
"""

import re
import unicodedata
from difflib import SequenceMatcher

NOISE_TOKENS = frozenset({"fc", "afc", "cf", "sc", "club", "the"})

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_WHITESPACE = re.compile(r"\s+")

# Providers use different sport labels for the same sport.
SPORT_ALIASES = {
    "ice hockey": "ice_hockey",
    "ice_hockey": "ice_hockey",
    "hockey": "ice_hockey",
    "american football": "american_football",
    "american_football": "american_football",
    "football": "american_football",
    "association football": "soccer",
    "soccer": "soccer",
    "basketball": "basketball",
    "baseball": "baseball",
    "cricket": "cricket",
    "tennis": "tennis",
    "mixed martial arts": "mma",
    "mma": "mma",
}


def normalize_sport(value) -> str:
    key = normalize_name(value)
    return SPORT_ALIASES.get(key, key)


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(value) -> str:
    if value is None:
        return ""
    text = strip_accents(str(value)).lower()
    text = _NON_ALNUM.sub(" ", text)
    tokens = [t for t in text.split() if t and t not in NOISE_TOKENS]
    return " ".join(tokens)


def name_similarity(left, right) -> float:
    """Return a 0..1 similarity between two free-text names."""
    a = normalize_name(left)
    b = normalize_name(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    tokens_a = set(a.split())
    tokens_b = set(b.split())
    jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    ratio = SequenceMatcher(None, a, b).ratio()

    last_a, last_b = a.split()[-1], b.split()[-1]
    nickname = 1.0 if last_a == last_b else 0.0
    subset = 1.0 if tokens_a <= tokens_b or tokens_b <= tokens_a else 0.0

    return max(0.75 * ratio, 0.90 * nickname, 0.95 * subset, jaccard)
