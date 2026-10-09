"""M25.2 UI regression tests (Streamlit AppTest).

These execute the real app and drive the Find a Bet workflow. The DB layer is
stubbed so the tests are hermetic and never touch the user's database or the
network.
"""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

import ui.promotion_finder as pf
from opportunities import BetKind, discover_mock_opportunities
from promotions import (
    EligibilityStatus,
    IngestionResult,
    IngestionStatus,
    Promotion,
    parse_terms,
)
from services.discovery import DiscoveryResult, OpportunityStatus
from services.profitability import evaluate_profitability
from services.promotion_workflow import _recommendation

APP = Path(__file__).resolve().parent.parent / "app.py"

ZERO = Decimal("0")


def _promotion(**overrides):
    base = dict(
        id=1, sportsbook="Mock Book", name="offer", jurisdiction="Ontario",
        offer_type="BET_GET", new_customer_only=True,
        qualifying_stake=Decimal("10"), qualifying_min_odds=Decimal("1.5"),
        qualifying_max_odds=None,
        reward_amount=Decimal("20"), reward_type="FREE_BET_SNR", reward_count=1,
        stake_returned=False, status="TERMS_VERIFIED",
        lifecycle_status="TERMS_VERIFIED", eligibility=EligibilityStatus.ELIGIBLE.value,
        confidence=0.9,
    )
    base.update(overrides)
    return Promotion(**base)


def _row(promotion):
    return {
        "id": promotion.id,
        "sportsbook": promotion.sportsbook,
        "name": promotion.name,
        "jurisdiction": promotion.jurisdiction,
        "offer_type": promotion.offer_type,
        "new_customer_only": promotion.new_customer_only,
        "qualifying_stake": promotion.qualifying_stake,
        "qualifying_min_odds": promotion.qualifying_min_odds,
        "qualifying_max_odds": promotion.qualifying_max_odds,
        "reward_amount": promotion.reward_amount,
        "reward_type": promotion.reward_type,
        "reward_count": promotion.reward_count,
        "stake_returned": promotion.stake_returned,
        "reward_expiry_days": promotion.reward_expiry_days,
        "source_url": None,
        "terms_text": None,
        "status": promotion.status,
        "confidence": promotion.confidence,
        "eligibility": promotion.eligibility,
    }


def _discovery(recommendations, mode="DEMO"):
    return DiscoveryResult(
        tuple(recommendations), (), OpportunityStatus.SIMULATED.value, "OK", mode
    )


class Harness:
    def __init__(self, promotion, *, with_conversion=True):
        self.promotion = promotion
        self.saves = []
        self.ingestion = None
        q = _recommendation(replace(discover_mock_opportunities(10)[0], fully_hedged=True))
        c = None
        if with_conversion:
            c = _recommendation(
                replace(
                    discover_mock_opportunities(20, kind=BetKind.FREE_BET_SNR)[0],
                    fully_hedged=True,
                )
            )
        self.q_rec, self.c_rec = q, c

    def evaluation(self, promotion):
        return evaluate_profitability(promotion, self.q_rec, self.c_rec)

    def install(self, monkeypatch):
        monkeypatch.setattr(pf, "list_promotions", lambda *a, **k: [_row(self.promotion)])
        monkeypatch.setattr(pf, "get_promotion", lambda pid, *a, **k: _row(self.promotion))
        monkeypatch.setattr(pf, "sync_workflow", lambda *a, **k: "DRAFT")
        monkeypatch.setattr(pf, "list_reward_tokens", lambda *a, **k: [])
        monkeypatch.setattr(pf, "issue_reward_tokens", lambda *a, **k: [1])
        monkeypatch.setattr(pf, "_bankroll_available", lambda: None)
        monkeypatch.setattr(
            pf,
            "calculate_net_profit",
            lambda *a, **k: {
                "qualifying_loss": ZERO, "pending_conversion": ZERO,
                "realized_net": ZERO, "outstanding_liability": ZERO,
            },
        )

        def save_promotion(terms, book, name, **kwargs):
            self.saves.append({"terms": terms, "book": book, "name": name, **kwargs})
            return self.promotion.id

        monkeypatch.setattr(pf, "save_promotion", save_promotion)

        def fake_discover(promotion, mode="LIVE", **kwargs):
            evaluation = self.evaluation(promotion)
            return (
                evaluation,
                _discovery((self.q_rec,), mode),
                _discovery((self.c_rec,) if self.c_rec else (), mode),
            )

        monkeypatch.setattr(pf, "discover_profitability", fake_discover)

        if self.ingestion is not None:
            monkeypatch.setattr(
                pf, "ingest_promotion", lambda value, **k: self.ingestion
            )


