"""Technical indicators as pure functions on pandas Series.

Conventions follow StockCharts ChartSchool: EMAs and Wilder averages are seeded with a
simple average of the first `period` values, and every output is NaN until enough data
exists. Inputs must be sorted oldest to newest.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _seeded_ewm(values: pd.Series, period: int, alpha: float) -> pd.Series:
    """Exponential average seeded with the SMA of the first `period` non-NaN values."""
    out = pd.Series(np.nan, index=values.index, dtype=float)
    valid = values.dropna()
    if len(valid) < period:
        return out
    seeded = valid.iloc[period - 1 :].copy()
    seeded.iloc[0] = valid.iloc[:period].mean()
    out.loc[seeded.index] = seeded.ewm(alpha=alpha, adjust=False).mean()
    return out


def ema(values: pd.Series, period: int) -> pd.Series:
    """Exponential moving average, multiplier 2 / (period + 1)."""
    return _seeded_ewm(values, period, 2 / (period + 1))


def wilder(values: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing (used by RSI and ATR), multiplier 1 / period."""
    return _seeded_ewm(values, period, 1 / period)


def pct_from(values: pd.Series, reference: pd.Series) -> pd.Series:
    """Percent above (+) or below (-) a reference line, e.g. an EMA."""
    return (values / reference - 1) * 100


def roc(close: pd.Series, period: int) -> pd.Series:
    """Rate of change in percent over `period` bars."""
    return (close / close.shift(period) - 1) * 100


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI, 0-100."""
    change = close.diff()
    avg_gain = wilder(change.clip(lower=0), period)
    avg_loss = wilder((-change).clip(lower=0), period)
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    # No losses at all: RSI is 100 (rs is inf, which the formula already gives),
    # no gains and no losses: undefined, call it 50.
    return out.mask((avg_gain == 0) & (avg_loss == 0), 50.0)


def ppo(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """Percentage Price Oscillator: columns ppo, signal, histogram (all in percent)."""
    slow_ema = ema(close, slow)
    line = (ema(close, fast) - slow_ema) / slow_ema * 100
    signal_line = ema(line, signal)
    return pd.DataFrame(
        {"ppo": line, "signal": signal_line, "histogram": line - signal_line}, index=close.index
    )


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    ranges = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1)
    return ranges.max(axis=1, skipna=True)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's Average True Range."""
    return wilder(true_range(high, low, close), period)


def change_slope(values: pd.Series, period: int = 3) -> pd.Series:
    """Rise over run: change over `period` bars divided by `period` (ChartSchool's slope)."""
    if period < 1:
        raise ValueError("period must be at least 1")
    return (values - values.shift(period)) / period


def linreg_slope(values: pd.Series, points: int = 3) -> pd.Series:
    """Slope per bar of the least-squares line through the last `points` values."""
    if points < 2:
        raise ValueError("points must be at least 2")
    x = np.arange(points, dtype=float)
    weights = (x - x.mean()) / ((x - x.mean()) ** 2).sum()
    # weights[i] applies to the value (points - 1 - i) bars ago.
    return sum(w * values.shift(points - 1 - i) for i, w in enumerate(weights))
