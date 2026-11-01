from datetime import UTC, date, datetime

import pandas as pd
import pytest

from swingtrading import db, market_calendar, pipeline
from swingtrading import positions as pos
from swingtrading.config import Settings
from swingtrading.data.provider import PRICE_COLUMNS
from tests.fixtures.synthetic import BASE_VOLUME, END, bar_frame, setup_frame

D1, D2 = date(2026, 10, 29), date(2026, 10, 30)  # the sessions after the pivot on END
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)  # Saturday: Friday 10-30 is complete


def _piv() -> pd.DataFrame:
    """A clean setup on END, then up on D1 (entry) and a crash on D2 (stop-out)."""
    df = setup_frame(trend_bars=260)
    c = df["close"].iloc[-1]
    after = pd.DataFrame(
        [
            {"date": D1, "open": c * 1.005, "high": c * 1.03, "low": c, "close": c * 1.02},
            {"date": D2, "open": c, "high": c * 1.001, "low": c * 0.8, "close": c * 0.82},
        ]
    ).assign(volume=BASE_VOLUME)
    return pd.concat([df, after], ignore_index=True).assign(ticker="PIV")


def _flat() -> pd.DataFrame:
    n = len(_piv())
    moves = [0.3 if i % 2 else -0.3 for i in range(n)]
    return bar_frame(moves, [BASE_VOLUME] * n, end=D2).assign(ticker="FLAT")


class FrameProvider:
    def __init__(self, frames):
        self.data = pd.concat(frames)[PRICE_COLUMNS]
        self.calls = 0

    def history(self, tickers, start, end):
        self.calls += 1
        d = self.data
        return d[d["ticker"].isin(tickers) & (d["date"] >= start) & (d["date"] <= end)]


@pytest.fixture
def settings():
    s = Settings()
    s.ranking.shortlist_pct = 100
    s.data.backfill_sessions = 300
    return s


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "swing.db")
    with db.transaction(c):
        c.executemany(
            "INSERT INTO symbols (ticker, name, exchange) VALUES (?, ?, 'Q')",
            [("PIV", "Pivot Inc"), ("FLAT", "Flat Co")],
        )
    yield c
    c.close()


@pytest.fixture
def provider():
    return FrameProvider([_piv(), _flat()])