def _run_app():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(APP), default_timeout=90)
    app.run()
    return app


def _click(app, key):
    next(b for b in app.button if b.key == key).click()
    return app.run()


def _mode_radio(app):
    return next(r for r in app.radio if r.options == ["DEMO", "LIVE"])


# --- core regression ---------------------------------------------------------

def test_evaluate_button_does_not_crash_and_renders(monkeypatch):
    harness = Harness(_promotion())
    harness.install(monkeypatch)
    app = _run_app()
    app = _click(app, "pf_prof_evaluate")
    assert not app.exception, [str(e) for e in app.exception]
    # Result is stored under a non-widget key.
    assert app.session_state["pf_profitability_result"] is not None
    labels = {m.label: m.value for m in app.metric}
    assert labels["Worst-case profit"] != "—"
    assert labels["Best-case profit"] != "—"


def test_repeated_evaluate_clicks_do_not_crash(monkeypatch):
    harness = Harness(_promotion())
    harness.install(monkeypatch)
    app = _run_app()
    for _ in range(3):
        app = _click(app, "pf_prof_evaluate")
        assert not app.exception, [str(e) for e in app.exception]


def test_known_reward_amount_shows_projection(monkeypatch):
    harness = Harness(_promotion(reward_amount=Decimal("20")))
    harness.install(monkeypatch)
    app = _run_app()
    app = _click(app, "pf_prof_evaluate")
    labels = {m.label: m.value for m in app.metric}
    assert labels["Worst-case profit"].startswith("$")


def test_unknown_reward_amount_evaluates_without_inventing(monkeypatch):
    harness = Harness(
        _promotion(reward_amount=None, reward_type=None), with_conversion=False
    )
    harness.install(monkeypatch)
    app = _run_app()
    app = _click(app, "pf_prof_evaluate")
    assert not app.exception, [str(e) for e in app.exception]
    labels = {m.label: m.value for m in app.metric}
    assert labels["Worst-case profit"] == "—"


def test_changing_promotion_invalidates_result(monkeypatch):
    harness = Harness(_promotion())
    harness.install(monkeypatch)
    app = _run_app()
    app = _click(app, "pf_prof_evaluate")
    assert app.session_state["pf_profitability_result"] is not None

    # Point the DB stub at a different promotion and re-render.
    harness.promotion = _promotion(id=2, sportsbook="Other Book")
    app.run()
    assert app.session_state.get("pf_profitability_result") is None


def test_switching_mode_invalidates_result(monkeypatch):
    harness = Harness(_promotion())
    harness.install(monkeypatch)
    app = _run_app()
    app = _click(app, "pf_prof_evaluate")
    assert app.session_state["pf_profitability_result"] is not None

    _mode_radio(app).set_value("LIVE")
    app.run()
    assert app.session_state.get("pf_profitability_result") is None


def test_editing_terms_invalidates_result(monkeypatch):
    harness = Harness(_promotion())
    harness.install(monkeypatch)
    app = _run_app()
    app = _click(app, "pf_prof_evaluate")
    assert app.session_state["pf_profitability_result"] is not None

    # Same promotion id, but a term changed (e.g. reward amount edited).
    harness.promotion = _promotion(reward_amount=Decimal("40"))
    app.run()
    assert app.session_state.get("pf_profitability_result") is None


# --- URL ingestion followed by evaluation ------------------------------------

def test_url_ingestion_then_profitability(monkeypatch):
    harness = Harness(_promotion())
    terms = parse_terms(
        "Bet $10, get a $20 free bet. Minimum odds 1.50. Stake not returned. Ontario only."
    )
    harness.ingestion = IngestionResult(
        status=IngestionStatus.EXTRACTED.value,
        source_url="https://www.pointsbet.ca/promo",
        final_url="https://www.pointsbet.ca/promo",
        sportsbook="PointsBet Ontario", sportsbook_source="domain",
        terms=terms, text="...", pages=(),
        missing_terms=(), ambiguous_terms=(), login_gated=False,
        messages=("Pasted text parsed. Confirm every term before searching.",),
    )
    harness.install(monkeypatch)
    app = _run_app()

    app.text_area[0].set_value("https://www.pointsbet.ca/promo")
    app = _click(app, "pf_go")
    assert not app.exception, [str(e) for e in app.exception]
    assert app.session_state["pf_ingest"] is not None

    app = _click(app, "pf_ingest_save")
    assert not app.exception, [str(e) for e in app.exception]
    assert harness.saves and harness.saves[-1]["book"] == "PointsBet Ontario"

    app = _click(app, "pf_prof_evaluate")
    assert not app.exception, [str(e) for e in app.exception]
