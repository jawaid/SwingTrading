"""Smoke tests: every app screen renders, empty and with picks, without errors."""

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import yaml
from streamlit.testing.v1 import AppTest

from swingtrading import db
from swingtrading.config import DEFAULT_SETTINGS_PATH, PROJECT_ROOT, load_settings
from swingtrading.prices import save_bars
from swingtrading.ui import charts, data, settings_form, tables
from tests.fixtures.synthetic import setup_frame

PAGES = sorted((PROJECT_ROOT / "app" / "screens").glob("*.py"))


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
    page = PROJECT_ROOT / "app" / "screens" / "4_About_and_Settings.py"
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
    assert pd.isna(row["support"])  # the seeded setup has no pullback low
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
    assert next(t for t in fig.data if t.name == "Price").type == "candlestick"
    assert {t.name for t in fig.data} >= {"Entry", "Signal"}
    conn.close()


def _chart_args(conn, pick):
    bars, history = data.pick_chart_data(conn, pick)
    return bars, pick, history


def test_pick_chart_moving_averages_support_and_title(settings_file):
    _seed(settings_file)
    s, conn = data.open_db(load_settings(settings_file))
    pick = {**data.picks(conn)[0], "setup_details": '{"pullback_low": 95.5}'}
    bars, history = data.pick_chart_data(conn, pick, ema_periods=(10, 20))
    plain, _ = data.pick_chart_data(conn, pick)
    assert list(bars["date"]) == list(plain["date"])  # warm-up bars are not shown
    assert bars["ema_10"].notna().all() and bars["ema_20"].notna().all()
    support = data.support_level(pick, bars)
    window = bars[bars["date"] <= date.fromisoformat(pick["signal_date"])]
    assert support == (window.loc[window["low"].idxmin(), "date"], 95.5)
    # Support still in effect (as the pop-up passes it): drawn as the dark Support line.
    fig = charts.pick_chart(bars, pick, history, "Acme Inc · Widgets", support, support)
    names = [t.name for t in fig.data]
    assert "10 EMA" in names and "20 EMA" in names and "Support" in names
    line = next(t for t in fig.data if t.name == "Support")
    assert line.x[0] == support[0] and list(line.y) == [95.5, 95.5]
    assert {a.text for a in fig.layout.annotations} >= {"Support 95.50"}
    assert "Acme Inc · Widgets" in fig.layout.title.text
    conn.close()


def test_stop_line_starts_at_entry_with_the_first_stop():
    pick = {"ticker": "X", "signal_date": "2026-10-30", "entry_date": "2026-11-02",
            "entry_price": 100.0, "initial_stop": 90.0, "exit_date": None}  # fmt: skip
    bars = pd.DataFrame(
        {"date": [date(2026, 10, 30), date(2026, 11, 2)], "open": [99.0, 100.0],
         "high": [101.0, 103.0], "low": [98.0, 99.0], "close": [100.0, 102.0]}
    )  # fmt: skip
    history = pd.DataFrame({"date": [date(2026, 11, 2)], "close": [102.0], "stop": [92.0]})
    fig = charts.pick_chart(bars, pick, history)
    stop = next(t for t in fig.data if t.name == "Stop")
    assert list(stop.y) == [90.0, 92.0, 92.0]  # first stop, raised that evening, held
    assert stop.x[0] == date(2026, 11, 2) and stop.x[-1] > date(2026, 11, 2)
    assert {a.text for a in fig.layout.annotations} == {"Stop 92.00"}


def test_company_name_and_industry(settings_file):
    _seed(settings_file)
    s, conn = data.open_db(load_settings(settings_file))
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO symbols (ticker, name, exchange, industry, quote_type,"
            " fund_category) VALUES (?, ?, 'Q', ?, ?, ?)",
            [("PAYC", "Paycom Software, Inc. Common Stock", "Software - Application", "EQUITY",
              None),
             ("FSLY", "Fastly, Inc. - Class A Common Stock", "Software - Infrastructure",
              "EQUITY", None),
             ("SPY", "SPDR S&P 500 ETF Trust", None, "ETF", "Large Blend")],
        )  # fmt: skip
    assert data.company(conn, "PAYC") == "Paycom Software, Inc. · Software - Application"
    assert data.company(conn, "FSLY") == "Fastly, Inc. · Software - Infrastructure"
    assert data.company(conn, "SPY") == "SPDR S&P 500 ETF Trust · Large Blend"
    assert data.company(conn, "NOPE") == ""
    conn.close()


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


