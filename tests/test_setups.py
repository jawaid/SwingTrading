import logging

import pandas as pd
import pytest

from swingtrading import setups
from swingtrading.config import Settings
from swingtrading.setups import pullback_pivot as pp
from swingtrading.setups.base import SetupResult
from tests.fixtures.synthetic import BASE_VOLUME, END, setup_frame

DETECTOR = pp.PullbackPivot(Settings().setup, Settings().stops.atr_period)


def _reason(df: pd.DataFrame) -> str | None:
    return DETECTOR.evaluate(df)[1]


def test_clean_pullback_and_pivot_triggers():
    result = DETECTOR.detect(setup_frame())
    assert isinstance(result, SetupResult)
    assert result.detector == "pullback_pivot"
    assert result.as_of == END
    assert 0 <= result.score <= 100
    d = result.details
    assert 3 <= d["depth_pct"] <= 12 and 1 <= d["depth_atr"] <= 4
    assert 1 <= d["pullback_bars"] <= 10
    assert d["down_volume_ratio"] == pytest.approx(0.7)
    assert d["pivot_volume_ratio"] >= 1 and d["close_in_range"] >= 0.5


def test_pullback_too_deep():
    df = setup_frame(pullback=[-3.0] * 6)
    assert DETECTOR.detect(df) is None
    assert _reason(df) == pp.DEPTH_PCT


def test_disorderly_high_volume_selloff():
    df = setup_frame(pullback_volume=2.5 * BASE_VOLUME)
    assert DETECTOR.detect(df) is None
    assert _reason(df) == pp.HEAVY_DOWN_VOLUME


def test_pullback_that_broke_the_50_ema():
    df = setup_frame(trend_pct=0.05, pullback=[-1.6] * 5)  # flat trend: EMA sits close
    assert DETECTOR.detect(df) is None
    assert _reason(df) == pp.BELOW_TREND_EMA


def test_no_pullback():
    df = setup_frame(pullback=[])  # uptrend straight into the "pivot" bar
    assert DETECTOR.detect(df) is None
    assert _reason(df) == pp.NO_PULLBACK


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"pullback": [-0.4, 0.2] * 6}, pp.PULLBACK_TOO_LONG),
        ({"pivot_move": 0.5}, pp.NO_BREAKOUT),
        ({"pivot_close_at_high": False}, pp.WEAK_CLOSE),
        ({"pivot_volume": 0.8 * BASE_VOLUME}, pp.LIGHT_PIVOT_VOLUME),
    ],
)
def test_other_rejections(kwargs, reason):
    assert _reason(setup_frame(**kwargs)) == reason


def test_pullback_too_shallow():
    cfg = Settings().setup.model_copy(update={"pullback_min_pct": 10.0})
    assert pp.PullbackPivot(cfg).evaluate(setup_frame())[1] == pp.DEPTH_PCT


def test_depth_in_atr_terms_is_checked():
    cfg = Settings().setup.model_copy(update={"pullback_max_atr": 2.0})
    assert pp.PullbackPivot(cfg).evaluate(setup_frame())[1] == pp.DEPTH_ATR


def test_short_history_is_rejected():
    assert _reason(setup_frame().tail(60)) == pp.NOT_ENOUGH_HISTORY


def test_parameters_come_from_settings():
    loose = Settings().setup.model_copy(update={"down_volume_max_ratio": 3.0})
    df = setup_frame(pullback_volume=2.5 * BASE_VOLUME)
    assert pp.PullbackPivot(loose).detect(df) is not None


def test_quieter_pullback_scores_higher():
    quiet = DETECTOR.detect(setup_frame(pullback_volume=0.3 * BASE_VOLUME))
    normal = DETECTOR.detect(setup_frame(pullback_volume=0.9 * BASE_VOLUME))
    assert quiet.score > normal.score


def test_only_the_last_bar_is_the_pivot():
    df = setup_frame()
    assert DETECTOR.detect(df) is not None
    assert DETECTOR.detect(df.iloc[:-1]) is None  # day before the pivot: nothing yet


def test_make_detector_and_unknown_name():
    assert setups.make_detector(Settings()).name == "pullback_pivot"
    bad = Settings().model_copy(
        update={"setup": Settings().setup.model_copy(update={"detector": "nope"})}
    )
    with pytest.raises(ValueError, match="unknown setup detector"):
        setups.make_detector(bad)


class _Boom:
    name = "boom"

    def detect(self, df):
        if df["close"].iloc[-1] < 0:
            raise RuntimeError("bad data")
        return SetupResult("boom", df["date"].iloc[-1], 50.0)


def test_scan_skips_missing_bars_and_errors(caplog):
    good = setup_frame().assign(ticker="GOOD")
    bad = setup_frame().assign(ticker="BAD")
    bad.loc[bad.index[-1], "close"] = -1.0
    stale = setup_frame().iloc[:-1].assign(ticker="STALE")  # no bar on END
    bars = pd.concat([good, bad, stale])
    with caplog.at_level(logging.INFO):
        found = setups.scan(bars, ["GOOD", "BAD", "STALE", "MISSING"], _Boom(), END)
    assert list(found) == ["GOOD"]
    assert "BAD failed" in caplog.text and "STALE has no bar" in caplog.text


def test_scan_with_real_detector_ignores_later_bars():
    df = setup_frame().assign(ticker="AAA")
    found = setups.scan(df, ["AAA"], DETECTOR, END)
    assert found["AAA"].as_of == END
    earlier = df["date"].iloc[-2]
    assert setups.scan(df, ["AAA"], DETECTOR, earlier) == {}