def _runs(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY session_date")]


def test_first_run_creates_a_signal(conn, settings, provider):
    reports = pipeline.run(conn, settings, provider, target=END, now=NOW)
    assert len(reports) == 1
    r = reports[0]
    assert r.status == "success" and r.session == END
    assert r.universe_size == 2 and r.shortlist_size == 2
    assert r.new_picks == ["PIV"]
    pick = pos.picks(conn)[0]
    assert pick["status"] == "signal" and pick["signal_date"] == END.isoformat()
    assert pick["sctr_at_signal"] is not None and pick["signal_atr"] > 0
    run = _runs(conn)[0]
    assert run["status"] == "success" and run["new_picks"] == 1 and run["shortlist_size"] == 2
    assert run["finished_at"] is not None


def test_catch_up_processes_missed_sessions_in_order(conn, settings, provider):
    pipeline.run(conn, settings, provider, target=END, now=NOW)
    reports = pipeline.run(conn, settings, provider, now=NOW)  # default: last completed
    assert [r.session for r in reports] == [D1, D2]
    assert [t for t, _ in reports[0].entries] == ["PIV"]
    assert [t for t, *_ in reports[1].stop_outs] == ["PIV"]
    assert reports[1].new_picks == []  # PIV still has an active pick
    pick = pos.picks(conn)[0]
    assert pick["status"] == "exit_pending" and pick["entry_date"] == D1.isoformat()
    assert [r["session_date"] for r in _runs(conn)] == [d.isoformat() for d in (END, D1, D2)]


def test_rerun_is_a_no_op(conn, settings, provider):
    pipeline.run(conn, settings, provider, now=NOW)
    before = (pos.picks(conn), _runs(conn))
    assert pipeline.run(conn, settings, provider, now=NOW) == []
    assert pipeline.run(conn, settings, provider, target=END, now=NOW) == []  # already done
    assert (pos.picks(conn), _runs(conn)) == before


def test_dry_run_writes_nothing(conn, settings, provider):
    reports = pipeline.run(conn, settings, provider, target=END, dry_run=True, now=NOW)
    assert reports[0].new_picks == ["PIV"]
    assert pos.picks(conn) == [] and _runs(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM rankings").fetchone()[0] == 0
    assert "Dry run" in pipeline.format_reports(reports, dry_run=True)


def test_non_trading_day_is_skipped(conn, settings, provider):
    sunday = date(2026, 10, 25)
    reports = pipeline.run(conn, settings, provider, target=sunday, now=NOW)
    assert reports[0].status == "skipped"
    assert _runs(conn)[0]["status"] == "skipped"
    assert provider.calls == 0
    assert "not a trading session" in pipeline.format_reports(reports)


def test_incomplete_session_is_refused(conn, settings, provider):
    with pytest.raises(ValueError, match="not complete"):
        pipeline.run(conn, settings, provider, target=date(2026, 11, 2), now=NOW)


def test_failed_session_stops_the_catch_up_and_is_retried(conn, settings, provider, monkeypatch):
    pipeline.run(conn, settings, provider, target=END, now=NOW)

    def boom(*args, **kwargs):
        raise RuntimeError("detector broke")

    monkeypatch.setattr(pipeline, "scan", boom)
    reports = pipeline.run(conn, settings, provider, now=NOW)
    assert [(r.session, r.status) for r in reports] == [(D1, "failed")]  # D2 not attempted
    assert "detector broke" in reports[0].error
    assert pos.picks(conn)[0]["status"] == "signal"  # the session's changes rolled back
    assert _runs(conn)[-1]["status"] == "failed"

    monkeypatch.undo()
    reports = pipeline.run(conn, settings, provider, now=NOW)
    assert [(r.session, r.status) for r in reports] == [(D1, "success"), (D2, "success")]


def test_price_failure_is_recorded(conn, settings):
    class Down:
        def history(self, *args):
            raise ConnectionError("no network")

    reports = pipeline.run(conn, settings, Down(), target=END, now=NOW)
    assert reports[0].status == "failed" and "no network" in reports[0].error
    assert _runs(conn)[0]["status"] == "failed"


def test_notifier_gets_a_summary(conn, settings, provider):
    sent = []
    pipeline.run(conn, settings, provider, target=END, now=NOW, notifier=sent.append)
    assert sent == [f"{END}: 1 new picks, 0 stops raised, 0 stopped out, 0 closed"]
    sent.clear()
    pipeline.run(conn, settings, provider, target=END, dry_run=True, now=NOW, notifier=sent.append)
    assert sent == []


def test_format_reports_lists_the_day(conn, settings, provider):
    reports = pipeline.run(conn, settings, provider, target=END, now=NOW)
    reports += pipeline.run(conn, settings, provider, now=NOW)
    text = pipeline.format_reports(reports)
    assert f"Session {END}" in text and "New picks (1): PIV" in text
    assert "Entered at the open: PIV" in text and "Stopped out" in text


def test_sessions_to_process(conn):
    assert pipeline.sessions_to_process(conn, D2) == [D2]  # first run: just the target
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO runs (session_date, started_at, status) VALUES (?, 'x', 'success')",
            (market_calendar.previous_session(END, 2).isoformat(),),
        )
    assert pipeline.sessions_to_process(conn, D2) == [
        market_calendar.previous_session(END),
        END,
        D1,
        D2,
    ]


def test_second_run_is_refused_while_one_is_in_progress(conn, settings, provider):
    with pipeline.run_lock(conn):  # as if the scheduled run were still going
        for dry_run in (False, True):
            with pytest.raises(pipeline.RunInProgressError, match="already in progress"):
                pipeline.run(conn, settings, provider, target=END, dry_run=dry_run, now=NOW)
    assert provider.calls == 0 and _runs(conn) == [] and pos.picks(conn) == []
    reports = pipeline.run(conn, settings, provider, target=END, now=NOW)  # lock released
    assert reports[0].new_picks == ["PIV"]


def test_lock_is_released_when_a_run_raises(conn, settings, provider, monkeypatch):
    monkeypatch.setattr(pipeline, "sessions_to_process", lambda *a: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        pipeline.run(conn, settings, provider, target=END, now=NOW)
    monkeypatch.undo()
    assert pipeline.run(conn, settings, provider, target=END, now=NOW)[0].status == "success"


def test_in_memory_database_needs_no_lock(settings):
    mem = db.connect(":memory:")
    with pipeline.run_lock(mem), pipeline.run_lock(mem):
        pass
    mem.close()


def test_notifier_quiet_when_nothing_to_do(conn, settings, provider):
    pipeline.run(conn, settings, provider, target=END, now=NOW)
    sent = []
    pipeline.run(conn, settings, provider, target=END, now=NOW, notifier=sent.append)
    pipeline.run(conn, settings, provider, target=date(2026, 10, 25), now=NOW, notifier=sent.append)
    assert sent == []