def test_faq_covers_every_current_picks_column(settings_file):
    at = _run(PROJECT_ROOT / "app" / "screens" / "5_FAQ.py")
    assert not at.exception
    titles = {e.label for e in at.expander}
    for column in ["Ticker", "Status", "Signal", "Entered", "Entry", "Price", "Stop", "Support",
                   "To stop", "Open P/L", "SCTR", "Days held", "Setup score", "Exited", "Return",
                   "Profit factor", "Max drawdown", "Run now and Dry run"]:  # fmt: skip
        assert column in titles, column
    assert [t.label for t in at.tabs] == ["The basics", "Logic", "Current picks", "Past picks",
                                          "Performance", "About and settings"]  # fmt: skip
    logic = at.tabs[1].markdown[1].value
    assert "Step 10" in logic and "top 15%" in logic and "2.5 times" in logic
    assert "| 60% |" in logic
    assert "\\$2 billion" in logic and "{" not in logic  # dollars escaped, no unfilled fields


def test_disclaimer_is_shown(settings_file):
    from swingtrading.ui import disclaimer

    assert "educational purposes only" in disclaimer.text()
    assert not disclaimer.text().startswith("#")
    about = _run(PROJECT_ROOT / "app" / "screens" / "4_About_and_Settings.py")
    assert "licensed financial advisor" in about.warning[0].value
    faq = _run(PROJECT_ROOT / "app" / "screens" / "5_FAQ.py")
    assert "Is this financial advice?" in {e.label for e in faq.expander}
    assert faq.warning[0].value == disclaimer.RISK_WARNING
    assert "Stock trading carries a high level of risk" in faq.warning[0].value


@pytest.mark.parametrize("seeded", [False, True], ids=["empty", "with_picks"])
def test_footer_under_every_screen(settings_file, seeded):
    from swingtrading.ui import disclaimer

    if seeded:
        _seed(settings_file)
    at = AppTest.from_file(str(PROJECT_ROOT / "app" / "Home.py"), default_timeout=30)
    at.run()  # the default screen; when empty it ends early with st.stop()
    assert not at.exception
    assert at.main.caption[-1].value == disclaimer.FOOTER
    for page in PAGES:
        at.switch_page(str(page.relative_to(PROJECT_ROOT / "app"))).run()
        assert not at.exception, page
        assert at.main.caption[-1].value == disclaimer.FOOTER, page
        assert [c.value for c in at.main.caption].count(disclaimer.FOOTER) == 1, page


PAYC_DETAILS = {"detector": "pullback_pivot", "swing_high_date": "2026-10-23",
                "swing_high": 275.22, "pullback_low": 255.67, "depth_pct": 7.1,
                "depth_atr": 2.58, "pullback_bars": 4, "down_volume_ratio": 0.82,
                "pivot_volume_ratio": 1.18, "close_in_range": 0.69, "atr": 7.5708}  # fmt: skip


def test_why_selected_explains_each_rule(settings_file):
    s = load_settings(settings_file)
    pick = {"sctr_at_signal": 98.3, "setup_score": 41.8,
            "setup_details": json.dumps(PAYC_DETAILS)}  # fmt: skip
    reasons = data.why_selected(pick, s)
    assert [r.split(":**")[0] for r in reasons] == [
        "**Strong stock", "**Recent high", "**Healthy dip", "**Quiet selling",
        "**Trend held", "**Turned up", "**Setup score 42 / 100",
    ]  # fmt: skip
    text = "\n".join(reasons)
    for part in ["SCTR 98.3", "top 15%", "275.22 on Oct 23", "7.1%", "2.6 times", "255.67",
                 "4 session(s)", "0.82×", "below 1×", "50-day EMA", "69%", "1.18×"]:  # fmt: skip
        assert part in text, part


def test_why_selected_without_down_days_or_details(settings_file):
    s = load_settings(settings_file)
    quiet = {**PAYC_DETAILS, "down_volume_ratio": 0.0}
    reasons = data.why_selected({"setup_details": json.dumps(quiet)}, s)
    assert any("no down days" in r for r in reasons)
    only_rank = data.why_selected({"sctr_at_signal": 90.0, "setup_details": None}, s)
    assert len(only_rank) == 1 and "SCTR 90.0" in only_rank[0]


def test_chart_html_has_copy_button():
    import plotly.graph_objects as go

    html = charts.chart_html(go.Figure(), "PAYC_2026-10-30")
    assert 'id="copy"' in html and "Copy chart" in html and 'id="chart"' in html
    assert "clipboard.write" in html and "downloadImage" in html and "PAYC_2026-10-30" in html


def test_chart_opens_from_a_button(settings_file):
    _seed(settings_file)
    at = _run(PROJECT_ROOT / "app" / "screens" / "1_Current_Picks.py")
    assert not at.exception
    show = next(b for b in at.button if "Show chart" in b.label)
    assert show.disabled  # until a row is ticked


