from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from swingtrading import db, market_calendar
from swingtrading import prices as p
from swingtrading.config import Settings
from swingtrading.data.provider import PRICE_COLUMNS

AS_OF = date(2026, 10, 28)
NEXT = date(2026, 10, 29)
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)  # both sessions above are complete


class FakeProvider:
    """Synthetic bars: close = base + session index, scaled by `factor` (adjustment)."""

    def __init__(self, tickers, missing_dates=None, factor=None, dead=()):
        self.tickers = set(tickers)
        self.missing_dates = missing_dates or {}
        self.factor = factor or {}
        self.dead = set(dead)
        self.requests = []

    def history(self, tickers, start, end):
        self.requests.append((sorted(tickers), start, end))
        sessions = market_calendar.sessions_between(start, end + timedelta(days=3))
        rows = []
        for t in tickers:
            if t not in self.tickers or t in self.dead:
                continue
            f = self.factor.get(t, 1.0)
            for d in sessions:  # deliberately runs past `end`, like a partial current bar
                if d in self.missing_dates.get(t, ()):
                    continue
                c = (100 + d.toordinal() % 1000) * f
                rows.append((t, d, c, c * 1.01, c * 0.99, c, 1_000_000))
        return pd.DataFrame(rows, columns=PRICE_COLUMNS)


@pytest.fixture
def settings():
    s = Settings()
    s.data.backfill_sessions = 30
    s.data.adjustment_check_sessions = 3
    return s


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "swing.db")
    yield c
    c.close()


def _count(conn, ticker=None):
    sql, args = "SELECT COUNT(*) FROM prices", ()
    if ticker:
        sql, args = sql + " WHERE ticker = ?", (ticker,)
    return conn.execute(sql, args).fetchone()[0]


def test_backfill_stores_the_window_and_nothing_after_as_of(conn, settings):
    provider = FakeProvider(["AAA", "BBB"])
    result = p.update_prices(conn, settings, provider, ["AAA", "BBB"], AS_OF, full=True, now=NOW)
    assert _count(conn, "AAA") == _count(conn, "BBB") == 30
    assert p.last_dates(conn) == {"AAA": AS_OF, "BBB": AS_OF}
    assert result.bars_written == 60
    assert not (result.no_data or result.stale or result.gaps)
    start = p.window_start(AS_OF, 30)
    assert provider.requests == [(["AAA", "BBB"], start, AS_OF)]


def test_incremental_fetches_only_missing_sessions_plus_overlap(conn, settings):
    provider = FakeProvider(["AAA"])
    p.update_prices(conn, settings, provider, ["AAA"], AS_OF, full=True, now=NOW)
    result = p.update_prices(conn, settings, provider, ["AAA"], NEXT, now=NOW)
    _, start, end = provider.requests[-1]
    assert start == market_calendar.previous_session(AS_OF, 3)
    assert end == NEXT
    assert p.last_dates(conn)["AAA"] == NEXT
    assert result.reloaded == []


def test_up_to_date_tickers_are_not_requested(conn, settings):
    provider = FakeProvider(["AAA"])
    p.update_prices(conn, settings, provider, ["AAA"], AS_OF, full=True, now=NOW)
    result = p.update_prices(conn, settings, provider, ["AAA"], AS_OF, now=NOW)
    assert len(provider.requests) == 1
    assert result.up_to_date == 1
    assert result.bars_written == 0


def test_new_ticker_gets_full_window_in_incremental_mode(conn, settings):
    provider = FakeProvider(["AAA", "NEW"])
    p.update_prices(conn, settings, provider, ["AAA"], AS_OF, full=True, now=NOW)
    p.update_prices(conn, settings, provider, ["AAA", "NEW"], NEXT, now=NOW)
    assert (["NEW"], p.window_start(NEXT, 30), NEXT) in provider.requests
    assert _count(conn, "NEW") == 30


