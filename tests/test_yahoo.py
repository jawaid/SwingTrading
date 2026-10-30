from datetime import date

import numpy as np
import pandas as pd
import pytest

from swingtrading.config import DataSettings
from swingtrading.data import yahoo


def _yf_frame(tickers, dates):
    fields = ["Close", "High", "Low", "Open", "Volume"]
    cols = pd.MultiIndex.from_product([fields, tickers], names=["Price", "Ticker"])
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in dates], name="Date")
    return pd.DataFrame(np.arange(len(idx) * len(cols), dtype=float).reshape(len(idx), -1),
                        index=idx, columns=cols)  # fmt: skip


def test_to_long_drops_empty_tickers_and_rows():
    frame = _yf_frame(["AAA", "BAD"], ["2026-10-27", "2026-10-28"])
    frame.loc[:, (slice(None), "BAD")] = np.nan
    frame.loc[pd.Timestamp("2026-10-28"), ("Close", "AAA")] = np.nan
    long = yahoo._to_long(frame)
    assert list(long.columns) == ["ticker", "date", "open", "high", "low", "close", "volume"]
    assert long["ticker"].tolist() == ["AAA"]
    assert long["date"].tolist() == [date(2026, 10, 27)]


def test_history_batches_and_trims_to_end(monkeypatch):
    settings = DataSettings(batch_size=2, max_retries=0)
    provider = yahoo.YahooPriceProvider(settings)
    seen = []

    def fake_download(tickers, start, end):
        seen.append(list(tickers))
        # Includes a bar after `end`, like a partial current-day bar.
        return _yf_frame(tickers, ["2026-10-27", "2026-10-28", "2026-10-29"])

    monkeypatch.setattr(provider, "_download", fake_download)
    out = provider.history(["A", "B", "C"], date(2026, 10, 27), date(2026, 10, 28))
    assert seen == [["A", "B"], ["C"]]
    assert out["date"].max() == date(2026, 10, 28)
    assert sorted(out["ticker"].unique()) == ["A", "B", "C"]


def test_failed_batch_does_not_abort(monkeypatch):
    provider = yahoo.YahooPriceProvider(DataSettings(batch_size=1, max_retries=1,
                                                     retry_backoff_seconds=0))  # fmt: skip
    calls = []

    def flaky(tickers, start, end):
        calls.append(tickers[0])
        if tickers[0] == "BAD":
            raise RuntimeError("boom")
        return _yf_frame(tickers, ["2026-10-28"])

    monkeypatch.setattr(provider, "_download", flaky)
    out = provider.history(["BAD", "GOOD"], date(2026, 10, 28), date(2026, 10, 28))
    # Retried once, skipped, then re-requested once more (and retried) after the batches.
    assert calls == ["BAD", "BAD", "GOOD", "BAD", "BAD"]
    assert out["ticker"].tolist() == ["GOOD"]


def test_metadata_from_info():
    meta = yahoo.metadata_from_info(
        {
            "quoteType": "ETF",
            "longName": "ProShares UltraPro Short QQQ",
            "category": "Trading--Inverse Equity",
            "totalAssets": 2105422336,
            "marketCap": None,
        }
    )
    assert meta.quote_type == "ETF"
    assert meta.fund_category == "Trading--Inverse Equity"
    assert meta.total_assets == pytest.approx(2.105e9, rel=1e-3)
    assert meta.market_cap is None
    assert yahoo.metadata_from_info({}) is None
    assert yahoo.metadata_from_info({"trailingPegRatio": None}) is None


def test_metadata_retries_then_returns_none(monkeypatch):
    provider = yahoo.YahooMetadataProvider(
        DataSettings(max_retries=2, retry_backoff_seconds=0, info_throttle_seconds=0)
    )
    calls = []

    def boom(ticker):
        calls.append(ticker)
        raise RuntimeError("connection reset")

    monkeypatch.setattr(provider, "_info", boom)
    assert provider.metadata("XYZ") is None
    assert len(calls) == 3


def test_empty_info_is_retried(monkeypatch):
    provider = yahoo.YahooMetadataProvider(
        DataSettings(max_retries=3, retry_backoff_seconds=0, info_throttle_seconds=0)
    )
    responses = [{}, {}, {"quoteType": "EQUITY", "marketCap": 3e9}]
    monkeypatch.setattr(provider, "_info", lambda ticker: responses.pop(0))
    meta = provider.metadata("AAPL")
    assert meta.market_cap == 3e9
    assert responses == []


def test_not_found_is_not_retried(monkeypatch):
    provider = yahoo.YahooMetadataProvider(
        DataSettings(max_retries=3, retry_backoff_seconds=0, info_throttle_seconds=0)
    )
    calls = []

    def not_found(ticker):
        calls.append(ticker)
        raise RuntimeError("HTTP Error 404: ")

    monkeypatch.setattr(provider, "_info", not_found)
    assert provider.metadata("ZZZZ") is None
    assert calls == ["ZZZZ"]


def test_call_with_timeout():
    import threading

    assert yahoo._call_with_timeout(lambda: 42, 1) == 42
    with pytest.raises(ValueError):
        yahoo._call_with_timeout(lambda: int("x"), 1)
    release = threading.Event()
    with pytest.raises(TimeoutError):
        yahoo._call_with_timeout(lambda: release.wait(5), 0.05)
    release.set()


def test_share_classes_use_yahoo_symbols(monkeypatch):
    provider = yahoo.YahooPriceProvider(DataSettings(max_retries=0))

    def fake_download(tickers, start, end):
        # yfinance answers with Yahoo's spelling of the symbol.
        return _yf_frame([yahoo.to_yahoo(t) for t in tickers], ["2026-10-28"])

    monkeypatch.setattr(provider, "_download", fake_download)
    out = provider.history(["BRK.B", "AAPL"], date(2026, 10, 28), date(2026, 10, 28))
    assert sorted(out["ticker"]) == ["AAPL", "BRK.B"]
    assert yahoo.to_yahoo("BRK.B") == "BRK-B"


def test_tickers_missing_from_a_batch_are_requested_again(monkeypatch):
    provider = yahoo.YahooPriceProvider(
        DataSettings(batch_size=3, max_retries=2, retry_backoff_seconds=0)
    )
    requests = []

    def flaky(tickers, start, end):
        requests.append(list(tickers))
        # First call: B silently fails, DEAD never has data.
        ok = [t for t in tickers if t != "DEAD" and not (t == "B" and len(requests) == 1)]
        return _yf_frame(ok, ["2026-10-28"]) if ok else pd.DataFrame()

    monkeypatch.setattr(provider, "_download", flaky)
    out = provider.history(["A", "B", "DEAD"], date(2026, 10, 28), date(2026, 10, 28))
    assert sorted(out["ticker"]) == ["A", "B"]
    assert requests == [["A", "B", "DEAD"], ["B", "DEAD"], ["DEAD"]]