def test_current_picks_support_shows_broken_only_when_levels_exist(settings_file):
    _seed(settings_file)
    s, conn = data.open_db(load_settings(settings_file))
    # The seeded pick has no pullback low in its setup details: empty, not Broken.
    row = data.current_picks(conn).iloc[0]
    assert pd.isna(row["support"]) and not row["support_broken"]
    # PAYC's stored levels (pullback low 255.67) sit far above the seeded ~100 prices, and
    # its swing-high date has no bar here, so the stored values are used: the price is
    # below every level, so support is broken. The number stays for sorting.
    conn.execute("UPDATE picks SET setup_details = ? WHERE ticker = 'AAA'",
                 (json.dumps(PAYC_DETAILS),))  # fmt: skip
    df = data.current_picks(conn)
    assert list(df.columns).index("support") == list(df.columns).index("current_stop") + 1
    assert df.iloc[0]["support_broken"] and df.iloc[0]["support"] == pytest.approx(255.67)
    conn.close()


def test_support_column_text():
    df = pd.DataFrame({"ticker": ["COP", "NEW", "MANH"], "support": [147.47, None, 232.98]})
    styler = tables.support_text(df.style, pd.Series([True, False, False]))
    cells = [line.split() for line in styler.to_string().splitlines()[1:]]
    assert [c[-1] for c in cells] == ["Broken", "NEW", "232.98"]  # NEW's support is blank
    assert df["support"].dtype == float  # still numbers underneath, so it sorts by them


def _support_bars(highs, closes):
    days = pd.bdate_range("2026-10-26", periods=len(highs)).date
    return pd.DataFrame({"date": days, "open": closes, "high": highs,
                         "low": [c - 1 for c in closes], "close": closes})  # fmt: skip


# Swing high 50 on Oct 26, signal Oct 29, ATR 2: a breakout is confirmed by a close of
# 50 + 0.5 x 2 = 51 or more. The bars' lows are close - 1, so the pullback low is 46 (Oct 28).
SUPPORT_PICK = {"ticker": "X", "signal_date": "2026-10-29", "entry_date": None,
                "exit_date": None, "setup_details": json.dumps(
    {"swing_high_date": "2026-10-26", "swing_high": 50.0, "pullback_low": 46.0,
     "atr": 2.0})}  # fmt: skip
BASE_HIGHS, BASE_CLOSES = [50, 49, 48, 49], [49.5, 48, 47, 48.5]
PULLBACK = (date(2026, 10, 28), 46.0)


def _check(bars, expected):
    """support_now matches `expected` and is never above the last close."""
    got = data.support_now(SUPPORT_PICK, bars)
    assert got == expected
    assert got is None or got[1] <= bars["close"].iloc[-1]
    return got


def test_close_just_above_the_high_does_not_move_support():
    # Oct 30 closes at 50.6 and Nov 2 at 50.9: above 50, but under 51.
    bars = _support_bars(BASE_HIGHS + [51, 51.2], BASE_CLOSES + [50.6, 50.9])
    assert data.support_level(SUPPORT_PICK, bars) == PULLBACK
    _check(bars, PULLBACK)


def test_close_half_an_atr_above_the_high_confirms_it():
    bars = _support_bars(BASE_HIGHS + [51.5, 52], BASE_CLOSES + [51, 51.5])
    _check(bars, (date(2026, 10, 30), 50.0))  # dated on the confirming close


LATER_HIGHS = [50, 49, 48, 49, 52, 55, 53, 52, 54, 57]
LATER_CLOSES = [49.5, 48, 47, 48.5, 51, 54, 52, 51, 53, 56]


def test_support_climbs_to_a_later_high_and_falls_back_through_the_levels():
    # 50 is confirmed Oct 30. Nov 2 peaks at 55 (known Nov 4); Nov 6 closes at 56, 55 + 1.
    _check(_support_bars(LATER_HIGHS, LATER_CLOSES), (date(2026, 11, 6), 55.0))
    # Back under 55 but above 50: the 50 floor again.
    _check(_support_bars(LATER_HIGHS + [55], LATER_CLOSES + [52]), (date(2026, 10, 30), 50.0))
    # Under 50 too: the pullback low.
    _check(_support_bars(LATER_HIGHS + [55], LATER_CLOSES + [48]), PULLBACK)


def test_close_below_the_pullback_low_breaks_support():
    _check(_support_bars(LATER_HIGHS + [55], LATER_CLOSES + [45]), None)
    _check(_support_bars(BASE_HIGHS + [47], BASE_CLOSES + [45.5]), None)


