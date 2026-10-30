"""PLACEHOLDER setup detector: pullback and pivot.

!!! This is a stand-in formula. The owner will replace it with their own. !!!
Swap it by writing another SetupDetector and naming it in `setup.detector`.

The last bar of the frame is the candidate pivot session ("today"). Rules, with every
threshold from the `setup` section of settings.yaml:

1. Swing high: the highest high of the `swing_high_lookback` sessions before today.
2. Pullback: the sessions after the swing high up to yesterday; at least one and at most
   `max_pullback_sessions`. Its low is the lowest low from the swing high through today.
3. Depth from the swing high to that low: `pullback_min_pct`-`pullback_max_pct` percent,
   and `pullback_min_atr`-`pullback_max_atr` ATRs (ATR as of the swing high).
4. Orderly: average volume on the pullback's down days (close below the prior close) is
   below `down_volume_max_ratio` x the `volume_avg_period`-day average volume as of the
   swing high, and no close from the swing high through today is below the
   `trend_ema_period`-day EMA.
5. Pivot: today's close is above yesterday's high, in the top part of today's range
   (`pivot_close_range_min`, 0.5 = upper half), on volume of at least
   `pivot_volume_min_ratio` x the average of the prior `pivot_volume_period` sessions.

Setup score, 0-100, four equal parts: depth near the middle of the allowed ATR band,
quiet down-day volume, a strong pivot volume (2x the minimum earns full marks), and a
close near the top of the day's range.
"""

from __future__ import annotations

import pandas as pd

from swingtrading import indicators as ind
from swingtrading.config import SetupSettings
from swingtrading.setups.base import SetupResult

NAME = "pullback_pivot"

# Rejection reasons, in the order the rules are checked.
NOT_ENOUGH_HISTORY = "not_enough_history"
NO_PULLBACK = "no_pullback"
PULLBACK_TOO_LONG = "pullback_too_long"
DEPTH_PCT = "depth_pct_out_of_range"
DEPTH_ATR = "depth_atr_out_of_range"
HEAVY_DOWN_VOLUME = "heavy_down_volume"
BELOW_TREND_EMA = "close_below_trend_ema"
NO_BREAKOUT = "close_not_above_prior_high"
WEAK_CLOSE = "close_low_in_range"
LIGHT_PIVOT_VOLUME = "pivot_volume_too_light"


def _clip01(x: float) -> float:
    return min(1.0, max(0.0, x))


class PullbackPivot:
    name = NAME

    def __init__(self, cfg: SetupSettings, atr_period: int = 14) -> None:
        self.cfg = cfg
        self.atr_period = atr_period

    def min_bars(self) -> int:
        c = self.cfg
        longest = max(
            c.volume_avg_period, c.trend_ema_period, c.pivot_volume_period, self.atr_period + 1
        )
        return longest + c.swing_high_lookback + 1

    def detect(self, df: pd.DataFrame) -> SetupResult | None:
        result, _ = self.evaluate(df)
        return result

    def evaluate(self, df: pd.DataFrame) -> tuple[SetupResult | None, str | None]:
        """The setup, or None and the first rule it failed."""
        c = self.cfg
        if len(df) < self.min_bars():
            return None, NOT_ENOUGH_HISTORY
        df = df.reset_index(drop=True)
        high, low, close, volume = df["high"], df["low"], df["close"], df["volume"]
        t = len(df) - 1

        window = high.iloc[t - c.swing_high_lookback : t]
        h = int(window[window == window.max()].index[-1])  # latest bar at the high
        bars = t - 1 - h
        if bars < 1:
            return None, NO_PULLBACK
        if bars > c.max_pullback_sessions:
            return None, PULLBACK_TOO_LONG

        swing_high = float(high.iloc[h])
        pullback_low = float(low.iloc[h + 1 : t + 1].min())
        atr = float(ind.atr(high, low, close, self.atr_period).iloc[h])
        depth_pct = (swing_high - pullback_low) / swing_high * 100
        depth_atr = (swing_high - pullback_low) / atr
        if not c.pullback_min_pct <= depth_pct <= c.pullback_max_pct:
            return None, DEPTH_PCT
        if not c.pullback_min_atr <= depth_atr <= c.pullback_max_atr:
            return None, DEPTH_ATR

        avg_volume = float(volume.iloc[h - c.volume_avg_period + 1 : h + 1].mean())
        pullback = df.iloc[h + 1 : t]
        down = pullback[pullback["close"] < close.shift(1).iloc[h + 1 : t]]
        down_ratio = float(down["volume"].mean() / avg_volume) if len(down) else 0.0
        if down_ratio >= c.down_volume_max_ratio:
            return None, HEAVY_DOWN_VOLUME
        trend = ind.ema(close, c.trend_ema_period)
        if (close.iloc[h + 1 : t + 1] < trend.iloc[h + 1 : t + 1]).any():
            return None, BELOW_TREND_EMA

        today = df.iloc[t]
        if today["close"] <= high.iloc[t - 1]:
            return None, NO_BREAKOUT
        day_range = today["high"] - today["low"]
        close_pos = (today["close"] - today["low"]) / day_range if day_range > 0 else 0.0
        if close_pos < c.pivot_close_range_min:
            return None, WEAK_CLOSE
        pivot_avg = float(volume.iloc[t - c.pivot_volume_period : t].mean())
        pivot_ratio = float(today["volume"] / pivot_avg)
        if pivot_ratio < c.pivot_volume_min_ratio:
            return None, LIGHT_PIVOT_VOLUME

        mid = (c.pullback_min_atr + c.pullback_max_atr) / 2
        half = (c.pullback_max_atr - c.pullback_min_atr) / 2
        parts = {
            "depth": 1 - abs(depth_atr - mid) / half,
            "down_volume": 1 - down_ratio / c.down_volume_max_ratio,
            "pivot_volume": (pivot_ratio - c.pivot_volume_min_ratio)
            / max(c.pivot_volume_min_ratio, 1e-9),
            "close": (close_pos - c.pivot_close_range_min) / max(1 - c.pivot_close_range_min, 1e-9),
        }
        score = round(float(sum(25 * _clip01(v) for v in parts.values())), 1)
        details = {
            "swing_high_date": df["date"].iloc[h].isoformat(),
            "swing_high": round(swing_high, 4),
            "pullback_low": round(pullback_low, 4),
            "depth_pct": round(depth_pct, 2),
            "depth_atr": round(depth_atr, 2),
            "pullback_bars": bars,
            "down_volume_ratio": round(down_ratio, 2),
            "pivot_volume_ratio": round(pivot_ratio, 2),
            "close_in_range": round(float(close_pos), 2),
            "atr": round(atr, 4),
        }
        return SetupResult(NAME, df["date"].iloc[t], score, details), None
