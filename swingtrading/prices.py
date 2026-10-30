"""Store daily bars: full backfill, incremental updates, and gap reporting.

Bars are split/dividend adjusted. A new split or dividend changes every earlier adjusted
price, so an incremental update re-fetches a few stored sessions and, if any close moved,
reloads that ticker's whole history to keep one consistent adjustment basis.
"""

from __future__ import annotations

import bisect
import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from swingtrading import market_calendar
from swingtrading.config import Settings
from swingtrading.data.provider import PRICE_COLUMNS, PriceProvider
from swingtrading.db import transaction

log = logging.getLogger(__name__)


@dataclass
class PriceUpdateResult:
    requested: int = 0
    bars_written: int = 0
    up_to_date: int = 0
    reloaded: list[str] = field(default_factory=list)  # history re-fetched after adjustment
    no_data: list[str] = field(default_factory=list)  # nothing returned at all
    stale: list[str] = field(default_factory=list)  # latest bar older than as_of
    gaps: dict[str, int] = field(default_factory=dict)  # ticker -> missing sessions

    def summary(self) -> str:
        return (
            f"{self.requested} tickers: {self.bars_written} bars written, "
            f"{self.up_to_date} already up to date, {len(self.reloaded)} reloaded after "
            f"adjustment, {len(self.no_data)} with no data, {len(self.stale)} stale, "
            f"{len(self.gaps)} with gaps"
        )


def _check_as_of(as_of: date, settings: Settings, now: datetime | None) -> None:
    """Refuse to store a session that hasn't fully closed (no partial current-day bars)."""
    done = market_calendar.last_completed_session(now, settings.schedule.close_buffer_minutes)
    if as_of > done:
        raise ValueError(f"session {as_of} is not complete yet; last completed is {done}")
    if not market_calendar.is_trading_day(as_of):
        raise ValueError(f"{as_of} is not a trading session")


def window_start(as_of: date, sessions: int) -> date:
    """First session of a window of `sessions` sessions ending at as_of."""
    return as_of if sessions == 1 else market_calendar.previous_session(as_of, sessions - 1)


# ---- storage ----------------------------------------------------------------------------


def save_bars(conn: sqlite3.Connection, bars: pd.DataFrame) -> int:
    if bars.empty:
        return 0
    rows = [
        (r.ticker, r.date.isoformat(), r.open, r.high, r.low, r.close, r.volume)
        for r in bars[PRICE_COLUMNS].itertuples(index=False)
    ]
    conn.executemany(
        """INSERT INTO prices (ticker, date, open, high, low, close, volume, adjusted)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1)
           ON CONFLICT (ticker, date) DO UPDATE SET
               open = excluded.open, high = excluded.high, low = excluded.low,
               close = excluded.close, volume = excluded.volume""",
        rows,
    )
    return len(rows)


def last_dates(conn: sqlite3.Connection) -> dict[str, date]:
    rows = conn.execute("SELECT ticker, MAX(date) AS last FROM prices GROUP BY ticker")
    return {r["ticker"]: date.fromisoformat(r["last"]) for r in rows}


