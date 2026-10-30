"""StockCharts Technical Rank (SCTR): raw score per ticker, then a 0-99.99 percentile rank.

Raw score (weights from settings, StockCharts defaults shown):
  long term  60%: % above/below 200-day EMA (30%), 125-day ROC (30%)
  mid term   30%: % above/below 50-day EMA (15%), 20-day ROC (15%)
  short term 10%: 3-day slope of the PPO(12,26,9) histogram (5%), 14-day RSI (5%)
Each component contributes value x weight, except the PPO histogram slope:
  slope > 1 -> full weight, slope < -1 -> 0, otherwise (slope + 1) / 2 x weight.
The SCTR is the raw score's percentile rank across the whole universe that day.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from datetime import date

import pandas as pd

from swingtrading import indicators as ind
from swingtrading import market_calendar
from swingtrading.config import RankingSettings, Settings
from swingtrading.db import transaction
from swingtrading.prices import load_bars

log = logging.getLogger(__name__)

COMPONENTS = ["long_ema_pct", "long_roc", "mid_ema_pct", "mid_roc", "ppo_slope", "rsi"]
POINTS = ["long_ema_pts", "long_roc_pts", "mid_ema_pts", "mid_roc_pts", "ppo_slope_pts", "rsi_pts"]
SCTR_MAX = 99.99


def components(close: pd.Series, cfg: RankingSettings) -> pd.DataFrame:
    """The six raw SCTR inputs for every date of one ticker's close series."""
    hist = ind.ppo(close, cfg.ppo_fast, cfg.ppo_slow, cfg.ppo_signal)["histogram"]
    return pd.DataFrame(
        {
            "long_ema_pct": ind.pct_from(close, ind.ema(close, cfg.long_ema_period)),
            "long_roc": ind.roc(close, cfg.long_roc_period),
            "mid_ema_pct": ind.pct_from(close, ind.ema(close, cfg.mid_ema_period)),
            "mid_roc": ind.roc(close, cfg.mid_roc_period),
            "ppo_slope": ind.linreg_slope(hist, cfg.ppo_slope_period),
            "rsi": ind.rsi(close, cfg.rsi_period),
        },
        index=close.index,
    )


def ppo_slope_points(slope: float, weight: float) -> float:
    if slope > 1:
        return weight
    if slope < -1:
        return 0.0
    return (slope + 1) / 2 * weight


def score(comp: pd.DataFrame, cfg: RankingSettings) -> pd.DataFrame:
    """Add per-component points and raw_score to a frame of components."""
    w = cfg.weights
    out = comp.copy()
    out["long_ema_pts"] = comp["long_ema_pct"] * w.long_ema / 100
    out["long_roc_pts"] = comp["long_roc"] * w.long_roc / 100
    out["mid_ema_pts"] = comp["mid_ema_pct"] * w.mid_ema / 100
    out["mid_roc_pts"] = comp["mid_roc"] * w.mid_roc / 100
    out["ppo_slope_pts"] = comp["ppo_slope"].map(
        lambda s: math.nan if pd.isna(s) else ppo_slope_points(s, w.ppo_slope)
    )
    out["rsi_pts"] = comp["rsi"] * w.rsi / 100
    out["raw_score"] = out[POINTS].sum(axis=1, skipna=False)
    return out


def percentile_rank(raw: pd.Series) -> pd.Series:
    """Map raw scores to 0-99.99: lowest gets 0, highest 99.99, ties share a rank."""
    if raw.empty:
        return raw.astype(float)
    if len(raw) == 1:
        return pd.Series(SCTR_MAX, index=raw.index)
    ranks = raw.rank(method="average")
    return ((ranks - 1) / (len(raw) - 1) * SCTR_MAX).round(2)


def rank_universe(
    bars: pd.DataFrame, cfg: RankingSettings, as_of: date, min_history: int
) -> pd.DataFrame:
    """Rank every ticker with at least `min_history` bars through as_of.

    `bars` is a long price frame. Returns one row per ranked ticker with components,
    points, raw_score and sctr, sorted best first.
    """
    rows = []
    for ticker, df in bars[bars["date"] <= as_of].groupby("ticker", sort=False):
        df = df.sort_values("date")
        if len(df) < min_history or df["date"].iloc[-1] != as_of:
            continue  # too little history, or no bar for this session
        close = df["close"].reset_index(drop=True)
        last = score(components(close, cfg), cfg).iloc[-1]
        if pd.isna(last["raw_score"]):
            continue
        rows.append({"ticker": ticker, **last.to_dict()})
    if not rows:
        return pd.DataFrame(columns=["ticker", *COMPONENTS, *POINTS, "raw_score", "sctr"])
    ranked = pd.DataFrame(rows)
    ranked["sctr"] = percentile_rank(ranked["raw_score"])
    return ranked.sort_values(["sctr", "ticker"], ascending=[False, True], ignore_index=True)


def shortlist(ranked: pd.DataFrame, pct: float) -> pd.DataFrame:
    """The top `pct` percent of ranked names by SCTR (at least one if any are ranked)."""
    if ranked.empty:
        return ranked
    n = max(1, math.ceil(len(ranked) * pct / 100))
    return ranked.sort_values("sctr", ascending=False).head(n)


def save_rankings(conn: sqlite3.Connection, as_of: date, ranked: pd.DataFrame) -> int:
    """Replace the stored rankings for as_of."""
    cols = ["sctr", "raw_score", *COMPONENTS, *POINTS]
    with transaction(conn):
        conn.execute("DELETE FROM rankings WHERE date = ?", (as_of.isoformat(),))
        conn.executemany(
            f"INSERT INTO rankings (date, ticker, {', '.join(cols)}) "
            f"VALUES (?, ?, {', '.join('?' * len(cols))})",
            [
                (as_of.isoformat(), r["ticker"], *(float(r[c]) for c in cols))
                for _, r in ranked.iterrows()
            ],
        )
    return len(ranked)


def load_rankings(conn: sqlite3.Connection, as_of: date) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM rankings WHERE date = ? ORDER BY sctr DESC, ticker",
        conn,
        params=(as_of.isoformat(),),
    )


def rank_session(
    conn: sqlite3.Connection, settings: Settings, tickers: list[str], as_of: date
) -> pd.DataFrame:
    """Load stored bars, rank `tickers` for as_of, store and return the result."""
    # Enough bars for the longest EMA to settle; history beyond this barely moves it.
    lookback = max(settings.universe.min_history_sessions, settings.data.backfill_sessions)
    start = market_calendar.previous_session(as_of, lookback)
    bars = load_bars(conn, tickers, start=start, end=as_of)
    ranked = rank_universe(bars, settings.ranking, as_of, settings.universe.min_history_sessions)
    skipped = len(tickers) - len(ranked)
    if skipped:
        log.info("Ranking %s: %d of %d tickers skipped (history or no bar)", as_of, skipped,
                 len(tickers))  # fmt: skip
    save_rankings(conn, as_of, ranked)
    log.info("Ranked %d tickers for %s", len(ranked), as_of)
    return ranked
