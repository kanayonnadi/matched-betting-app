"""Promotion URL ingestion (M25.1).

Accept either a promotion URL or pasted text, recognise the sportsbook from the
domain, fetch the landing page (following redirects where permitted), pull terms
from the page and any linked terms-and-conditions pages, and report exactly what
could and could not be extracted.

This module **never invents offer conditions**. When a page cannot be fetched, is
login-gated, or does not state a term, the field stays ``None`` and is reported
for confirmation. Partial/ambiguous results are surfaced for the user to confirm
before any search runs.
"""

import re
from dataclasses import dataclass
from enum import Enum
from html import unescape
from html.parser import HTMLParser
from typing import Callable, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

from matching.normalize import normalize_name

from .models import CRITICAL_FIELDS, PromotionTerms
from .parser import parse_terms
from .validator import validate_terms

USER_AGENT = (
    "Mozilla/5.0 (compatible; MatchedBettingDesk/1.0; +https://localhost) "
    "personal promotion reader"
)

# Registrable domain suffix -> canonical sportsbook display name.
DOMAIN_BOOKS = {
    "pointsbet.ca": "PointsBet Ontario",
    "pointsbet.com": "PointsBet Ontario",
    "betmgm.ca": "BetMGM Ontario",
    "betmgm.com": "BetMGM Ontario",
    "bet365.com": "Bet365",
    "bet99.com": "Bet99",
    "betano.ca": "Betano",
    "betano.com": "Betano",
    "betrivers.ca": "BetRivers Ontario",
    "betrivers.com": "BetRivers Ontario",
    "playnow.com": "PlayNow",
    "proline.ca": "Proline",
    "olg.ca": "OLG Proline",
    "sportsinteraction.ca": "Sports Interaction",
    "sportsinteraction.com": "Sports Interaction",
    "fanduel.com": "FanDuel",
    "draftkings.com": "DraftKings",
    "caesars.com": "Caesars",
    "thescorebet.com": "theScore Bet",
}

# Normalized text token -> sportsbook display name (fallback when no URL domain).
TEXT_BOOKS = (
    ("pointsbet", "PointsBet Ontario"),
    ("betmgm", "BetMGM Ontario"),
    ("bet365", "Bet365"),
    ("bet99", "Bet99"),
    ("betano", "Betano"),
    ("betrivers", "BetRivers Ontario"),
    ("playnow", "PlayNow"),
    ("proline", "Proline"),
    ("olg", "OLG Proline"),
    ("sports interaction", "Sports Interaction"),
    ("fanduel", "FanDuel"),
    ("draftkings", "DraftKings"),
    ("caesars", "Caesars"),
    ("thescore bet", "theScore Bet"),
)

_TERMS_HINTS = (
    "terms", "t&c", "t and c", "conditions", "rules", "legal",
    "promotion terms", "offer terms", "full terms",
)

_URL_RE = re.compile(r"^(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:/[^\s]*)?$", re.IGNORECASE)


class IngestionStatus(str, Enum):
    TEXT = "TEXT"
    EXTRACTED = "EXTRACTED"
    PARTIAL = "PARTIAL"
    UNREACHABLE = "UNREACHABLE"
    INVALID_URL = "INVALID_URL"


@dataclass(frozen=True)
class PageFetch:
    url: str
    final_url: str
    status_code: Optional[int]
    ok: bool
    html: str = ""
    text: str = ""
    links: Tuple[Tuple[str, str], ...] = ()
    login_gated: bool = False
    error: Optional[str] = None


@dataclass(frozen=True)
class IngestionResult:
    status: str
    source_url: Optional[str]
    final_url: Optional[str]
    sportsbook: Optional[str]
    sportsbook_source: Optional[str]
    terms: PromotionTerms
    text: str
    pages: Tuple[PageFetch, ...]
    missing_terms: Tuple[str, ...]
    ambiguous_terms: Tuple[str, ...]
    login_gated: bool
    messages: Tuple[str, ...] = ()


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.links = []
        self.has_password = False
        self._skip = 0
        self._in_anchor = False
        self._href = None
        self._anchor_text = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        if tag == "input" and (attributes.get("type") or "").lower() == "password":
            self.has_password = True
        if tag == "a":
            self._in_anchor = True
            self._href = attributes.get("href")
            self._anchor_text = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip > 0:
            self._skip -= 1
        if tag == "a" and self._in_anchor:
            self.links.append((self._href, " ".join(self._anchor_text).strip()))
            self._in_anchor = False
            self._href = None
            self._anchor_text = []

    def handle_data(self, data):
        if self._skip or not data:
            return
        stripped = data.strip()
        if not stripped:
            return
        self.text_parts.append(stripped)
        if self._in_anchor:
            self._anchor_text.append(stripped)


def looks_like_url(value: str) -> bool:
    value = (value or "").strip()
    if not value or " " in value or "\n" in value:
        return False
    return bool(_URL_RE.match(value))


def normalize_url(value: str) -> str:
    value = (value or "").strip()
    if not value.lower().startswith(("http://", "https://")):
        value = "https://" + value
    return value


