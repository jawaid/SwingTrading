import math

import numpy as np
import pandas as pd
import pytest

from swingtrading import indicators as ind

# StockCharts ChartSchool worked example: 10-day EMA.
EMA_CLOSES = [22.27, 22.19, 22.08, 22.17, 22.18, 22.13, 22.23, 22.43, 22.24, 22.29,
              22.15, 22.39, 22.38, 22.61, 23.36, 24.05, 23.75, 23.83, 23.95, 23.63]  # fmt: skip
EMA_10 = [22.22, 22.21, 22.24, 22.27, 22.33, 22.52, 22.80, 22.97, 23.13, 23.28, 23.34]

# StockCharts ChartSchool worked example: 14-day RSI.
RSI_CLOSES = [44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245,
              45.8433, 46.0826, 45.8931, 46.0328, 45.6140, 46.2820, 46.2820, 46.0028,
              46.0328, 46.4116, 46.2222, 45.6439, 46.2122]  # fmt: skip
RSI_14 = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93]


def test_ema_matches_stockcharts_example():
    out = ind.ema(pd.Series(EMA_CLOSES), 10)
    assert out.iloc[:9].isna().all()
    assert out.dropna().round(2).tolist() == pytest.approx(EMA_10, abs=0.005)


def test_rsi_matches_stockcharts_example():
    out = ind.rsi(pd.Series(RSI_CLOSES), 14)
    assert out.iloc[:14].isna().all()
    assert out.dropna().round(2).tolist() == pytest.approx(RSI_14, abs=0.005)


def test_rsi_edge_cases():
    assert ind.rsi(pd.Series(np.arange(1.0, 31.0)), 14).dropna().eq(100).all()
    assert ind.rsi(pd.Series(np.arange(30.0, 0.0, -1)), 14).dropna().eq(0).all()
    assert ind.rsi(pd.Series([10.0] * 30), 14).dropna().eq(50).all()


def test_ema_seeds_with_sma_and_skips_leading_nans():
    s = pd.Series([np.nan, np.nan, 1.0, 2.0, 3.0, 4.0])
    out = ind.ema(s, 3)
    assert out.iloc[:4].isna().all()
    assert out.iloc[4] == pytest.approx(2.0)  # SMA of 1, 2, 3
    assert out.iloc[5] == pytest.approx(2.0 + 0.5 * (4.0 - 2.0))  # multiplier 2/(3+1)


def test_too_short_series_is_all_nan():
    assert ind.ema(pd.Series([1.0, 2.0]), 3).isna().all()


def test_wilder_smoothing():
    out = ind.wilder(pd.Series([1.0, 2.0, 3.0, 7.0]), 3)
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[3] == pytest.approx((2.0 * 2 + 7.0) / 3)


def test_roc_and_pct_from():
    close = pd.Series([100.0, 105.0, 110.0, 99.0])
    assert ind.roc(close, 2).tolist()[2:] == pytest.approx([10.0, 99 / 105 * 100 - 100])
    assert ind.pct_from(pd.Series([115.0]), pd.Series([100.0])).iloc[0] == pytest.approx(15.0)


def test_true_range_and_atr_hand_calculated():
    high = pd.Series([10.0, 11.0, 12.5, 11.0])
    low = pd.Series([9.0, 10.0, 11.5, 9.0])
    close = pd.Series([9.5, 10.5, 12.0, 9.5])
    # TR: first bar H-L; then max(H-L, |H-Cprev|, |L-Cprev|).
    assert ind.true_range(high, low, close).tolist() == [1.0, 1.5, 2.0, 3.0]
    out = ind.atr(high, low, close, 3)
    assert out.iloc[2] == pytest.approx(1.5)  # SMA of 1, 1.5, 2
    assert out.iloc[3] == pytest.approx((1.5 * 2 + 3.0) / 3)


def test_ppo_definition():
    rng = np.random.default_rng(0)
    close = pd.Series(100 + rng.normal(0, 1, 120).cumsum())
    out = ind.ppo(close, 12, 26, 9)
    e12, e26 = ind.ema(close, 12), ind.ema(close, 26)
    expected = (e12 - e26) / e26 * 100
    pd.testing.assert_series_equal(out["ppo"], expected, check_names=False)
    pd.testing.assert_series_equal(
        out["histogram"], out["ppo"] - ind.ema(out["ppo"], 9), check_names=False
    )
    assert out["histogram"].first_valid_index() == 25 + 8  # slow EMA, then signal EMA


def test_ppo_is_zero_for_flat_prices_and_positive_in_uptrend():
    assert ind.ppo(pd.Series([50.0] * 60))["ppo"].dropna().abs().max() == 0
    assert (ind.ppo(pd.Series(np.linspace(50, 100, 60)))["ppo"].dropna() > 0).all()


def test_linreg_slope():
    s = pd.Series([1.0, 2.0, 4.0, 4.0, 1.0])
    out = ind.linreg_slope(s, 3)
    assert out.iloc[:2].isna().all()
    assert out.iloc[2:].tolist() == pytest.approx([1.5, 1.0, -1.5])  # (y2 - y0) / 2
    y = np.array([3.0, 1.0, 4.0, 1.0, 5.0])
    assert ind.linreg_slope(pd.Series(y), 5).iloc[-1] == pytest.approx(
        np.polyfit(range(5), y, 1)[0]
    )
    with pytest.raises(ValueError):
        ind.linreg_slope(s, 1)


def test_indicators_are_pure():
    close = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    before = close.copy()
    ind.ema(close, 3), ind.rsi(close, 3), ind.ppo(close, 2, 3, 2), ind.linreg_slope(close)
    pd.testing.assert_series_equal(close, before)
    assert not math.isnan(ind.ema(close, 3).iloc[-1])


def test_change_slope_is_rise_over_run():
    hist = pd.Series([0.3, -0.6, 0.9, 1.2, 0.0])
    out = ind.change_slope(hist, 3)
    assert out.iloc[:3].isna().all()
    assert out.iloc[3:].tolist() == pytest.approx([(1.2 - 0.3) / 3, (0.0 + 0.6) / 3])
    with pytest.raises(ValueError):
        ind.change_slope(hist, 0)
