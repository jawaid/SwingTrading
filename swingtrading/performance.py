"""Trade statistics, the equal-weighted equity curve of picks, and the SPY comparison.

Returns are fractions (0.05 = +5%). All functions are pure: the app loads picks and
closes from the database and passes them in.

Equity curve: every session, the portfolio holds each pick that is open that day in
equal weight, so the day's return is the average of the held picks' returns, and the
curve compounds those daily returns. A pick's return on its entry day runs from the entry
price (the open) to the close; on its exit day, from the prior close to the exit price
(the open). A day with nothing held earns nothing. Still-open picks count through the
latest close.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import pandas as pd

BENCHMARK = "SPY"


@dataclass(frozen=True)
class TradeStats:
    trades: int
    wins: int
    losses: int
    win_rate: float  # fraction of trades with a positive return
    avg_win: float | None
    avg_loss: float | None  # negative, or None with no losers
    profit_factor: float | None  # gross wins / gross losses; inf with no losers
    expectancy: float  # average return per trade
    avg_days_held: float


def trade_stats(returns: Iterable[float], days_held: Iterable[float]) -> TradeStats | None:
    """Stats for closed trades; None when there are none. A 0% trade counts as a loss."""
    r = pd.Series(list(returns), dtype=float)
    if r.empty:
        return None
    wins, losses = r[r > 0], r[r <= 0]
    gross_loss = -losses.sum()
    if gross_loss > 0:
        profit_factor: float | None = wins.sum() / gross_loss
    else:
        profit_factor = math.inf if len(wins) else None
    win_rate = len(wins) / len(r)
    return TradeStats(
        trades=len(r),
        wins=len(wins),
        losses=len(losses),
        win_rate=win_rate,
        avg_win=float(wins.mean()) if len(wins) else None,
        avg_loss=float(losses.mean()) if len(losses) else None,
        profit_factor=profit_factor,
        expectancy=float(r.mean()),
        avg_days_held=float(pd.Series(list(days_held), dtype=float).mean()),
    )


def _pick_returns(pick: dict, closes: pd.Series) -> pd.Series:
    """One pick's daily returns over the sessions it was held. `closes` is that ticker's
    close by session, forward-filled over the whole period."""
    entry_day = date.fromisoformat(pick["entry_date"])
    exit_day = date.fromisoformat(pick["exit_date"]) if pick.get("exit_date") else None
    held = closes[(closes.index >= entry_day) & ((exit_day is None) | (closes.index <= exit_day))]
    prev = closes.shift(1).reindex(held.index)
    out = held / prev - 1
    if entry_day in out.index:
        out[entry_day] = held[entry_day] / pick["entry_price"] - 1
    if exit_day is not None and exit_day in out.index:
        out[exit_day] = pick["exit_price"] / prev[exit_day] - 1
    return out


def daily_returns(picks: list[dict], closes: pd.DataFrame) -> pd.Series:
    """Equal-weighted daily returns of entered picks, one per session in `closes` from the
    session before the first entry (0, the starting point) to the last session.

    picks: dicts with ticker, entry_date, entry_price, exit_date, exit_price (ISO dates;
    exit fields None while open). closes: close by session (index of dates) and ticker."""
    entered = [p for p in picks if p.get("entry_date")]
    if not entered or closes.empty:
        return pd.Series(dtype=float)
    closes = closes.sort_index().ffill()
    first = min(date.fromisoformat(p["entry_date"]) for p in entered)
    before = closes.index[closes.index < first]
    start = before[-1] if len(before) else first
    sessions = closes.index[closes.index >= start]
    per_pick = pd.DataFrame(
        {i: _pick_returns(p, closes[p["ticker"]]) for i, p in enumerate(entered)},
        index=sessions,
    )
    return per_pick.mean(axis=1).fillna(0.0).rename("return")


def equity_curve(daily: pd.Series) -> pd.Series:
    """Growth of 1 compounded from daily returns."""
    return (1 + daily).cumprod().rename("equity")


def max_drawdown(equity: pd.Series) -> float:
    """Largest peak-to-trough fall as a negative fraction (0 if it never fell)."""
    if equity.empty:
        return 0.0
    return float((equity / equity.cummax() - 1).min())


def benchmark_curve(closes: pd.Series, sessions: pd.Index) -> pd.Series:
    """Growth of 1 in the benchmark over the same sessions as the equity curve."""
    c = closes.sort_index().ffill().reindex(sessions).ffill()
    return (c / c.iloc[0]).rename(BENCHMARK)


def monthly_returns(curve: pd.Series) -> pd.Series:
    """Compounded return per calendar month ('YYYY-MM') of a growth-of-1 curve. The first
    point is only the starting value, so it doesn't get a month of its own."""
    if len(curve) < 2:
        return pd.Series(dtype=float)
    month = pd.Index([d.strftime("%Y-%m") for d in curve.index[1:]])
    ends = curve.iloc[1:].groupby(month).last()
    starts = ends.shift(1)
    starts.iloc[0] = curve.iloc[0]
    return ends / starts - 1
