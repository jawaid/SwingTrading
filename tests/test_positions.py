import json

import numpy as np
import pandas as pd
import pytest

from swingtrading import db, market_calendar, stops
from swingtrading import positions as pos
from swingtrading.config import Settings
from swingtrading.data.provider import PRICE_COLUMNS
from swingtrading.setups.base import SetupResult
from tests.fixtures.synthetic import END

# ATR(3) x 2 keeps the numbers easy to check by hand.
SETTINGS = Settings().model_copy(
    update={"stops": Settings().stops.model_copy(update={"atr_period": 3, "atr_mult": 2.0})}
)
FLAT = (100.0, 101.0, 99.0, 100.0)  # open, high, low, close: true range 2, so ATR 2
BASE = 10


def _bars(path: list[tuple[float, float, float, float]], ticker: str = "AAA") -> pd.DataFrame:
    """BASE flat sessions, then `path`, ending on END."""
    rows = [FLAT] * BASE + path
    sessions = market_calendar.sessions_between(market_calendar.previous_session(END, 60), END)
    sessions = sessions[-len(rows) :]
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df.insert(0, "date", sessions)
    df.insert(0, "ticker", ticker)
    df["volume"] = 1e6
    return df[PRICE_COLUMNS]


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "swing.db")
    yield c
    c.close()


def _signal(conn, bars, i, ticker="AAA"):
    session = bars["date"].iloc[i]
    bar = pos.session_bars(bars, session, 3)[ticker]
    setup = SetupResult("pullback_pivot", session, 70.0, {"depth_pct": 5.0})
    return pos.create_signal(conn, ticker, session, bar.close, bar.atr, 95.0, setup)


def _run(conn, bars, start, end=None):
    """Process sessions bars[start..end] in order; returns the last result."""
    end = len(bars) - 1 if end is None else end
    result = None
    for i in range(start, end + 1):
        result = pos.process_session(conn, SETTINGS, bars["date"].iloc[i], bars)
    return result


# ---- stops.py ------------------------------------------------------------------------


def test_initial_stop():
    assert stops.initial_stop(101.0, 2.0, 2.5) == pytest.approx(96.0)


@pytest.mark.parametrize("seed", range(5))
def test_trailing_stop_never_decreases(seed):
    rng = np.random.default_rng(seed)
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.03, 300))
    atrs = rng.uniform(0.5, 8.0, 300)  # wild ATR swings would pull a naive stop down
    stop, highest, path = 90.0, -np.inf, []
    for c, a in zip(closes, atrs, strict=True):
        highest = max(highest, c)
        stop = stops.trail_stop(stop, highest, a, 2.5)
        path.append(stop)
    assert all(b >= a for a, b in zip(path, path[1:], strict=False))
    assert path[-1] > 90.0


def test_trail_stop_without_atr_stays_put():
    assert stops.trail_stop(95.0, 120.0, float("nan"), 2.5) == 95.0


def test_stopped_out_is_strictly_below():
    assert stops.stopped_out(94.99, 95.0) and not stops.stopped_out(95.0, 95.0)


# ---- lifecycle -----------------------------------------------------------------------


def test_full_lifecycle(conn):
    bars = _bars(
        [
            (100.0, 101.0, 99.0, 100.0),  # 10: signal on this close
            (101.0, 103.0, 100.0, 102.0),  # 11: enter at 101, new high close
            (102.0, 106.0, 101.5, 105.0),  # 12: higher close, stop rises
            (105.0, 105.5, 100.0, 101.0),  # 13: pullback, stop holds
            (101.0, 101.5, 94.0, 95.0),  # 14: close below stop -> exit_pending
            (94.0, 96.0, 93.0, 95.5),  # 15: exit at the open, 94
        ]
    )
    pid = _signal(conn, bars, BASE)
    assert pos.get_pick(conn, pid)["status"] == "signal"

    r = _run(conn, bars, BASE, BASE)  # the signal session itself: nothing fills
    assert r.entries == [] and pos.get_pick(conn, pid)["status"] == "signal"

    r = _run(conn, bars, BASE + 1, BASE + 1)
    p = pos.get_pick(conn, pid)
    assert r.entries == [pid] and p["status"] == "open"
    assert p["entry_date"] == bars["date"].iloc[BASE + 1].isoformat()
    assert p["entry_price"] == 101.0
    assert p["initial_stop"] == pytest.approx(101.0 - 2 * 2.0)  # ATR at signal = 2
    atr = stops.atr_series(bars, 3)
    assert p["current_stop"] == pytest.approx(max(97.0, 102.0 - 2 * atr.iloc[BASE + 1]))
    assert p["highest_close"] == 102.0

    _run(conn, bars, BASE + 2, BASE + 3)
    p = pos.get_pick(conn, pid)
    expected = 105.0 - 2 * atr.iloc[BASE + 2]
    assert p["current_stop"] == pytest.approx(expected)  # the 13th's wider ATR can't lower it
    assert p["highest_close"] == 105.0

    r = _run(conn, bars, BASE + 4, BASE + 4)
    p = pos.get_pick(conn, pid)
    assert r.stop_outs == [pid] and p["status"] == "exit_pending"
    assert p["exit_signal_date"] == bars["date"].iloc[BASE + 4].isoformat()
    assert pos.active_tickers(conn) == {"AAA"}  # still blocks a new signal

    r = _run(conn, bars, BASE + 5, BASE + 5)
    p = pos.get_pick(conn, pid)
    assert r.exits == [pid] and p["status"] == "closed"
    assert p["exit_date"] == bars["date"].iloc[BASE + 5].isoformat()
    assert p["exit_price"] == 94.0
    assert p["return_pct"] == pytest.approx(94.0 / 101.0 - 1)
    assert p["days_held"] == 4
    assert pos.active_tickers(conn) == set()

    history = pos.stop_history(conn, pid)
    assert [h["date"] for h in history] == [d.isoformat() for d in bars["date"].iloc[11:15]]
    assert all(b["stop"] >= a["stop"] for a, b in zip(history, history[1:], strict=False))


