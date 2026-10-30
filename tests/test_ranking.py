from datetime import date

import numpy as np
import pandas as pd
import pytest

from swingtrading import db, market_calendar
from swingtrading import indicators as ind
from swingtrading import ranking as r
from swingtrading.config import RankingSettings, Settings
from swingtrading.data.provider import PRICE_COLUMNS

CFG = RankingSettings()
AS_OF = date(2026, 10, 28)


def _bars(ticker: str, closes: np.ndarray, end: date = AS_OF) -> pd.DataFrame:
    sessions = market_calendar.sessions_between(date(2024, 1, 1), end)[-len(closes) :]
    return pd.DataFrame(
        {
            "ticker": ticker,
            "date": sessions,
            "open": closes,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": 1e6,
        }
    )[PRICE_COLUMNS]


def _trend(daily_pct: float, n: int = 300, seed: int = 0) -> np.ndarray:
    noise = np.random.default_rng(seed).normal(0, 0.002, n)
    return 100 * np.cumprod(1 + daily_pct / 100 + noise)


@pytest.mark.parametrize(
    ("slope", "points"),
    [(2.0, 5.0), (1.0001, 5.0), (1.0, 5.0), (0.0, 2.5), (-0.5, 1.25), (-1.0, 0.0), (-3.0, 0.0)],
)
def test_ppo_slope_points(slope, points):
    assert r.ppo_slope_points(slope, 5) == pytest.approx(points)


def test_ppo_slope_uses_three_day_change_over_three():
    close = pd.Series(_trend(0.2, n=120))
    comp = r.components(close, CFG)
    hist = ind.ppo(close)["histogram"]
    assert comp["ppo_slope"].iloc[-1] == pytest.approx((hist.iloc[-1] - hist.iloc[-4]) / 3)


def test_score_hand_calculated():
    comp = pd.DataFrame(
        [{"long_ema_pct": 15, "long_roc": 20, "mid_ema_pct": 5, "mid_roc": -4,
          "ppo_slope": 0.2, "rsi": 60}]
    )  # fmt: skip
    out = r.score(comp, CFG).iloc[0]
    assert out["long_ema_pts"] == pytest.approx(4.5)  # ChartSchool's example: 15 x 0.30
    assert out["long_roc_pts"] == pytest.approx(6.0)
    assert out["mid_ema_pts"] == pytest.approx(0.75)
    assert out["mid_roc_pts"] == pytest.approx(-0.6)
    assert out["ppo_slope_pts"] == pytest.approx(3.0)  # (0.2 + 1) x 2.5
    assert out["rsi_pts"] == pytest.approx(3.0)
    assert out["raw_score"] == pytest.approx(4.5 + 6.0 + 0.75 - 0.6 + 3.0 + 3.0)


def test_score_is_nan_until_all_components_exist():
    comp = pd.DataFrame([{c: 1.0 for c in r.COMPONENTS} | {"rsi": np.nan}])
    assert pd.isna(r.score(comp, CFG).iloc[0]["raw_score"])


def test_percentile_rank_scale_and_ties():
    raw = pd.Series([5.0, -3.0, 10.0, 5.0, 0.0])
    out = r.percentile_rank(raw)
    assert out.min() == 0 and out.max() == 99.99
    assert out[0] == out[3]  # ties share a rank
    assert r.percentile_rank(pd.Series([1.0])).tolist() == [99.99]


def test_ranking_is_monotonic_in_raw_score():
    raw = pd.Series(np.random.default_rng(1).normal(0, 10, 500))
    sctr = r.percentile_rank(raw)
    order = raw.sort_values().index
    assert sctr[order].is_monotonic_increasing
    assert sctr.between(0, 99.99).all()


def test_rank_universe_orders_trends_and_skips_short_history():
    bars = pd.concat(
        [
            _bars("UP", _trend(0.3)),
            _bars("FLAT", _trend(0.0, seed=1)),
            _bars("DOWN", _trend(-0.3, seed=2)),
            _bars("YOUNG", _trend(0.5, n=200, seed=3)),
            _bars("NOBAR", _trend(0.3, seed=4), end=date(2026, 10, 27)),
        ]
    )
    ranked = r.rank_universe(bars, CFG, AS_OF, min_history=250)
    assert ranked["ticker"].tolist() == ["UP", "FLAT", "DOWN"]
    assert ranked["sctr"].tolist() == [99.99, 50.0, 0.0]
    assert ranked["raw_score"].is_monotonic_decreasing
    up = ranked.iloc[0]
    assert up["long_ema_pct"] > 0 and up["long_roc"] > 0 and up["rsi"] > 50


def test_rank_universe_ignores_bars_after_as_of():
    bars = _bars("UP", _trend(0.3, n=301))
    as_of = bars["date"].iloc[-2]
    future = bars.copy()
    future.loc[future.index[-1], "close"] = 1.0  # a crash on the next day must not leak in
    a = r.rank_universe(bars, CFG, as_of, 250)
    b = r.rank_universe(future, CFG, as_of, 250)
    assert a["raw_score"].iloc[0] == b["raw_score"].iloc[0]


def test_shortlist_takes_top_percent():
    ranked = pd.DataFrame({"ticker": list("ABCDEFGHIJ"), "sctr": np.linspace(0, 99.99, 10)})
    assert r.shortlist(ranked, 15)["ticker"].tolist() == ["J", "I"]  # ceil(1.5) = 2
    assert r.shortlist(ranked, 1)["ticker"].tolist() == ["J"]
    assert r.shortlist(ranked.head(0), 15).empty


def test_rank_session_stores_and_replaces(tmp_path):
    from swingtrading.prices import save_bars

    conn = db.connect(tmp_path / "swing.db")
    bars = pd.concat([_bars("UP", _trend(0.3)), _bars("DOWN", _trend(-0.3, seed=2))])
    save_bars(conn, bars)
    settings = Settings()
    r.rank_session(conn, settings, ["UP", "DOWN"], AS_OF)
    r.rank_session(conn, settings, ["UP", "DOWN"], AS_OF)  # idempotent
    stored = r.load_rankings(conn, AS_OF)
    assert stored["ticker"].tolist() == ["UP", "DOWN"]
    assert stored["sctr"].tolist() == [99.99, 0.0]
    assert stored[r.POINTS].notna().all().all()
    conn.close()
