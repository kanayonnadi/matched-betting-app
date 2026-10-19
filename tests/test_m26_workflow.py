from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import ui.promotion_finder as pf
import ui.workflow as wf
from database import init_db
from opportunities import BetKind, discover_mock_opportunities
from promotions import (
    EligibilityStatus,
    IngestionResult,
    IngestionStatus,
    Promotion,
    parse_terms,
    promotion_from_row,
)
from services.discovery import DiscoveryResult, OpportunityStatus
from services.profitability import evaluate_reward_collection_profitability
from services.promotion_workflow import _recommendation

APP = __import__("pathlib").Path(__file__).resolve().parent.parent / "app.py"
ZERO = Decimal("0")
NOW = datetime.now(timezone.utc)

TEXT = (
    "PointsBet Ontario: Bet $10, get $100 in five $20 free bets. Minimum odds 1.50. "
    "Stake not returned. Ontario only."
)
INCOMPLETE_TEXT = "Bet $10, get $100 in bonus bets. Ontario only."


def _row(**overrides):
    base = {
        "id": 1, "sportsbook": "PointsBet Ontario", "name": "promotion",
        "jurisdiction": "Ontario", "offer_type": "BET_GET", "new_customer_only": True,
        "qualifying_stake": Decimal("10"), "qualifying_min_odds": Decimal("1.5"),
        "qualifying_max_odds": None, "reward_amount": Decimal("100"),
        "reward_type": "FREE_BET_SNR", "reward_count": 5, "reward_unit_amount": Decimal("20"),
        "stake_returned": False, "reward_expiry_days": None, "reward_expiry_hours": 168,
        "source_url": None, "terms_text": None, "status": "ELIGIBILITY_CONFIRMED",
        "confidence": 0.9, "eligibility": EligibilityStatus.ELIGIBLE.value,
    }
    base.update(overrides)
    return base


def _qual_rec():
    return _recommendation(replace(discover_mock_opportunities(10)[0], fully_hedged=True))


def _conv_rec():
    return _recommendation(
        replace(discover_mock_opportunities(20, kind=BetKind.FREE_BET_SNR)[0], fully_hedged=True)
    )


def _discovery(recs, mode="DEMO"):
    return DiscoveryResult(tuple(recs), (), OpportunityStatus.SIMULATED.value, "OK", mode)


class Harness:
    def __init__(self, *, row=None, with_qualifying=True, with_eval=True, tokens=None, qual_recs=None):
        self.row = row or _row()
        self.saved = []
        self.ingest_calls = []
        self.with_qualifying = with_qualifying
        self.with_eval = with_eval
        self.tokens = tokens if tokens is not None else []
        self.qual_recs = qual_recs

    def _promotion(self, pid=None):
        return promotion_from_row({**self.row, "id": pid or self.row["id"]})

    def install(self, monkeypatch):
        # --- workflow module ---
        monkeypatch.setattr(wf, "save_promotion", lambda terms, book, name, **k: (self.saved.append((terms, book, name)) or self.row["id"]))
        monkeypatch.setattr(wf, "get_promotion", lambda pid, *a, **k: self.row)
        monkeypatch.setattr(wf, "set_promotion_eligibility", lambda *a, **k: None)
        monkeypatch.setattr(wf, "update_promotion", lambda *a, **k: None)
        monkeypatch.setattr(wf, "record_promotion_action", lambda *a, **k: 1)
        monkeypatch.setattr(wf, "sync_workflow", lambda *a, **k: "DRAFT")
        monkeypatch.setattr(wf, "sync_promotion_lifecycle", lambda *a, **k: "ELIGIBILITY_CONFIRMED")
        monkeypatch.setattr(wf, "list_reward_tokens", lambda *a, **k: list(self.tokens))
        monkeypatch.setattr(wf, "issue_reward_tokens", lambda *a, **k: [1])
        monkeypatch.setattr(wf, "confirm_reward_received", lambda *a, **k: None)
        monkeypatch.setattr(wf, "record_qualifying_placed", lambda *a, **k: 1)
        monkeypatch.setattr(
            wf, "confirm_qualifying_settlement",
            lambda *a, **k: type("R", (), {"status": "OK", "message": "settled"})(),
        )
        if self.qual_recs is not None:
            qual_recs = list(self.qual_recs)
        else:
            qual_recs = [_qual_rec()] if self.with_qualifying else []
        monkeypatch.setattr(wf, "discover_qualifying", lambda promotion, mode, **k: _discovery(list(qual_recs), mode))
        monkeypatch.setattr(wf, "_bankroll_available", lambda: None)

        def fake_eval(promotion, mode, **k):
            rec_q = _qual_rec()
            rec_c = _conv_rec()
            promotion = promotion_from_row(self.row)
            evaluation = evaluate_reward_collection_profitability(
                promotion, rec_q,
                __import__("promotions").collection_from_promotion(promotion),
                {Decimal("20"): [rec_c]}, mode="DEMO",
            )
            return evaluation, _discovery([rec_q], mode), _discovery([rec_c], mode)

        monkeypatch.setattr(wf, "discover_profitability", fake_eval)

        def fake_ingest(value, **k):
            self.ingest_calls.append(value)
            terms = parse_terms(TEXT)
            return IngestionResult(
                status=IngestionStatus.EXTRACTED.value, source_url=value, final_url=value,
                sportsbook="PointsBet Ontario", sportsbook_source="domain", terms=terms,
                text=value, pages=(), missing_terms=(), ambiguous_terms=(),
                login_gated=False, messages=(),
            )

        monkeypatch.setattr(wf, "ingest_promotion", fake_ingest)

        # --- classic module (inert) ---
        monkeypatch.setattr(pf, "list_promotions", lambda *a, **k: [])
        monkeypatch.setattr(pf, "get_promotion", lambda *a, **k: None)
        monkeypatch.setattr(pf, "sync_workflow", lambda *a, **k: "DRAFT")
        monkeypatch.setattr(pf, "list_reward_tokens", lambda *a, **k: [])
        monkeypatch.setattr(pf, "_bankroll_available", lambda: None)
        monkeypatch.setattr(
            pf, "calculate_net_profit",
            lambda *a, **k: {"qualifying_loss": ZERO, "pending_conversion": ZERO,
                             "realized_net": ZERO, "outstanding_liability": ZERO},
        )