def load_bars(
    conn: sqlite3.Connection,
    tickers: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """Stored bars as a long frame with PRICE_COLUMNS, sorted by ticker and date."""
    sql = "SELECT ticker, date, open, high, low, close, volume FROM prices WHERE 1 = 1"
    params: list[str] = []
    if start:
        sql += " AND date >= ?"
        params.append(start.isoformat())
    if end:
        sql += " AND date <= ?"
        params.append(end.isoformat())
    if tickers is not None:
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS wanted (ticker TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM wanted")
        conn.executemany("INSERT OR IGNORE INTO wanted VALUES (?)", [(t,) for t in tickers])
        sql += " AND ticker IN (SELECT ticker FROM wanted)"
    df = pd.read_sql_query(sql + " ORDER BY ticker, date", conn, params=params)
    df["date"] = [date.fromisoformat(d) for d in df["date"]]
    return df


# ---- adjustment check ---------------------------------------------------------------


def adjusted_tickers(stored: pd.DataFrame, fetched: pd.DataFrame, tolerance: float) -> set[str]:
    """Tickers whose re-fetched closes differ from stored closes on overlapping dates."""
    if stored.empty or fetched.empty:
        return set()
    both = stored.merge(fetched, on=["ticker", "date"], suffixes=("_old", "_new"))
    moved = (both["close_new"] / both["close_old"] - 1).abs() > tolerance
    return set(both.loc[moved, "ticker"])


# ---- updates ----------------------------------------------------------------------------


def update_prices(
    conn: sqlite3.Connection,
    settings: Settings,
    provider: PriceProvider,
    tickers: list[str],
    as_of: date,
    full: bool = False,
    now: datetime | None = None,
) -> PriceUpdateResult:
    """Bring stored bars for `tickers` up to as_of.

    full=True re-downloads the whole backfill window for every ticker. Otherwise only
    sessions after each ticker's last stored bar are fetched (plus a small overlap for the
    adjustment check); tickers with no stored bars get the full window.
    """
    _check_as_of(as_of, settings, now)
    cfg = settings.data
    result = PriceUpdateResult(requested=len(tickers))
    full_start = window_start(as_of, cfg.backfill_sessions)
    last = {} if full else last_dates(conn)

    # Group tickers by the date their fetch starts, so each group is one batched request.
    groups: dict[date, list[str]] = defaultdict(list)
    for t in tickers:
        last_bar = last.get(t)
        if last_bar is None or last_bar < full_start:
            groups[full_start].append(t)
        elif last_bar >= as_of:
            result.up_to_date += 1
        else:
            overlap = market_calendar.previous_session(last_bar, cfg.adjustment_check_sessions)
            groups[max(overlap, full_start)].append(t)

    reload: set[str] = set()
    for start, group in sorted(groups.items()):
        log.info("Fetching %d tickers from %s to %s", len(group), start, as_of)
        bars = provider.history(group, start, as_of)
        bars = bars[(bars["date"] >= start) & (bars["date"] <= as_of)]
        replace: list[str] = []
        if start == full_start:
            # A full window replaces what's stored, so no older bars on another basis remain.
            replace = sorted(set(bars["ticker"]))
        else:
            stored = load_bars(conn, group, start, as_of)
            moved = adjusted_tickers(stored, bars, cfg.adjustment_tolerance)
            reload |= moved
            bars = bars[~bars["ticker"].isin(moved)]
        with transaction(conn):
            conn.executemany("DELETE FROM prices WHERE ticker = ?", [(t,) for t in replace])
            result.bars_written += save_bars(conn, bars)

    if reload:
        log.info("Reloading full history for %d tickers after a split/dividend", len(reload))
        result.reloaded = sorted(reload)
        bars = provider.history(result.reloaded, full_start, as_of)
        bars = bars[(bars["date"] >= full_start) & (bars["date"] <= as_of)]
        got = sorted(set(bars["ticker"]))  # keep old bars for any ticker whose reload failed
        with transaction(conn):
            conn.executemany("DELETE FROM prices WHERE ticker = ?", [(t,) for t in got])
            result.bars_written += save_bars(conn, bars)

    _report(conn, result, tickers, full_start, as_of)
    return result


def _report(
    conn: sqlite3.Connection,
    result: PriceUpdateResult,
    tickers: list[str],
    full_start: date,
    as_of: date,
) -> None:
    """Record tickers with no data, stale data, or missing sessions. Never raises."""
    sessions = market_calendar.sessions_between(full_start, as_of)
    stats = {
        r["ticker"]: r
        for r in conn.execute(
            """SELECT ticker, MIN(date) AS first, MAX(date) AS last, COUNT(*) AS n
               FROM prices WHERE date >= ? GROUP BY ticker""",
            (full_start.isoformat(),),
        )
    }
    for t in tickers:
        s = stats.get(t)
        if s is None:
            result.no_data.append(t)
            continue
        if date.fromisoformat(s["last"]) < as_of:
            result.stale.append(t)
        first = date.fromisoformat(s["first"])
        last = date.fromisoformat(s["last"])
        expected = bisect.bisect_right(sessions, last) - bisect.bisect_left(sessions, first)
        if s["n"] < expected:
            result.gaps[t] = expected - s["n"]

    log.info("Prices: %s", result.summary())
    for label, names in (("No data", result.no_data), ("Stale", result.stale)):
        if names:
            log.warning("%s for %d tickers: %s", label, len(names), _preview(names))
    if result.gaps:
        worst = sorted(result.gaps.items(), key=lambda kv: -kv[1])
        log.warning(
            "Missing sessions for %d tickers: %s",
            len(worst),
            _preview([f"{t}({n})" for t, n in worst]),
        )


def _preview(names: list[str], limit: int = 20) -> str:
    more = f" ... and {len(names) - limit} more" if len(names) > limit else ""
    return " ".join(names[:limit]) + more
