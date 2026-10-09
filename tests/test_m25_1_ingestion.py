from decimal import Decimal

from promotions import (
    IngestionStatus,
    PageFetch,
    html_to_contents,
    ingest_promotion,
    looks_like_url,
    sportsbook_from_text,
    sportsbook_from_url,
    terms_links,
)


def _fetch(url, *, ok=True, final_url=None, text="", links=(), login_gated=False, error=None, status=200):
    def fetcher(_url):
        return PageFetch(
            url=url, final_url=final_url or url, status_code=status, ok=ok,
            text=text, links=tuple(links), login_gated=login_gated, error=error,
        )

    return fetcher


# --- URL / sportsbook recognition -------------------------------------------

def test_looks_like_url():
    assert looks_like_url("https://www.pointsbet.ca/promo")
    assert looks_like_url("pointsbet.ca/promo")
    assert not looks_like_url("Bet $10, get a $20 free bet.")
    assert not looks_like_url("pointsbet.ca / promo")
    assert not looks_like_url("")


def test_sportsbook_from_url_domain_mapping():
    assert sportsbook_from_url("https://www.pointsbet.ca/promo") == "PointsBet Ontario"
    assert sportsbook_from_url("https://promo.betmgm.ca/x") == "BetMGM Ontario"
    assert sportsbook_from_url("https://bet365.com/offer") == "Bet365"
    assert sportsbook_from_url("https://example.com/x") is None


def test_sportsbook_from_text():
    assert sportsbook_from_text("PointsBet Ontario: bet $10 get $20") == "PointsBet Ontario"
    assert sportsbook_from_text("no book here") is None


# --- HTML parsing ------------------------------------------------------------

def test_html_to_contents_extracts_text_links_and_password():
    html = (
        "<html><head><style>.x{}</style></head><body>"
        "<h1>Bet $10</h1><script>track()</script>"
        "<a href='/terms'>Terms and Conditions</a>"
        "<input type='password' name='pw'>"
        "</body></html>"
    )
    text, links, has_password = html_to_contents(html)
    assert "Bet $10" in text
    assert "track" not in text
    assert has_password is True
    assert ("/terms", "Terms and Conditions") in links


def test_terms_links_same_domain_only_and_capped():
    links = (
        ("https://evil.example/terms", "terms"),
        ("/terms", "Terms and Conditions"),
        ("/terms", "Terms and Conditions"),  # duplicate
        ("/t-and-c", "T&C"),
        ("/rules", "Rules"),
    )
    found = terms_links(links, "https://pointsbet.ca/promo", limit=2)
    assert found == ("https://pointsbet.ca/terms", "https://pointsbet.ca/t-and-c")


# --- pasted text -------------------------------------------------------------

def test_ingest_pasted_text_parses_without_inventing():
    result = ingest_promotion(
        "Bet $10, get a $20 free bet. Minimum odds 1.50. Stake not returned. Ontario only."
    )
    assert result.status == IngestionStatus.TEXT.value
    assert result.terms.qualifying_stake == Decimal("10")
    assert result.terms.reward_amount == Decimal("20")
    assert result.terms.qualifying_min_odds == Decimal("1.50")
    assert result.terms.stake_returned is False
    assert result.missing_terms == ()
    assert result.source_url is None


# --- URL extraction ----------------------------------------------------------

def test_ingest_url_extracts_landing_and_terms_pages():
    landing = "Bet $10, get a $20 free bet. <a href='/terms'>Terms and Conditions</a>"
    t_and_c = "Minimum odds 1.50. Stake not returned. Ontario only."

    def fetcher(url):
        if url.endswith("/terms"):
            return PageFetch(url=url, final_url=url, status_code=200, ok=True, text=t_and_c)
        return PageFetch(
            url=url, final_url="https://www.pointsbet.ca/promo",
            status_code=200, ok=True, text=landing,
            links=(("/terms", "Terms and Conditions"),),
        )

    result = ingest_promotion("pointsbet.ca/promo", fetcher=fetcher)
    assert result.status == IngestionStatus.EXTRACTED.value
    assert result.sportsbook == "PointsBet Ontario"
    assert result.sportsbook_source == "domain"
    assert result.terms.qualifying_min_odds == Decimal("1.50")
    assert result.terms.stake_returned is False
    assert result.missing_terms == ()
    assert len(result.pages) == 2


def test_ingest_url_uses_redirect_final_domain():
    def fetcher(url):
        return PageFetch(
            url=url, final_url="https://www.betmgm.ca/offer", status_code=200, ok=True,
            text="Bet $10, get a $20 free bet. Minimum odds 1.50. Stake not returned.",
        )

    result = ingest_promotion("https://short.link/abc", fetcher=fetcher)
    assert result.sportsbook == "BetMGM Ontario"
    assert result.final_url == "https://www.betmgm.ca/offer"


def test_ingest_login_gated_is_partial_and_never_invents():
    def fetcher(url):
        return PageFetch(
            url=url, final_url=url, status_code=200, ok=True, login_gated=True,
            text="Sign in to view your offer",
        )

    result = ingest_promotion("https://www.pointsbet.ca/join", fetcher=fetcher)
    assert result.status == IngestionStatus.PARTIAL.value
    assert result.login_gated is True
    assert result.terms.qualifying_stake is None
    assert result.terms.reward_amount is None
    assert result.missing_terms
    assert any("sign-in" in m.lower() or "sign in" in m.lower() for m in result.messages)


def test_ingest_unreachable_never_invents_conditions():
    def fetcher(url):
        return PageFetch(url=url, final_url=url, status_code=None, ok=False, error="connection refused")

    result = ingest_promotion("https://www.pointsbet.ca/promo", fetcher=fetcher)
    assert result.status == IngestionStatus.UNREACHABLE.value
    assert result.terms.qualifying_stake is None
    assert result.terms.reward_amount is None
    assert result.missing_terms
    assert any("no offer conditions were assumed" in m.lower() for m in result.messages)


def test_sportsbook_override_wins():
    def fetcher(url):
        return PageFetch(
            url=url, final_url="https://unknown.example/x", status_code=200, ok=True,
            text="Bet $10, get a $20 free bet. Minimum odds 1.50. Stake not returned.",
        )

    result = ingest_promotion(
        "unknown.example/x", fetcher=fetcher, sportsbook_override="PointsBet Ontario"
    )
    assert result.sportsbook == "PointsBet Ontario"
    assert result.sportsbook_source == "override"


def test_ingest_empty_input():
    result = ingest_promotion("   ")
    assert result.status == IngestionStatus.INVALID_URL.value
    assert result.missing_terms  # CRITICAL_FIELDS reported
    assert result.terms.qualifying_stake is None
