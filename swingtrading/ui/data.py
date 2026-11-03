"""Read-only queries that shape database rows into tables for the app."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date

import pandas as pd

from swingtrading import db, indicators, market_calendar, performance
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
                   "current_price", "current_stop", "support", "distance_to_stop_pct",
                   "open_pl_pct", "sctr", "days_held", "setup_score"]  # fmt: skip


def current_picks(conn: sqlite3.Connection) -> pd.DataFrame:
    """Signal, open and exit-pending picks with their latest price, P/L and SCTR. Support is
    the setup's pullback low, the same level the chart draws."""
    rows = picks(conn, db.ACTIVE_PICK_STATUSES)
    if not rows:
        return pd.DataFrame(columns=CURRENT_COLUMNS)
    latest = _latest_closes(conn, sorted({p["ticker"] for p in rows}))
    sctr = _latest_sctr(conn)
    out = []
    for p in rows:
        last_date, last_close = latest.get(p["ticker"], (None, None))
        entry, stop = p["entry_price"], p["current_stop"]
        if p["entry_date"] and last_date and last_date < date.fromisoformat(p["entry_date"]):
            last_date = None  # entered this morning: no close since the entry yet
        out.append(
            {
                **{k: p[k] for k in ("id", "ticker", "status", "signal_date", "entry_date",
                                     "entry_price", "current_stop", "setup_score")},
                "current_price": last_close,
                "support": setup_details(p).get("pullback_low"),
                "distance_to_stop_pct": (last_close / stop - 1) * 100
                if stop and last_close
                else None,
                "open_pl_pct": (last_close / entry - 1) * 100
                if entry and last_close and last_date
                else None,
                "sctr": sctr.get(p["ticker"], p["sctr_at_signal"]),
                "days_held": _sessions_held(p["entry_date"], last_date),
            }
        )  # fmt: skip
    df = pd.DataFrame(out)[CURRENT_COLUMNS]
    numeric = ["entry_price", "current_price", "current_stop", "support", "distance_to_stop_pct",
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
    conn: sqlite3.Connection, pick: dict, before: int = 30, ema_periods: tuple[int, ...] = ()
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Bars from `before` sessions ahead of the signal to the exit (or latest), and the
    pick's stop history. Each period in `ema_periods` adds an `ema_<period>` column,
    computed over earlier bars too so it is settled by the first bar shown."""
    signal = date.fromisoformat(pick["signal_date"])
    start = market_calendar.previous_session(signal, before)
    warmup = 3 * max(ema_periods, default=0)
    first = market_calendar.previous_session(start, warmup) if warmup else start
    end = date.fromisoformat(pick["exit_date"]) if pick["exit_date"] else None
    bars = load_bars(conn, [pick["ticker"]], start=first, end=end)
    for period in ema_periods:
        bars[f"ema_{period}"] = indicators.ema(bars["close"], period)
    bars = bars[bars["date"] >= start].reset_index(drop=True)
    history = pd.DataFrame(stop_history(conn, pick["id"]), columns=["date", "close", "stop"])
    history["date"] = [date.fromisoformat(d) for d in history["date"]]
    return bars, history


_SHARE_CLASS = re.compile(
    r"\s*(-\s*)?(Class [A-Z] )?(Common Stock|Common Shares|Ordinary Shares|"
    r"American Depositary Shares|Depositary Shares|Representing Limited Partner).*$",
    re.IGNORECASE,
)


def company(conn: sqlite3.Connection, ticker: str) -> str:
    """'Company name · Industry' for a chart title, e.g. 'Paycom Software, Inc. · Software -
    Application'. ETFs show their fund category. Empty if the ticker is unknown."""
    row = conn.execute(
        "SELECT name, industry, fund_category, quote_type FROM symbols WHERE ticker = ?",
        (ticker,),
    ).fetchone()
    if row is None:
        return ""
    name = _SHARE_CLASS.sub("", row["name"] or "").strip(" -")
    group = row["industry"] or row["fund_category"] or ("ETF" if row["quote_type"] == "ETF" else "")
    return " · ".join(part for part in (name, group) if part)


def support_level(pick: dict, bars: pd.DataFrame) -> tuple[date, float] | None:
    """The setup's pullback low (the level the stock bounced from before the signal) and
    the day it was made: the lowest low after the swing high, up to the signal day."""
    details = setup_details(pick)
    if details.get("pullback_low") is None:
        return None
    level = float(details["pullback_low"])
    signal = date.fromisoformat(pick["signal_date"])
    high_day = details.get("swing_high_date")
    after = date.fromisoformat(high_day) if high_day else date.min
    window = bars[(bars["date"] > after) & (bars["date"] <= signal)]
    day = window.loc[window["low"].idxmin(), "date"] if not window.empty else signal
    return day, level


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


def why_selected(pick: dict, settings: Settings) -> list[str]:
    """Plain-English reasons the pick was made: one markdown line per rule it passed, with
    the stock's number next to the limit from settings."""
    r, s = settings.ranking, settings.setup
    out = []
    if pick.get("sctr_at_signal") is not None:
        out.append(
            f"**Strong stock:** SCTR {pick['sctr_at_signal']:.1f} out of 100 on the signal "
            f"day, in the top {r.shortlist_pct:g}% of the universe (only those are scanned)."
        )
    d = setup_details(pick)
    if d.get("detector") != "pullback_pivot" or "swing_high" not in d:
        return out
    high_day = date.fromisoformat(d["swing_high_date"]).strftime("%b %d")
    down = (
        "There were no down days in the dip."
        if d["down_volume_ratio"] == 0
        else f"Volume on the down days was {d['down_volume_ratio']:.2f}× its "
        f"{s.volume_avg_period}-day average (must be below {s.down_volume_max_ratio:g}×)."
    )
    out += [
        f"**Recent high:** it reached {d['swing_high']:.2f} on {high_day}, its highest in the "
        f"last {s.swing_high_lookback} sessions.",
        f"**Healthy dip:** it pulled back {d['depth_pct']:.1f}% ({d['depth_atr']:.1f} times its "
        f"daily move, ATR) to {d['pullback_low']:.2f} over {d['pullback_bars']} session(s). "
        f"Allowed: {s.pullback_min_pct:g}–{s.pullback_max_pct:g}%, {s.pullback_min_atr:g}–"
        f"{s.pullback_max_atr:g} ATR, at most {s.max_pullback_sessions} sessions.",
        f"**Quiet selling:** {down}",
        f"**Trend held:** no close below the {s.trend_ema_period}-day EMA during the dip.",
        f"**Turned up:** on the signal day it closed above the prior day's high, "
        f"{d['close_in_range'] * 100:.0f}% of the way up the day's range (needs "
        f"{s.pivot_close_range_min * 100:.0f}%+), on {d['pivot_volume_ratio']:.2f}× its "
        f"{s.pivot_volume_period}-day average volume (needs {s.pivot_volume_min_ratio:g}×+).",
    ]
    if pick.get("setup_score") is not None:
        out.append(
            f"**Setup score {pick['setup_score']:.0f} / 100:** how cleanly it passed (dip "
            "depth, quiet selling, turn-up volume, strong close; a quarter each)."
        )
    return out


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
