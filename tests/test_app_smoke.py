from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parent.parent / "app.py"


def _run_primary(page):
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()
    app.sidebar.radio[0].set_value(page).run()
    return app


def _run_advanced(page):
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()
    app.sidebar.radio[0].set_value("Advanced").run()
    app.sidebar.radio[1].set_value(page).run()
    return app


def test_app_runs_without_exceptions():
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()
    assert not app.exception
    assert not app.error


def test_primary_pages_render():
    for page in ("Find a Bet", "My Bets", "Bankroll"):
        app = _run_primary(page)
        assert not app.exception, page
        assert not app.error, page


def test_opportunities_page_renders_mock_table():
    app = _run_advanced("Opportunities")
    assert not app.exception
    assert not app.error
    assert len(app.dataframe) == 1
    table = app.dataframe[0].value
    assert table.shape[0] == 9
    assert "QL%" in table.columns
    losses = [float(value.rstrip("%")) for value in table["QL%"]]
    assert losses == sorted(losses)


def test_offer_planner_page_renders():
    app = _run_advanced("Offer Planner")
    assert not app.exception
    assert not app.error


def test_promotions_page_renders():
    app = _run_advanced("Promotions")
    assert not app.exception
    assert not app.error


def test_settlement_page_renders():
    app = _run_advanced("Settlement")
    assert not app.exception
    assert not app.error


def test_risk_page_renders():
    app = _run_advanced("Risk")
    assert not app.exception
    assert not app.error


def test_analytics_page_renders():
    app = _run_advanced("Analytics")
    assert not app.exception
    assert not app.error
