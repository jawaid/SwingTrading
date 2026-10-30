"""yfinance implementations of PriceProvider and MetadataProvider.

yfinance is unofficial: downloads are batched, every call is retried with backoff,
Ticker.info calls are throttled, and one bad ticker never aborts a run.
"""

from __future__ import annotations

import logging
import threading
import time
import warnings
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pandas as pd
import yfinance as yf

from swingtrading.config import DataSettings
from swingtrading.data.provider import PRICE_COLUMNS, TickerMetadata

log = logging.getLogger(__name__)

# yfinance logs an error line per failed ticker; we report failures ourselves.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
# yfinance still uses pandas APIs that pandas 3.1 deprecates; that's yfinance's to fix.
warnings.filterwarnings("ignore", category=FutureWarning, module=r"yfinance\.")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"yfinance\.")
# By default yfinance swallows request errors and returns empty data, which would bypass
# our retries. Make it raise.
yf.config.debug.hide_exceptions = False


class EmptyResponseError(RuntimeError):
    """Yahoo returned no usable data."""


def _call_with_timeout(fn: Callable[[], Any], seconds: float) -> Any:
    """Run fn in a daemon thread; raise TimeoutError if it hangs longer than `seconds`."""
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            result["value"] = fn()
        except BaseException as exc:
            result["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise TimeoutError(f"no response within {seconds:.0f}s")
    if "error" in result:
        raise result["error"]
    return result["value"]


def to_yahoo(ticker: str) -> str:
    """NASDAQ Trader writes share classes as BRK.B; Yahoo uses BRK-B."""
    return ticker.replace(".", "-")


def _with_retries(fn: Callable[[], Any], what: str, settings: DataSettings) -> Any:
    for attempt in range(settings.max_retries + 1):
        try:
            return fn()
        except Exception as exc:  # yfinance raises a wide variety of errors
            if attempt == settings.max_retries or "404" in str(exc):  # not found: don't retry
                raise
            wait = settings.retry_backoff_seconds * 2**attempt
            if "rate" in type(exc).__name__.lower() or "Too Many Requests" in str(exc):
                wait *= 5
            log.warning("%s failed (%s: %s); retry in %.1fs", what, type(exc).__name__, exc, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def _to_long(frame: pd.DataFrame) -> pd.DataFrame:
    """Turn yfinance's (field, ticker) column frame into long PRICE_COLUMNS rows."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    parts = []
    for ticker in frame.columns.get_level_values(1).unique():
        sub = frame.xs(ticker, axis=1, level=1).rename(columns=str.lower)
        sub = sub.dropna(subset=["open", "high", "low", "close"])
        if sub.empty:
            continue
        sub = sub[["open", "high", "low", "close", "volume"]].copy()
        sub.insert(0, "ticker", ticker)
        sub.insert(1, "date", [ts.date() for ts in sub.index])
        parts.append(sub.reset_index(drop=True))
    if not parts:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    return pd.concat(parts, ignore_index=True)[PRICE_COLUMNS]


class YahooPriceProvider:
    def __init__(self, settings: DataSettings) -> None:
        self.settings = settings

    def _download(self, tickers: list[str], start: date, end: date) -> pd.DataFrame:
        return yf.download(
            [to_yahoo(t) for t in tickers],
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),  # yfinance's end is exclusive
            auto_adjust=True,
            actions=False,
            group_by="column",
            multi_level_index=True,
            threads=True,
            progress=False,
        )

    def _fetch(self, tickers: list[str], start: date, end: date, label: str) -> pd.DataFrame:
        try:
            raw = _with_retries(
                lambda: _call_with_timeout(
                    lambda: self._download(tickers, start, end),
                    self.settings.request_timeout_seconds * 4,
                ),
                label,
                self.settings,
            )
        except Exception as exc:
            log.error("%s failed after retries: %s", label, exc)
            return pd.DataFrame(columns=PRICE_COLUMNS)
        long = _to_long(raw)
        long["ticker"] = long["ticker"].map(dict(zip(map(to_yahoo, tickers), tickers, strict=True)))
        # Never return bars past the requested end (e.g. a partial current-day bar).
        return long[(long["date"] >= start) & (long["date"] <= end)]

    def history(self, tickers: list[str], start: date, end: date) -> pd.DataFrame:
        size = self.settings.batch_size
        batches = [tickers[i : i + size] for i in range(0, len(tickers), size)]
        parts = []
        for n, batch in enumerate(batches, 1):
            log.info("Price batch %d/%d (%d tickers)", n, len(batches), len(batch))
            parts.append(self._fetch(batch, start, end, f"price batch {n}"))
        got = set(pd.concat(parts)["ticker"]) if parts else set()

        # yfinance reports per-ticker failures (e.g. a network blip mid-batch) as missing
        # columns rather than errors, so ask again for whatever didn't come back.
        missing = [t for t in tickers if t not in got]
        for attempt in range(1, self.settings.max_retries + 1):
            if not missing:
                break
            log.info("Re-requesting %d tickers with no data (attempt %d)", len(missing), attempt)
            time.sleep(self.settings.retry_backoff_seconds * 2 ** (attempt - 1))
            for i in range(0, len(missing), size):
                chunk = missing[i : i + size]
                parts.append(self._fetch(chunk, start, end, f"re-request {attempt}"))
            got = set(pd.concat(parts)["ticker"])
            missing = [t for t in missing if t not in got]
        if missing:
            log.info("%d tickers have no price data", len(missing))

        parts = [p for p in parts if not p.empty]
        if not parts:
            return pd.DataFrame(columns=PRICE_COLUMNS)
        return pd.concat(parts, ignore_index=True)


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def metadata_from_info(info: dict[str, Any]) -> TickerMetadata | None:
    """Map a Ticker.info dict to TickerMetadata; None if Yahoo returned nothing useful."""
    if not info or not info.get("quoteType"):
        return None
    return TickerMetadata(
        quote_type=info.get("quoteType"),
        name=info.get("longName") or info.get("shortName"),
        sector=info.get("sector"),
        industry=info.get("industry"),
        fund_category=info.get("category"),
        market_cap=_num(info.get("marketCap")),
        total_assets=_num(info.get("totalAssets")),
        total_revenue=_num(info.get("totalRevenue")),
    )


class YahooMetadataProvider:
    def __init__(self, settings: DataSettings) -> None:
        self.settings = settings
        self._last_call = 0.0

    def _throttle(self) -> None:
        wait = self.settings.info_throttle_seconds - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _info(self, ticker: str) -> dict[str, Any]:
        self._throttle()
        return _call_with_timeout(
            lambda: yf.Ticker(to_yahoo(ticker)).info, self.settings.request_timeout_seconds
        )

    def _checked_info(self, ticker: str) -> dict[str, Any]:
        info = self._info(ticker)
        # Yahoo answers rate limits and session hiccups with an empty payload; retry those.
        if metadata_from_info(info) is None:
            raise EmptyResponseError(f"empty info for {ticker}")
        return info

    def metadata(self, ticker: str) -> TickerMetadata | None:
        try:
            info = _with_retries(
                lambda: self._checked_info(ticker), f"info {ticker}", self.settings
            )
        except Exception as exc:
            log.warning("Metadata for %s unavailable: %s", ticker, exc)
            return None
        return metadata_from_info(info)