def test_gap_down_exit_fills_at_next_open(conn):
    bars = _bars(
        [
            (100.0, 101.0, 99.0, 100.0),  # signal
            (100.0, 101.0, 99.0, 100.0),  # enter at 100, stop 96
            (99.0, 99.5, 95.0, 95.5),  # close 95.5 < 96 -> exit_pending
            (80.0, 82.0, 79.0, 81.0),  # gaps far below the stop: exit at 80
        ]
    )
    pid = _signal(conn, bars, BASE)
    _run(conn, bars, BASE)
    p = pos.get_pick(conn, pid)
    assert p["status"] == "closed"
    assert p["exit_price"] == 80.0  # the open, not the stop
    assert p["return_pct"] == pytest.approx(-0.2)


def test_close_equal_to_stop_is_not_a_stop_out(conn):
    bars = _bars([FLAT, FLAT, (99.0, 99.5, 95.5, 96.0)])  # entry 100, stop 96
    pid = _signal(conn, bars, BASE)
    _run(conn, bars, BASE)
    assert pos.get_pick(conn, pid)["status"] == "open"


def test_stop_out_on_entry_day(conn):
    bars = _bars([FLAT, (100.0, 100.5, 90.0, 91.0)])  # enters at 100, closes below 96
    pid = _signal(conn, bars, BASE)
    r = _run(conn, bars, BASE)
    assert r.entries == [pid] and r.stop_outs == [pid]
    assert pos.get_pick(conn, pid)["status"] == "exit_pending"


def test_duplicate_active_pick_is_blocked(conn):
    bars = _bars([FLAT, FLAT, (99.0, 99.5, 90.0, 91.0), FLAT])
    _signal(conn, bars, BASE)
    with pytest.raises(pos.DuplicatePickError):
        _signal(conn, bars, BASE + 1)
    _run(conn, bars, BASE)  # enters, stops out, closes
    assert pos.picks(conn)[0]["status"] == "closed"
    second = _signal(conn, bars, BASE + 3)  # a closed pick doesn't block a new one
    assert pos.get_pick(conn, second)["status"] == "signal"


def test_processing_a_session_twice_changes_nothing(conn):
    bars = _bars([FLAT, (101.0, 103.0, 100.0, 102.0), (102.0, 106.0, 101.5, 105.0)])
    pid = _signal(conn, bars, BASE)
    _run(conn, bars, BASE)
    before = (pos.get_pick(conn, pid), pos.stop_history(conn, pid))
    r = _run(conn, bars, len(bars) - 1)
    after = (pos.get_pick(conn, pid), pos.stop_history(conn, pid))
    assert r.entries == r.stops_raised == r.stop_outs == []
    assert before[0]["current_stop"] == after[0]["current_stop"]
    assert before[1] == after[1]


def test_missing_bar_leaves_pick_waiting(conn):
    bars = _bars([FLAT, FLAT])
    pid = _signal(conn, bars, BASE)
    other = _bars([FLAT, FLAT], ticker="BBB")
    r = pos.process_session(conn, SETTINGS, bars["date"].iloc[-1], other)
    assert r.missing == ["AAA"]
    assert pos.get_pick(conn, pid)["status"] == "signal"


def test_rollback_undoes_a_session(conn):
    bars = _bars([FLAT, FLAT])
    pid = _signal(conn, bars, BASE)
    conn.commit()
    with pytest.raises(RuntimeError), db.transaction(conn):
        _run(conn, bars, BASE)
        raise RuntimeError("dry run")
    assert pos.get_pick(conn, pid)["status"] == "signal"
    assert pos.stop_history(conn, pid) == []


def test_setup_details_are_stored_as_json(conn):
    bars = _bars([FLAT])
    p = pos.get_pick(conn, _signal(conn, bars, BASE))
    assert json.loads(p["setup_details"]) == {"detector": "pullback_pivot", "depth_pct": 5.0}
    assert p["signal_atr"] == pytest.approx(2.0) and p["sctr_at_signal"] == 95.0
