"""Synthetic OHLCV builders for setup tests. Deterministic, no network."""

from __future__ import annotations

from datetime import date

import pandas as pd

from swingtrading import market_calendar

END = date(2026, 10, 28)
BASE_VOLUME = 1_000_000.0


def bar_frame(
    moves: list[float], volumes: list[float], end: date = END, spread: float = 0.015
) -> pd.DataFrame:
    """Bars from daily close-to-close % moves. Each bar opens at the prior close and its
    high/low sit `spread` beyond the body, so the true range is a few percent."""
    rows, prev = [], 100.0
    for move, vol in zip(moves, volumes, strict=True):
        close = prev * (1 + move / 100)
        rows.append(
            {
                "open": prev,
                "high": max(prev, close) * (1 + spread),
                "low": min(prev, close) * (1 - spread),
                "close": close,
                "volume": vol,
            }
        )
        prev = close
    df = pd.DataFrame(rows)
    df.insert(0, "date", market_calendar.sessions_between(date(2024, 1, 1), end)[-len(df) :])
    return df


def _uptrend(n: int, daily_pct: float) -> list[float]:
    # Small alternating wiggle so it is not a perfectly straight line.
    return [daily_pct + (0.3 if i % 2 else -0.3) for i in range(n)]


def setup_frame(
    trend_pct: float = 0.4,
    pullback: list[float] | None = None,
    pullback_volume: float = 0.7 * BASE_VOLUME,
    pivot_move: float = 3.0,
    pivot_volume: float = 1.6 * BASE_VOLUME,
    pivot_close_at_high: bool = True,
) -> pd.DataFrame:
    """An uptrend, then a pullback, then a pivot bar as the last row."""
    pullback = [-1.2] * 5 if pullback is None else pullback
    trend = _uptrend(120, trend_pct)
    moves = [*trend, *pullback, pivot_move]
    volumes = [BASE_VOLUME] * len(trend) + [pullback_volume] * len(pullback) + [pivot_volume]
    df = bar_frame(moves, volumes)
    last = df.index[-1]
    if pivot_close_at_high:  # close near the high of the day
        df.loc[last, "high"] = df.loc[last, "close"] * 1.002
    else:  # long upper wick: close in the lower part of the range
        df.loc[last, "high"] = df.loc[last, "close"] * 1.06
    return df
