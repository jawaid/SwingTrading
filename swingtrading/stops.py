"""ATR chandelier stop, close-based. Pure functions; positions.py applies them to picks.

Initial stop = entry price - atr_mult x ATR at the signal session.
Each session: candidate = highest close since entry - atr_mult x current ATR, and the
new stop is max(old stop, candidate), so the stop never moves down. A pick is stopped
out when a session closes below the stop in effect going into that session.
"""

from __future__ import annotations

import math

import pandas as pd

from swingtrading import indicators as ind


def initial_stop(entry_price: float, signal_atr: float, atr_mult: float) -> float:
    return entry_price - atr_mult * signal_atr


def trail_stop(old_stop: float, highest_close: float, atr: float, atr_mult: float) -> float:
    """The stop after one session. With no ATR (not enough history) it stays put."""
    if math.isnan(atr):
        return old_stop
    return max(old_stop, highest_close - atr_mult * atr)


def stopped_out(close: float, stop: float) -> bool:
    return close < stop


def atr_series(bars: pd.DataFrame, period: int) -> pd.Series:
    """ATR for one ticker's bars, sorted oldest to newest."""
    return ind.atr(bars["high"], bars["low"], bars["close"], period)
