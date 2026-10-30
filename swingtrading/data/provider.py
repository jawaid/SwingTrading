"""Abstract data interfaces. The pipeline depends on these, never on yfinance directly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd

PRICE_COLUMNS = ["ticker", "date", "open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class TickerMetadata:
    quote_type: str | None = None  # "EQUITY", "ETF", ...
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    fund_category: str | None = None
    market_cap: float | None = None
    total_assets: float | None = None
    total_revenue: float | None = None


class PriceProvider(Protocol):
    def history(self, tickers: list[str], start: date, end: date) -> pd.DataFrame:
        """Daily adjusted bars for sessions start..end inclusive.

        Returns a long frame with PRICE_COLUMNS; `date` holds datetime.date values.
        Tickers with no data are simply absent.
        """
        ...


class MetadataProvider(Protocol):
    def metadata(self, ticker: str) -> TickerMetadata | None:
        """Metadata for one ticker, or None if it could not be fetched."""
        ...