def test_split_or_dividend_reloads_full_history(conn, settings):
    provider = FakeProvider(["AAA", "BBB"])
    p.update_prices(conn, settings, provider, ["AAA", "BBB"], AS_OF, full=True, now=NOW)
    provider.factor["AAA"] = 0.5  # a 2:1 split rewrites every adjusted price
    result = p.update_prices(conn, settings, provider, ["AAA", "BBB"], NEXT, now=NOW)
    assert result.reloaded == ["AAA"]
    assert provider.requests[-1] == (["AAA"], p.window_start(NEXT, 30), NEXT)
    bars = p.load_bars(conn, ["AAA"])
    expected = [(100 + d.toordinal() % 1000) * 0.5 for d in bars["date"]]
    assert bars["close"].tolist() == pytest.approx(expected)
    assert bars["date"].iloc[0] == p.window_start(NEXT, 30)  # no bars left on the old basis
    assert _count(conn, "BBB") == 31


def test_small_differences_within_tolerance_do_not_reload(conn, settings):
    provider = FakeProvider(["AAA"])
    p.update_prices(conn, settings, provider, ["AAA"], AS_OF, full=True, now=NOW)
    provider.factor["AAA"] = 1.0001  # rounding noise, under the 0.05% tolerance
    result = p.update_prices(conn, settings, provider, ["AAA"], NEXT, now=NOW)
    assert result.reloaded == []


def test_failures_and_gaps_are_reported_not_raised(conn, settings):
    hole = market_calendar.previous_session(AS_OF, 10)
    provider = FakeProvider(
        ["AAA", "GAPPY", "DEAD"], missing_dates={"GAPPY": {hole}}, dead={"DEAD"}
    )
    result = p.update_prices(
        conn, settings, provider, ["AAA", "GAPPY", "DEAD"], AS_OF, full=True, now=NOW
    )
    assert result.no_data == ["DEAD"]
    assert result.gaps == {"GAPPY": 1}
    assert _count(conn, "AAA") == 30


def test_stale_ticker_is_reported(conn, settings):
    provider = FakeProvider(["AAA", "HALT"])
    p.update_prices(conn, settings, provider, ["AAA", "HALT"], AS_OF, full=True, now=NOW)
    provider.dead.add("HALT")
    result = p.update_prices(conn, settings, provider, ["AAA", "HALT"], NEXT, now=NOW)
    assert result.stale == ["HALT"]
    assert p.last_dates(conn)["HALT"] == AS_OF


def test_incomplete_session_is_refused(conn, settings):
    during_session = datetime(2026, 10, 28, 17, 0, tzinfo=UTC)  # 1 PM ET
    with pytest.raises(ValueError, match="not complete"):
        p.update_prices(conn, settings, FakeProvider([]), ["AAA"], AS_OF, now=during_session)


def test_non_trading_day_is_refused(conn, settings):
    with pytest.raises(ValueError, match="not a trading session"):
        p.update_prices(conn, settings, FakeProvider([]), ["AAA"], date(2026, 10, 25), now=NOW)


def test_adjusted_tickers():
    d = [date(2026, 10, 27), date(2026, 10, 28)]
    stored = pd.DataFrame({"ticker": ["A", "A", "B", "B"], "date": d + d, "close": [10, 11, 5, 5]})
    fetched = stored.assign(close=[10, 11, 5, 4.9])
    assert p.adjusted_tickers(stored, fetched, 0.0005) == {"B"}


def test_load_bars_filters(conn, settings):
    provider = FakeProvider(["AAA", "BBB"])
    p.update_prices(conn, settings, provider, ["AAA", "BBB"], AS_OF, full=True, now=NOW)
    start = market_calendar.previous_session(AS_OF, 4)
    bars = p.load_bars(conn, ["BBB"], start=start)
    assert set(bars["ticker"]) == {"BBB"}
    assert len(bars) == 5
    assert bars["date"].is_monotonic_increasing
