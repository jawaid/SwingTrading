"""Read-only queries that shape database rows into tables for the app."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date

import pandas as pd

from swingtrading import db, market_calendar, performance
from swingtrading.config import Settings, load_settings
from swingtrading.positions import picks, stop_history
from swingtrading.prices import load_bars


def open_db(settings: Settings | None = None) -> tuple[Settings, sqlite3.Connection]:
    settings = settings or load_settings()
    return settings, db.connect(settings.resolve(settings.paths.db_path))


def _latest_closes(conn: sqlite3.Connection, tickers: list[str]) -> dict[str, tuple[date, float]]:
    if not tickers:
        return {}
    marks = ", ".join("?" * len(tickers))
    rows = conn.execute(
        f"""SELECT p.ticker, p.date, p.close FROM prices p
            JOIN (SELECT ticker, MAX(date) AS d FROM prices WHERE ticker IN ({marks})
                  GROUP BY ticker) m ON p.ticker = m.ticker AND p.date = m.d""",
        tickers,
    )
    return {r["ticker"]: (date.fromisoformat(r["date"]), r["close"]) for r in rows}


def _latest_sctr(conn: sqlite3.Connection) -> dict[str, float]:
    rows = conn.execute(
        "SELECT ticker, sctr FROM rankings WHERE date = (SELECT MAX(date) FROM rankings)"
    )
    return {r["ticker"]: r["sctr"] for r in rows}


def _sessions_held(start: str | None, end: date | None) -> int | None:
    if not start or end is None:
        return None
    return len(market_calendar.sessions_between(date.fromisoformat(start), end)) - 1


CURRENT_COLUMNS = ["id", "ticker", "status", "signal_date", "entry_date", "entry_price",
                   "current_price", "current_stop", "distance_to_stop_pct", "open_pl_pct",
                   "sctr", "days_held", "setup_score"]  # fmt: skip


def current_picks(conn: sqlite3.Connection) -> pd.DataFrame:
    """Signal, open and exit-pending picks with their latest price, P/L and SCTR."""
    rows = picks(conn, db.ACTIVE_PICK_STATUSES)
    if not rows:
        return pd.DataFrame(columns=CURRENT_COLUMNS)
    latest = _latest_closes(conn, sorted({p["ticker"] for p in rows}))
    sctr = _latest_sctr(conn)
    out = []
    for p in rows:
        last_date, last_close = latest.get(p["ticker"], (None, None))
        entry, stop = p["entry_price"], p["current_stop"]
        out.append(
            {
                **{k: p[k] for k in ("id", "ticker", "status", "signal_date", "entry_date",
                                     "entry_price", "current_stop", "setup_score")},
                "current_price": last_close,
                "distance_to_stop_pct": (last_close / stop - 1) * 100
                if stop and last_close
                else None,
                "open_pl_pct": (last_close / entry - 1) * 100 if entry and last_close else None,
                "sctr": sctr.get(p["ticker"], p["sctr_at_signal"]),
                "days_held": _sessions_held(p["entry_date"], last_date),
            }
        )  # fmt: skip
    df = pd.DataFrame(out)[CURRENT_COLUMNS]
    numeric = ["entry_price", "current_price", "current_stop", "distance_to_stop_pct",
               "open_pl_pct", "sctr", "days_held", "setup_score"]  # fmt: skip
    df[numeric] = df[numeric].astype(float)  # None -> NaN, which tables show as blank
    df["entry_date"] = df["entry_date"].fillna("")
    return df


PAST_COLUMNS = ["id", "ticker", "signal_date", "entry_date", "entry_price", "exit_date",
                "exit_price", "return_pct", "days_held", "sctr_at_signal",
                "setup_score"]  # fmt: skip


def past_picks(conn: sqlite3.Connection) -> pd.DataFrame:
    """Closed picks, newest exit first; return_pct is in percent here."""
    rows = picks(conn, ("closed",))
    df = pd.DataFrame(rows, columns=list(rows[0]) if rows else PAST_COLUMNS)[PAST_COLUMNS]
    df["return_pct"] = df["return_pct"].astype(float) * 100
    return df.sort_values(["exit_date", "id"], ascending=False, ignore_index=True)


def pick_chart_data(
    conn: sqlite3.Connection, pick: dict, before: int = 30
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Bars from `before` sessions ahead of the signal to the exit (or latest), and the
    pick's stop history."""
    signal = date.fromisoformat(pick["signal_date"])
    start = market_calendar.previous_session(signal, before)
    end = date.fromisoformat(pick["exit_date"]) if pick["exit_date"] else None
    bars = load_bars(conn, [pick["ticker"]], start=start, end=end)
    history = pd.DataFrame(stop_history(conn, pick["id"]), columns=["date", "close", "stop"])
    history["date"] = [date.fromisoformat(d) for d in history["date"]]
    return bars, history


@dataclass
class PerformanceData:
    stats: performance.TradeStats | None
    equity: pd.Series  # growth of 1, by session
    benchmark: pd.Series  # SPY growth of 1 over the same sessions (empty without prices)
    max_drawdown: float
    monthly: pd.DataFrame  # month, picks %, SPY %


def performance_data(conn: sqlite3.Connection) -> PerformanceData:
    """Closed-trade stats plus the equity curve of every entered pick against SPY."""
    closed = picks(conn, ("closed",))
    stats = performance.trade_stats(
        [p["return_pct"] for p in closed], [p["days_held"] for p in closed]
    )
    entered = [p for p in picks(conn) if p["entry_date"]]
    empty = pd.Series(dtype=float)
    if not entered:
        return PerformanceData(stats, empty, empty, 0.0, pd.DataFrame())
    start = market_calendar.previous_session(
        min(date.fromisoformat(p["entry_date"]) for p in entered)
    )
    tickers = sorted({p["ticker"] for p in entered} | {performance.BENCHMARK})
    bars = load_bars(conn, tickers, start=start)
    closes = bars.pivot(index="date", columns="ticker", values="close")
    for t in tickers:  # a ticker with no stored bars still needs a column
        if t not in closes:
            closes[t] = float("nan")
    held = closes.drop(columns=[performance.BENCHMARK])
    equity = performance.equity_curve(performance.daily_returns(entered, held))
    spy = closes[performance.BENCHMARK].dropna()
    benchmark = performance.benchmark_curve(spy, equity.index) if len(spy) else empty
    monthly = pd.DataFrame({"picks_pct": performance.monthly_returns(equity) * 100})
    if len(benchmark):
        monthly["spy_pct"] = performance.monthly_returns(benchmark) * 100
    monthly = monthly.rename_axis("month").reset_index().iloc[::-1].reset_index(drop=True)
    return PerformanceData(stats, equity, benchmark, performance.max_drawdown(equity), monthly)


def setup_details(pick: dict) -> dict:
    return json.loads(pick["setup_details"]) if pick.get("setup_details") else {}


def runs(conn: sqlite3.Connection, limit: int = 20) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM runs ORDER BY session_date DESC LIMIT ?", conn, params=(limit,)
    )


def data_status(conn: sqlite3.Connection) -> dict:
    """Counts the About screen and empty states use."""
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "universe": one("SELECT COUNT(*) FROM symbols WHERE excluded_reason IS NULL"),
        "last_price_date": one("SELECT MAX(date) FROM prices"),
        "last_ranking_date": one("SELECT MAX(date) FROM rankings"),
        "picks": one("SELECT COUNT(*) FROM picks"),
    }
