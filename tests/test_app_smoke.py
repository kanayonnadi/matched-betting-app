from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parent.parent / "app.py"


def test_app_runs_without_exceptions():
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()
    assert not app.exception
    assert not app.error


def test_opportunities_page_renders_mock_table():
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()
    app.sidebar.radio[0].set_value("Opportunities").run()
    assert not app.exception
    assert not app.error
    assert len(app.dataframe) == 1
    table = app.dataframe[0].value
    assert table.shape[0] == 9
    assert "QL%" in table.columns
    losses = [float(value.rstrip("%")) for value in table["QL%"]]
    assert losses == sorted(losses)


def test_offer_planner_page_renders():
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()
    app.sidebar.radio[0].set_value("Offer Planner").run()
    assert not app.exception
    assert not app.error


def test_promotions_page_renders():
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()
    app.sidebar.radio[0].set_value("Promotions").run()
    assert not app.exception
    assert not app.error


def test_settlement_page_renders():
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()
    app.sidebar.radio[0].set_value("Settlement").run()
    assert not app.exception
    assert not app.error


def test_risk_page_renders():
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()
    app.sidebar.radio[0].set_value("Risk").run()
    assert not app.exception
    assert not app.error
