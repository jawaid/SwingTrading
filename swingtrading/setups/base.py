"""Setup detector interface, result type, registry and the shortlist scan."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

import pandas as pd

from swingtrading.config import Settings

log = logging.getLogger(__name__)

# Columns a detector may rely on; rows sorted oldest to newest, last row = signal session.
BAR_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class SetupResult:
    """A setup found on the last bar of the frame handed to a detector."""

    detector: str
    as_of: date
    score: float  # 0-100, higher is a cleaner setup
    details: dict[str, Any] = field(default_factory=dict)  # shown in the app


class SetupDetector(Protocol):
    name: str

    def detect(self, df: pd.DataFrame) -> SetupResult | None:
        """Return a SetupResult if the last bar of `df` completes a setup, else None."""
        ...


def _pullback_pivot(settings: Settings) -> SetupDetector:
    from swingtrading.setups.pullback_pivot import PullbackPivot

    return PullbackPivot(settings.setup, settings.stops.atr_period)


DETECTORS: dict[str, Callable[[Settings], SetupDetector]] = {
    "pullback_pivot": _pullback_pivot,
}


def make_detector(settings: Settings) -> SetupDetector:
    """The detector named by `setup.detector` in settings."""
    try:
        return DETECTORS[settings.setup.detector](settings)
    except KeyError:
        raise ValueError(
            f"unknown setup detector {settings.setup.detector!r}; choose from {sorted(DETECTORS)}"
        ) from None


def scan(
    bars: pd.DataFrame,
    tickers: Iterable[str],
    detector: SetupDetector,
    as_of: date,
    errors: list[str] | None = None,
) -> dict[str, SetupResult]:
    """Run `detector` on each ticker's bars through as_of. Tickers with no bar on as_of
    are skipped, and a detector error on one ticker is logged (and appended to `errors`)
    without stopping the scan."""
    found: dict[str, SetupResult] = {}
    groups = dict(tuple(bars[bars["date"] <= as_of].groupby("ticker", sort=False)))
    for ticker in tickers:
        df = groups.get(ticker)
        if df is None or df["date"].max() != as_of:
            log.info("Setup scan %s: %s has no bar for this session, skipped", as_of, ticker)
            continue
        try:
            result = detector.detect(df.sort_values("date")[BAR_COLUMNS].reset_index(drop=True))
        except Exception:
            log.exception("Setup scan %s: %s failed, skipped", as_of, ticker)
            if errors is not None:
                errors.append(ticker)
            continue
        if result is not None:
            found[ticker] = result
    return found
