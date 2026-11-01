"""Smoke tests: every app screen renders, empty and with picks, without errors."""

import json
from pathlib import Path

import pytest
import yaml
from streamlit.testing.v1 import AppTest

from swingtrading import db
from swingtrading.config import DEFAULT_SETTINGS_PATH, PROJECT_ROOT, load_settings
from swingtrading.prices import save_bars
from swingtrading.ui import charts, data, settings_form
from tests.fixtures.synthetic import setup_frame

PAGES = sorted((PROJECT_ROOT / "app" / "pages").glob("*.py"))


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    raw = yaml.safe_load(DEFAULT_SETTINGS_PATH.read_text())
    raw["paths"] = {k: str(tmp_path / Path(v).name) for k, v in raw["paths"].items()}
    path = tmp_path / "settings.yaml"
    path.write_text(DEFAULT_SETTINGS_PATH.read_text())  # keep the comments
    from swingtrading.config import Settings, save_settings

    save_settings(Settings.model_validate(raw), path)
    monkeypatch.setenv("SWING_SETTINGS", str(path))
    return path


def _seed(settings_file):
    s = load_settings(settings_file)
    conn = db.connect(s.resolve(s.paths.db_path))
    bars = setup_frame().assign(ticker="AAA")
    save_bars(conn, bars)
    save_bars(conn, bars.assign(ticker="BBB"))
    d = [x.isoformat() for x in bars["date"]]
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO picks (ticker, status, signal_date, signal_close, signal_atr,"
            " sctr_at_signal, setup_score, setup_details, entry_date, entry_price,"
            " initial_stop, current_stop, highest_close)"
            " VALUES ('AAA', 'open', ?, 100, 2, 95, 70, ?, ?, 101, 96, 97, 110)",
            (d[-10], json.dumps({"depth_pct": 5.0}), d[-9]),
        )
        conn.execute(
            "INSERT INTO picks (ticker, status, signal_date, signal_close, signal_atr,"
            " sctr_at_signal, setup_score, entry_date, entry_price, initial_stop,"
            " current_stop, highest_close, exit_signal_date, exit_date, exit_price,"
            " return_pct, days_held)"
            " VALUES ('BBB', 'closed', ?, 100, 2, 90, 60, ?, 101, 96, 98, 105, ?, ?, 99,"
            " -0.0198, 6)",
            (d[-20], d[-19], d[-14], d[-13]),
        )
        conn.executemany(
            "INSERT INTO stop_history (pick_id, date, close, stop) VALUES (?, ?, ?, ?)",
            [(1, d[-9 + i], 100 + i, 96 + i * 0.1) for i in range(9)],
        )
        conn.execute(
            "INSERT INTO runs (session_date, started_at, finished_at, status, new_picks)"
            " VALUES (?, 'x', 'y', 'success', 1)",
            (d[-1],),
        )
    conn.close()


def _run(page: Path) -> AppTest:
    at = AppTest.from_file(str(page), default_timeout=30)
    at.run()
    return at


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.stem)
def test_page_renders_empty(settings_file, page):
    at = _run(page)
    assert not at.exception, at.exception
    if "Picks" in page.stem or "Performance" in page.stem:
        assert at.info  # an empty state that says what to do


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.stem)
def test_page_renders_with_picks(settings_file, page):
    _seed(settings_file)
    at = _run(page)
    assert not at.exception, at.exception


def test_settings_form_validates_and_saves(settings_file):
    page = PROJECT_ROOT / "app" / "pages" / "4_About_and_Settings.py"
    at = _run(page)
    at.number_input(key="stops.atr_mult").set_value(3.0)
    at.button[-1].click().run()  # Save settings
    assert not at.exception and at.success
    assert load_settings(settings_file).stops.atr_mult == 3.0
    assert "# SwingTrading settings" in settings_file.read_text()  # comments survive

    at.number_input(key="setup.pullback_min_pct").set_value(50.0)  # above the max of 12
    at.button[-1].click().run()
    assert at.error and load_settings(settings_file).setup.pullback_min_pct == 3.0


def test_current_picks_numbers(settings_file):
    _seed(settings_file)
    s, conn = data.open_db(load_settings(settings_file))
    row = data.current_picks(conn).iloc[0]
    last = conn.execute("SELECT close FROM prices WHERE ticker='AAA' ORDER BY date DESC").fetchone()
    assert row["current_price"] == pytest.approx(last[0])
    assert row["open_pl_pct"] == pytest.approx((last[0] / 101 - 1) * 100)
    assert row["distance_to_stop_pct"] == pytest.approx((last[0] / 97 - 1) * 100)
    assert row["days_held"] == 8
    past = data.past_picks(conn).iloc[0]
    assert past["return_pct"] == pytest.approx(-1.98)
    conn.close()


def test_pick_chart_has_stepped_dashed_teal_stop(settings_file):
    _seed(settings_file)
    s, conn = data.open_db(load_settings(settings_file))
    pick = data.picks(conn)[0]
    fig = charts.pick_chart(*_chart_args(conn, pick))
    stop = next(t for t in fig.data if t.name == "Stop")
    assert stop.line.shape == "hv" and stop.line.dash == "dash" and stop.line.color == charts.TEAL
    assert next(t for t in fig.data if t.name == "Close").line.color == charts.SLATE
    assert {t.name for t in fig.data} >= {"Entry", "Signal"}
    conn.close()


def _chart_args(conn, pick):
    bars, history = data.pick_chart_data(conn, pick)
    return bars, pick, history


def test_settings_form_helpers():
    assert settings_form.label("min_market_cap") == "Min market cap"
    assert settings_form.label("pullback_min_atr") == "Pullback min ATR"
    s = load_settings()
    new = settings_form.apply(s, {("stops", "atr_mult"): 3.0})
    assert new.stops.atr_mult == 3.0 and s.stops.atr_mult == 2.5
    assert (("ranking", "weights", "rsi"), 5) in settings_form.fields(s)


def test_performance_data_from_the_database(settings_file):
    _seed(settings_file)
    s, conn = data.open_db(load_settings(settings_file))
    save_bars(conn, setup_frame().assign(ticker="SPY"))
    perf = data.performance_data(conn)
    assert perf.stats.trades == 1 and perf.stats.expectancy == pytest.approx(-0.0198)
    assert perf.equity.iloc[0] == 1.0 and list(perf.benchmark.index) == list(perf.equity.index)
    assert perf.max_drawdown <= 0
    assert list(perf.monthly.columns) == ["month", "picks_pct", "spy_pct"]
    fig = charts.equity_chart(perf.equity, perf.benchmark)
    assert [t.name for t in fig.data] == ["Picks", "SPY"]
    conn.close()