def sportsbook_from_url(url: str) -> Optional[str]:
    host = (urlparse(normalize_url(url)).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    for domain, name in DOMAIN_BOOKS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return None


def sportsbook_from_text(text: str) -> Optional[str]:
    normalized = normalize_name(text or "")
    for token, name in TEXT_BOOKS:
        if token in normalized:
            return name
    return None


def html_to_contents(html: str) -> Tuple[str, Tuple[Tuple[str, str], ...], bool]:
    parser = _PageParser()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 - malformed HTML must not crash ingestion
        pass
    text = unescape(" ".join(parser.text_parts))
    text = re.sub(r"\s+", " ", text).strip()
    return text, tuple(parser.links), parser.has_password


def _looks_login_gated(fetch: PageFetch) -> bool:
    if fetch.login_gated:
        return True
    lowered = fetch.text.lower()
    markers = ("sign in", "log in", "login", "create an account", "register")
    return any(marker in lowered for marker in markers) and len(fetch.text) < 1200


def terms_links(
    links: Sequence[Tuple[str, str]], base_url: str, limit: int = 2
) -> Tuple[str, ...]:
    base_host = (urlparse(base_url).hostname or "").lower()
    found = []
    for href, anchor in links:
        if not href:
            continue
        haystack = f"{href} {anchor}".lower()
        if not any(hint in haystack for hint in _TERMS_HINTS):
            continue
        absolute = urljoin(base_url, href)
        host = (urlparse(absolute).hostname or "").lower()
        if host and base_host and not (host == base_host or host.endswith("." + base_host)):
            continue  # stay on the operator's own domain
        if absolute in found:
            continue
        found.append(absolute)
        if len(found) >= limit:
            break
    return tuple(found)


def requests_fetcher(url: str, timeout: int = 15) -> PageFetch:
    import requests

    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=timeout, allow_redirects=True
        )
    except requests.RequestException as exc:
        return PageFetch(url=url, final_url=url, status_code=None, ok=False, error=str(exc))
    text, links, has_password = html_to_contents(response.text)
    return PageFetch(
        url=url,
        final_url=response.url,
        status_code=response.status_code,
        ok=response.ok,
        html=response.text,
        text=text,
        links=links,
        login_gated=has_password,
        error=None if response.ok else f"HTTP {response.status_code}",
    )


def _empty_terms() -> PromotionTerms:
    return PromotionTerms()


def ingest_promotion(
    value: str,
    fetcher: Optional[Callable[[str], PageFetch]] = None,
    *,
    sportsbook_override: Optional[str] = None,
    max_terms_pages: int = 2,
) -> IngestionResult:
    """Ingest a URL or pasted promotion description.

    ``fetcher`` is injectable for offline testing. Pasted text is parsed directly;
    URLs are fetched and their terms extracted. Missing fields are reported, never
    guessed.
    """
    value = (value or "").strip()
    if not value:
        return IngestionResult(
            status=IngestionStatus.INVALID_URL.value, source_url=None, final_url=None,
            sportsbook=sportsbook_override, sportsbook_source="override" if sportsbook_override else None,
            terms=_empty_terms(), text="", pages=(),
            missing_terms=CRITICAL_FIELDS, ambiguous_terms=(),
            login_gated=False, messages=("Nothing was entered.",),
        )

    if not looks_like_url(value):
        terms = parse_terms(value)
        _, issues = validate_terms(terms)
        missing = tuple(f for f in CRITICAL_FIELDS if getattr(terms, f) is None)
        sportsbook = sportsbook_override or sportsbook_from_text(value)
        return IngestionResult(
            status=IngestionStatus.TEXT.value, source_url=None, final_url=None,
            sportsbook=sportsbook,
            sportsbook_source="override" if sportsbook_override else ("text" if sportsbook else None),
            terms=terms, text=value, pages=(),
            missing_terms=missing, ambiguous_terms=issues, login_gated=False,
            messages=(
                "Pasted text parsed. Confirm every term before searching.",
            ) if not missing else (
                "Pasted text parsed but some critical terms are missing; confirm them.",
            ),
        )

    url = normalize_url(value)
    fetch = (fetcher or requests_fetcher)(url)
    sportsbook = sportsbook_override or sportsbook_from_url(fetch.final_url) or sportsbook_from_url(url)
    sportsbook_source = (
        "override" if sportsbook_override else ("domain" if sportsbook else None)
    )

    if not fetch.ok:
        return IngestionResult(
            status=IngestionStatus.UNREACHABLE.value, source_url=url,
            final_url=fetch.final_url, sportsbook=sportsbook,
            sportsbook_source=sportsbook_source, terms=_empty_terms(), text="",
            pages=(fetch,), missing_terms=CRITICAL_FIELDS, ambiguous_terms=(),
            login_gated=False,
            messages=(
                f"Could not access {url}"
                + (f" ({fetch.error})" if fetch.error else "")
                + ". No offer conditions were assumed. Paste the full promotion "
                "description instead.",
            ),
        )

    pages = [fetch]
    combined = fetch.text
    for link in terms_links(fetch.links, fetch.final_url, limit=max_terms_pages):
        extra = (fetcher or requests_fetcher)(link)
        pages.append(extra)
        if extra.ok and extra.text:
            combined += " " + extra.text

    terms = parse_terms(combined)
    _, issues = validate_terms(terms)
    missing = tuple(f for f in CRITICAL_FIELDS if getattr(terms, f) is None)
    login_gated = _looks_login_gated(fetch)

    messages = []
    if login_gated:
        messages.append(
            "This page appears to require sign-in and does not publish the full "
            "offer terms. Paste the promotion description or terms text instead."
        )
    if missing:
        messages.append("Missing critical terms: " + ", ".join(missing) + ".")
    if not any(p.url != fetch.final_url and p.ok and p.text for p in pages[1:]):
        messages.append("No linked terms-and-conditions page was accessible.")

    status = (
        IngestionStatus.PARTIAL.value
        if (login_gated or missing)
        else IngestionStatus.EXTRACTED.value
    )
    return IngestionResult(
        status=status, source_url=url, final_url=fetch.final_url,
        sportsbook=sportsbook, sportsbook_source=sportsbook_source,
        terms=terms, text=combined, pages=tuple(pages),
        missing_terms=missing, ambiguous_terms=issues, login_gated=login_gated,
        messages=tuple(messages),
    )