def _run(monkeypatch, harness):
    from streamlit.testing.v1 import AppTest

    harness.install(monkeypatch)
    app = AppTest.from_file(str(APP), default_timeout=90)
    app.run()
    return app


def _btn(app, key):
    next(b for b in app.button if b.key == key).click()
    return app.run()


# --- Phase: analysis helpers (unit) -----------------------------------------

def test_analyze_text_extraction():
    terms, book, ingest = wf.analyze_promotion(TEXT)
    assert ingest is None
    assert book.lower().startswith("pointsbet")
    assert terms.reward_count == 5
    assert terms.reward_unit_amount == Decimal("20")


def test_missing_questions_only_ask_what_is_missing():
    complete = parse_terms(TEXT)
    ids = [q[0] for q in wf.missing_questions(complete)]
    assert "min_odds_choice" not in ids
    assert "stake_returned" not in ids
    assert "claimed" in ids

    incomplete = parse_terms(INCOMPLETE_TEXT)
    ids = [q[0] for q in wf.missing_questions(incomplete)]
    assert {"min_odds_choice", "stake_returned", "reward_structure"}.issubset(set(ids))


def test_effective_terms_applies_answers_only():
    terms = parse_terms(INCOMPLETE_TEXT)
    effective, claimed = wf.effective_terms(
        terms,
        {"reward_structure": "One free bet", "stake_returned": "No (stake not returned)",
         "min_odds_choice": "No minimum stated", "claimed": "No, I'm eligible"},
    )
    assert effective.reward_count == 1
    assert effective.stake_returned is False
    assert effective.qualifying_min_odds is None
    assert claimed == "No, I'm eligible"


# --- Phase: stage 1 UI ------------------------------------------------------

def test_terms_text_submission_extracts(monkeypatch):
    app = _run(monkeypatch, Harness())
    next(t for t in app.text_area if t.key == "wf_input").set_value(TEXT)
    app = _btn(app, "wf_analyze")
    assert not app.exception, [str(e) for e in app.exception]
    assert app.session_state["wf_terms"].reward_count == 5


def test_url_submission_uses_ingestion(monkeypatch):
    harness = Harness()
    app = _run(monkeypatch, harness)
    next(t for t in app.text_area if t.key == "wf_input").set_value("https://www.pointsbet.ca/promo")
    app = _btn(app, "wf_analyze")
    assert harness.ingest_calls == ["https://www.pointsbet.ca/promo"]
    assert not app.exception, [str(e) for e in app.exception]


def test_dynamic_questions_render_for_incomplete(monkeypatch):
    app = _run(monkeypatch, Harness())
    next(t for t in app.text_area if t.key == "wf_input").set_value(INCOMPLETE_TEXT)
    app = _btn(app, "wf_analyze")
    keys = {s.key for s in app.selectbox}
    assert "wf_q_min_odds_choice" in keys
    assert "wf_q_stake_returned" in keys
    assert "wf_q_claimed" in keys


def test_manual_override_via_edit_details(monkeypatch):
    app = _run(monkeypatch, Harness())
    next(t for t in app.text_area if t.key == "wf_input").set_value(TEXT)
    app = _btn(app, "wf_analyze")
    next(t for t in app.text_input if t.key == "wf_edit_reward").set_value("50")
    app = _btn(app, "wf_edit_apply")
    assert app.session_state["wf_terms"].reward_amount == Decimal("50")


def test_approve_complete_and_transition(monkeypatch):
    harness = Harness()
    app = _run(monkeypatch, harness)
    next(t for t in app.text_area if t.key == "wf_input").set_value(TEXT)
    app = _btn(app, "wf_analyze")
    app = _btn(app, "wf_approve")
    assert not app.exception, [str(e) for e in app.exception]
    assert harness.saved
    assert app.session_state["wf_stage"] == 2


