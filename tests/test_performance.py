"""Performance math against small, hand-calculated trades."""

import math
from datetime import date

import pandas as pd
import pytest

from swingtrading import performance as perf

D0, D1, D2, D3, D4 = (date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30),
                      date(2026, 10, 1), date(2026, 10, 2))  # fmt: skip


def test_trade_stats_by_hand():
    s = perf.trade_stats([0.10, -0.05, 0.20, -0.10], [3, 5, 4, 2])
    assert (s.trades, s.wins, s.losses) == (4, 2, 2)
    assert s.win_rate == pytest.approx(0.5)
    assert s.avg_win == pytest.approx(0.15)  # (0.10 + 0.20) / 2
    assert s.avg_loss == pytest.approx(-0.075)  # (-0.05 - 0.10) / 2
    assert s.profit_factor == pytest.approx(2.0)  # 0.30 / 0.15
    assert s.expectancy == pytest.approx(0.0375)  # 0.5 * 0.15 + 0.5 * -0.075
    assert s.avg_days_held == pytest.approx(3.5)


def test_trade_stats_edges():
    assert perf.trade_stats([], []) is None
    only_wins = perf.trade_stats([0.1, 0.2], [1, 1])
    assert only_wins.profit_factor == math.inf and only_wins.avg_loss is None
    flat = perf.trade_stats([0.0], [2])
    assert flat.losses == 1 and flat.win_rate == 0 and flat.profit_factor is None


# A: entry D1 at 100, closes 110 (+10%) and 99 (-10%), exits D3 at the 99 open (0%).
# B: entry D2 at 50, closes 60 (+20%) and 60 (0%), then 45 on D4 (-25%), still open.
PICKS = [
    {"ticker": "A", "entry_date": D1.isoformat(), "entry_price": 100.0,
     "exit_date": D3.isoformat(), "exit_price": 99.0},
    {"ticker": "B", "entry_date": D2.isoformat(), "entry_price": 50.0,
     "exit_date": None, "exit_price": None},
    {"ticker": "C", "entry_date": None, "entry_price": None,  # signal only: ignored
     "exit_date": None, "exit_price": None},
]  # fmt: skip
CLOSES = pd.DataFrame(
    {"A": [100.0, 110.0, 99.0, 98.0, 97.0], "B": [48.0, 49.0, 60.0, 60.0, 45.0],
     "C": [10.0] * 5},
    index=[D0, D1, D2, D3, D4],
)  # fmt: skip


def test_daily_returns_by_hand():
    r = perf.daily_returns(PICKS, CLOSES)
    assert list(r.index) == [D0, D1, D2, D3, D4]
    # D0 start; D1 A +10%; D2 mean(A -10%, B +20%); D3 mean(A exit 0%, B 0%); D4 B -25%
    assert list(r) == pytest.approx([0.0, 0.10, 0.05, 0.0, -0.25])


def test_equity_drawdown_and_months():
    eq = perf.equity_curve(perf.daily_returns(PICKS, CLOSES))
    assert list(eq) == pytest.approx([1.0, 1.1, 1.155, 1.155, 0.86625])
    assert perf.max_drawdown(eq) == pytest.approx(-0.25)  # 1.155 -> 0.86625
    months = perf.monthly_returns(eq)
    assert list(months.index) == ["2026-09", "2026-10"]
    assert list(months) == pytest.approx([0.155, -0.25])  # 1 -> 1.155 -> 0.86625


def test_gap_in_holdings_earns_nothing():
    picks = [
        {"ticker": "A", "entry_date": D1.isoformat(), "entry_price": 100.0,
         "exit_date": D2.isoformat(), "exit_price": 120.0},
        {"ticker": "B", "entry_date": D4.isoformat(), "entry_price": 50.0,
         "exit_date": None, "exit_price": None},
    ]  # fmt: skip
    r = perf.daily_returns(picks, CLOSES)
    # D1 +10%, D2 exit 120 / 110 - 1, D3 nothing held, D4 B 45 / 50 - 1
    assert list(r) == pytest.approx([0.0, 0.10, 120 / 110 - 1, 0.0, -0.10])


def test_missing_close_is_carried_forward():
    closes = CLOSES.copy()
    closes.loc[D3, "B"] = float("nan")  # no bar that day: hold the last close
    r = perf.daily_returns(PICKS, closes)
    assert list(r) == pytest.approx([0.0, 0.10, 0.05, 0.0, -0.25])


def test_benchmark_over_the_same_sessions():
    spy = pd.Series([400.0, 404.0, 402.0, 410.0, 420.0], index=[D0, D1, D2, D3, D4])
    eq = perf.equity_curve(perf.daily_returns(PICKS, CLOSES))
    b = perf.benchmark_curve(spy, eq.index)
    assert list(b) == pytest.approx([1.0, 1.01, 1.005, 1.025, 1.05])
    assert list(perf.monthly_returns(b)) == pytest.approx([0.005, 1.05 / 1.005 - 1])


def test_nothing_entered_gives_empty_curve():
    assert perf.daily_returns(PICKS[2:], CLOSES).empty
    assert perf.max_drawdown(pd.Series(dtype=float)) == 0.0
    assert perf.monthly_returns(pd.Series(dtype=float)).empty


def test_starting_point_gets_no_month_of_its_own():
    curve = pd.Series([1.0, 1.1, 1.21], index=[date(2026, 7, 31), date(2026, 8, 3), D3])
    months = perf.monthly_returns(curve)
    assert list(months.index) == ["2026-08", "2026-10"]
    assert list(months) == pytest.approx([0.1, 0.1])