def test_higher_low_after_the_signal_raises_support_without_look_ahead():
    # After the signal: Oct 30 up to 50.5 (under 51), Nov 2 dips to a 47.2 low, then turns up.
    highs = BASE_HIGHS + [51, 49, 50, 50.7]
    closes = BASE_CLOSES + [50.5, 48.2, 49.5, 50.2]
    # Through Nov 3 the Nov 2 low has only one session after it: not known yet.
    _check(_support_bars(highs[:-1], closes[:-1]), PULLBACK)
    # Known on Nov 4, two sessions after it formed, above the 46 support then in effect.
    _check(_support_bars(highs, closes), (date(2026, 11, 4), 47.2))


def test_levels_come_from_adjusted_bars_not_stored_details():
    # A later 2-for-1 split halves every stored bar; the details still say 50, 46 and ATR 2.
    bars = _support_bars(BASE_HIGHS + [51.5, 52], BASE_CLOSES + [51, 51.5])
    half = bars.assign(**{c: bars[c] / 2 for c in ("open", "high", "low", "close")})
    assert data.support_level(SUPPORT_PICK, half) == (date(2026, 10, 28), 23.0)
    # Swing high 25, ATR 1: Oct 30's close of 25.5 is exactly 25 + 0.5 x 1.
    _check(half, (date(2026, 10, 30), 25.0))
    # Without the adjustment the stored 50 would sit far above the 25.75 close.


def test_support_is_never_above_the_last_close():
    cases = [
        (BASE_HIGHS + [51, 51.2], BASE_CLOSES + [50.6, 50.9]),
        (BASE_HIGHS + [51.5, 52], BASE_CLOSES + [51, 51.5]),
        (LATER_HIGHS, LATER_CLOSES),
        (LATER_HIGHS + [55], LATER_CLOSES + [52]),
        (LATER_HIGHS + [55], LATER_CLOSES + [48]),
        (LATER_HIGHS + [55], LATER_CLOSES + [45]),
        (BASE_HIGHS + [51, 49, 50, 50.7], BASE_CLOSES + [50.5, 48.2, 49.5, 50.2]),
    ]
    for highs, closes in cases:
        for n in range(len(BASE_HIGHS), len(highs) + 1):  # every day, not just the last
            bars = _support_bars(highs[:n], closes[:n])
            got = data.support_now(SUPPORT_PICK, bars)
            assert got is None or got[1] <= closes[n - 1], (highs[:n], got)


def test_chart_shows_pullback_low_and_raised_support():
    bars = _support_bars(BASE_HIGHS + [51.5, 52], BASE_CLOSES + [51, 51.5])
    history = pd.DataFrame(columns=["date", "close", "stop"])
    fig = charts.pick_chart(bars, SUPPORT_PICK, history, "",
                            data.support_level(SUPPORT_PICK, bars),
                            data.support_now(SUPPORT_PICK, bars))  # fmt: skip
    lines = {t.name: t for t in fig.data}
    assert lines["Support"].y[0] == 50.0 and lines["Support"].x[0] == date(2026, 10, 30)
    assert lines["Pullback low"].y[0] == 46.0
    assert {a.text for a in fig.layout.annotations} == {"Support 50.00", "Pullback low 46.00"}


def test_chart_draws_broken_support_lighter():
    bars = _support_bars(BASE_HIGHS + [47], BASE_CLOSES + [45.5])
    history = pd.DataFrame(columns=["date", "close", "stop"])
    fig = charts.pick_chart(bars, SUPPORT_PICK, history, "",
                            data.support_level(SUPPORT_PICK, bars), None)  # fmt: skip
    line = next(t for t in fig.data if t.name == "Broken support")
    assert line.line.color == charts.PULLBACK_LOW and line.y[0] == 46.0
    assert "Support" not in {t.name for t in fig.data}
    assert {a.text for a in fig.layout.annotations} == {"Broken support 46.00"}


def test_support_label_moves_off_a_nearby_stop_label():
    # Like MANH: stop 233.15 and support 232.98 are 0.07% apart.
    pick = {"ticker": "X", "signal_date": "2026-10-29", "entry_date": "2026-10-30",
            "entry_price": 249.42, "initial_stop": 228.67, "exit_date": None}  # fmt: skip
    bars = _support_bars([240, 236, 233, 250, 255], [238, 235, 234, 248, 255])
    history = pd.DataFrame({"date": [date(2026, 10, 30)], "close": [255.0], "stop": [233.15]})
    fig = charts.pick_chart(bars, pick, history, "", (date(2026, 10, 28), 232.98),
                            (date(2026, 10, 28), 232.98))  # fmt: skip
    shift = {a.text: a.yshift for a in fig.layout.annotations}
    assert shift == {"Stop 233.15": 0, "Support 232.98": -14}
    assert charts._label_shift(240.0, "Support 240.00", 233.15) == 0  # 2.9% apart