def test_approve_incomplete_requires_acknowledgement(monkeypatch):
    app = _run(monkeypatch, Harness())
    next(t for t in app.text_area if t.key == "wf_input").set_value(INCOMPLETE_TEXT)
    app = _btn(app, "wf_analyze")
    # Approve without acknowledging the material warnings.
    app = _btn(app, "wf_approve")
    assert app.session_state.get("wf_stage", 1) == 1
    assert app.error
    # Acknowledge and approve.
    next(c for c in app.checkbox if c.key == "wf_ack").check()
    app.run()
    app = _btn(app, "wf_approve")
    assert app.session_state["wf_stage"] == 2


# --- Phase: stage 2 UI ------------------------------------------------------

def _approved_app(monkeypatch, harness):
    app = _run(monkeypatch, harness)
    next(t for t in app.text_area if t.key == "wf_input").set_value(TEXT)
    app = _btn(app, "wf_analyze")
    app = _btn(app, "wf_approve")
    return app


def test_qualifying_search_and_placement_vs_settlement(monkeypatch):
    harness = Harness()
    app = _approved_app(monkeypatch, harness)
    app = _btn(app, "wf_q_search")
    assert not app.exception, [str(e) for e in app.exception]
    assert any(b.key == "wf_q_pick_0" for b in app.button)
    app = _btn(app, "wf_q_pick_0")
    app = _btn(app, "wf_q_confirm")
    assert app.session_state.get("wf_q_bet") == 1
    # Settlement is separate from placement.
    assert any(b.key == "wf_q_settle" for b in app.button)
    app = _btn(app, "wf_q_settle")
    assert not app.exception, [str(e) for e in app.exception]


def test_reruns_persist_stage(monkeypatch):
    harness = Harness()
    app = _approved_app(monkeypatch, harness)
    assert app.session_state["wf_stage"] == 2
    app.run()  # plain rerun, no interaction
    assert app.session_state["wf_stage"] == 2


def test_back_navigation_preserves_progress(monkeypatch):
    harness = Harness()
    app = _approved_app(monkeypatch, harness)
    app = _btn(app, "wf_back")
    assert app.session_state["wf_stage"] == 1
    assert app.session_state["wf_terms"] is not None


# --- Phase: stage 3 UI ------------------------------------------------------

def _rewards_credited_tokens():
    return [
        {"id": i + 1, "token_key": f"promotion:1:reward_{i+1}", "face_value": 20.0,
         "reward_type": "FREE_BET_SNR", "status": "CREDITED", "sequence": i,
         "expires_at": None, "realized_pnl": None, "eligible_markets": "[]"}
        for i in range(5)
    ]


def test_reward_credit_then_continue_and_promo_search(monkeypatch):
    harness = Harness(tokens=_rewards_credited_tokens())
    app = _approved_app(monkeypatch, harness)
    app = _btn(app, "wf_q_search")
    app = _btn(app, "wf_q_pick_0")
    app = _btn(app, "wf_q_confirm")
    app = _btn(app, "wf_q_settle")
    # Reward already credited (stub) -> continue to promo bets.
    app = _btn(app, "wf_q_continue")
    assert app.session_state["wf_stage"] == 3
    app = _btn(app, "wf_c_search")
    assert not app.exception, [str(e) for e in app.exception]
    assert app.session_state.get("wf_c_eval") is not None


def test_selecting_option_greys_out_others_and_reveals_next_step(monkeypatch):
    two_recs = [
        _recommendation(replace(discover_mock_opportunities(10)[0], fully_hedged=True)),
        _recommendation(replace(discover_mock_opportunities(10)[1], fully_hedged=True)),
    ]
    harness = Harness(qual_recs=two_recs)
    app = _approved_app(monkeypatch, harness)
    app = _btn(app, "wf_q_search")
    assert not app.exception, [str(e) for e in app.exception]
    picks = {b.key: b for b in app.button if b.key.startswith("wf_q_pick_")}
    assert set(picks) == {"wf_q_pick_0", "wf_q_pick_1"}
    assert not any(b.disabled for b in picks.values())

    app = _btn(app, "wf_q_pick_0")
    picks = {b.key: b for b in app.button if b.key.startswith("wf_q_pick_")}
    assert picks["wf_q_pick_0"].disabled is True
    assert picks["wf_q_pick_0"].label == "Selected"
    assert picks["wf_q_pick_1"].disabled is True  # other option greyed out
    # The next-step box is revealed with its confirmation action.
    assert any(b.key == "wf_q_confirm" for b in app.button)


def test_recheck_opportunity_refreshes_without_crash(monkeypatch):
    harness = Harness()
    app = _approved_app(monkeypatch, harness)
    app = _btn(app, "wf_q_search")
    assert not app.exception, [str(e) for e in app.exception]
    app = _btn(app, "wf_q_recheck")
    assert not app.exception, [str(e) for e in app.exception]
    assert app.session_state.get("wf_q_result") is not None
