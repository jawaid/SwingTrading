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
                   "current_price", "current_stop", "support", "support_broken",
                   "distance_to_stop_pct", "open_pl_pct", "sctr", "days_held",
                   "setup_score"]  # fmt: skip


def current_picks(conn: sqlite3.Connection) -> pd.DataFrame:
    """Signal, open and exit-pending picks with their latest price, P/L and SCTR. Support is
    where it stands now (`support_now`), the same level the chart labels. When the price
    has closed below every level, `support_broken` is True and support holds the broken
    pullback low; with no setup data it is NaN."""
    rows = picks(conn, db.ACTIVE_PICK_STATUSES)
    if not rows:
        return pd.DataFrame(columns=CURRENT_COLUMNS)
    latest = _latest_closes(conn, sorted({p["ticker"] for p in rows}))
    sctr = _latest_sctr(conn)
    starts = [setup_details(p).get("swing_high_date") or p["signal_date"] for p in rows]
    bars = load_bars(
        conn, sorted({p["ticker"] for p in rows}), start=date.fromisoformat(min(starts))
    )
    out = []
    for p in rows:
        own = bars[bars["ticker"] == p["ticker"]].reset_index(drop=True)
        support, base = support_now(p, own), support_level(p, own)
        last_date, last_close = latest.get(p["ticker"], (None, None))
        entry, stop = p["entry_price"], p["current_stop"]
        if p["entry_date"] and last_date and last_date < date.fromisoformat(p["entry_date"]):
            last_date = None  # entered this morning: no close since the entry yet
        out.append(
            {
                **{k: p[k] for k in ("id", "ticker", "status", "signal_date", "entry_date",
                                     "entry_price", "current_stop", "setup_score")},
                "current_price": last_close,
                # Broken (levels exist, price closed below them all): keep the pullback
                # low so the column still sorts by price; the table shows "Broken".
                "support": support[1] if support else base[1] if base else None,
                "support_broken": support is None and base is not None,
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


PIVOT_BARS = 2  # a pivot is the highest high (or lowest low) of 2 sessions either side
BREAKOUT_ATR_BUFFER = 0.5  # a close this many ATRs above a ceiling confirms the breakout


def _setup_levels(pick: dict, bars: pd.DataFrame) -> dict | None:
    """The setup's swing high, pullback low (and its day) and ATR, recomputed from `bars`
    on the stored dates so they follow any later split or dividend adjustment. The ATR is
    scaled by the same factor as the swing high. Falls back to the stored values when a
    needed bar is missing. None without a stored pullback low."""
    details = setup_details(pick)
    if details.get("pullback_low") is None:
        return None
    signal = date.fromisoformat(pick["signal_date"])
    high_day = (
        date.fromisoformat(details["swing_high_date"]) if details.get("swing_high_date") else None
    )
    after = high_day or date.min
    window = bars[(bars["date"] > after) & (bars["date"] <= signal)]
    i = window["low"].idxmin() if not window.empty else None
    stored = {
        "high_day": high_day,
        "swing_high": details.get("swing_high"),
        "pullback_low": float(details["pullback_low"]),
        "low_day": window.loc[i, "date"] if i is not None else signal,
        "atr": details.get("atr"),
    }
    at_high = bars[bars["date"] == high_day] if high_day else bars.iloc[0:0]
    if at_high.empty or i is None or not details.get("swing_high"):
        return stored
    swing_high = float(at_high["high"].iloc[0])
    atr = details.get("atr")
    return {
        "high_day": high_day,
        "swing_high": swing_high,
        "pullback_low": float(window.loc[i, "low"]),
        "low_day": window.loc[i, "date"],
        "atr": float(atr) * swing_high / float(details["swing_high"]) if atr else None,
    }


def support_level(pick: dict, bars: pd.DataFrame) -> tuple[date, float] | None:
    """The setup's pullback low (the level the stock bounced from before the signal) and
    the day it was made: the lowest bar low after the swing high, up to the signal day."""
    levels = _setup_levels(pick, bars)
    return None if levels is None else (levels["low_day"], levels["pullback_low"])


def support_now(pick: dict, bars: pd.DataFrame) -> tuple[date, float] | None:
    """Support on the last bar, and the day that level took effect; None when the price has
    closed below every support level (support is broken). Display only: no trading rule
    uses it.

    Candidates, each from the day it takes effect:
    - the pullback low;
    - confirmed broken ceilings: the setup's swing high and any later pivot high (known
      PIVOT_BARS sessions after it forms). A ceiling is confirmed by the first close at or
      above ceiling + BREAKOUT_ATR_BUFFER x ATR after it is known, and its level is the
      ceiling itself;
    - higher lows: pivot lows after the signal (known PIVOT_BARS sessions after they form),
      counted only if higher than the support in effect on the day they become known.
    Support is the highest candidate at or below the last close."""
    levels = _setup_levels(pick, bars)
    if levels is None or bars.empty:
        return None
    floor = (levels["low_day"], levels["pullback_low"])
    b = bars.reset_index(drop=True)
    last_close = float(b["close"].iloc[-1])
    high_day, atr = levels["high_day"], levels["atr"]
    if high_day is None or not atr or levels["swing_high"] is None:
        return floor if floor[1] <= last_close else None
    signal = date.fromisoformat(pick["signal_date"])
    dates, high, low, close = list(b["date"]), b["high"], b["low"], b["close"]
    start = next((i for i, d in enumerate(dates) if d >= high_day), len(b))
    k = PIVOT_BARS

    # Ceilings: (index known from, level). The setup's swing high is known on its own day.
    ceilings = [(start, float(levels["swing_high"]))]
    for i in range(start + 1, len(b) - k):
        h = high.iloc[i]
        if h > high.iloc[max(start, i - k) : i].max() and h >= high.iloc[i + 1 : i + k + 1].max():
            ceilings.append((i + k, float(h)))
    # Higher lows: (index known from, level, index formed), after the signal.
    lows = []
    for i in range(start + k, len(b) - k):
        lo = low.iloc[i]
        if (dates[i] > signal and lo < low.iloc[i - k : i].min()
                and lo <= low.iloc[i + 1 : i + k + 1].min()):  # fmt: skip
            lows.append((i + k, float(lo)))

    # Walk forward: each candidate joins on the day it takes effect.
    active = [floor]
    confirmed = set()
    for j in range(start, len(b)):
        for n, (known, level) in enumerate(ceilings):
            if (
                n not in confirmed
                and j > known
                and close.iloc[j] >= level + BREAKOUT_ATR_BUFFER * atr
            ):
                confirmed.add(n)
                active.append((dates[j], level))
        for known, level in lows:
            if known == j:
                in_effect = [lv for _, lv in active if lv <= close.iloc[j]]
                if not in_effect or level > max(in_effect):
                    active.append((dates[j], level))
    below = [c for c in active if c[1] <= last_close]
    return max(below, key=lambda c: c[1]) if below else None


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
