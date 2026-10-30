from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from swingtrading import db, market_calendar
from swingtrading import universe as u
from swingtrading.config import Settings
from swingtrading.data import symbols
from swingtrading.data.provider import PRICE_COLUMNS, TickerMetadata
from swingtrading.data.symbols import SymbolRow

FIXTURES = Path(__file__).parent / "fixtures"
AS_OF = date(2026, 10, 28)
NOW = datetime(2026, 10, 28, 22, 0, tzinfo=UTC)

LIQUID = (300, 100.0, 1_000_000)  # sessions, close, volume -> $100M/day

# Synthetic price history: (sessions available, close, volume).
PRICES = {
    "AAPL": LIQUID,
    "QQQ": LIQUID,
    "SPY": LIQUID,
    "JPST": LIQUID,
    "VCSH": LIQUID,
    "LEVCAT": LIQUID,
    "PFFD": LIQUID,
    "SMLETF": LIQUID,
    "SPCX": LIQUID,
    "O": LIQUID,
    "EPD": LIQUID,
    "BRK.B": LIQUID,
    "SMALL": LIQUID,
    "NOINFO": LIQUID,
    "TINY": (300, 5.0, 100_000),  # $0.5M/day
    "NEWCO": (120, 100.0, 1_000_000),  # too little history
    # SQQQ, TQQQ, etc. have no prices on purpose: they must be dropped before download.
}

STOCK = TickerMetadata(quote_type="EQUITY", industry="Software", market_cap=50e9, total_revenue=1e9)
ETF = TickerMetadata(quote_type="ETF", fund_category="Large Blend", total_assets=50e9)

METADATA = {
    "AAPL": STOCK,
    "QQQ": ETF,
    "SPY": ETF,
    "JPST": TickerMetadata(quote_type="ETF", fund_category="Ultrashort Bond", total_assets=40e9),
    "VCSH": TickerMetadata(quote_type="ETF", fund_category="Short-Term Bond", total_assets=40e9),
    "LEVCAT": TickerMetadata(
        quote_type="ETF", fund_category="Trading--Leveraged Equity", total_assets=10e9
    ),
    "PFFD": TickerMetadata(quote_type="ETF", fund_category="Preferred Stock", total_assets=3e9),
    "SMLETF": TickerMetadata(quote_type="ETF", fund_category="Large Blend", total_assets=5e8),
    "SPCX": TickerMetadata(quote_type="EQUITY", industry="Shell Companies", market_cap=3e9),
    "O": TickerMetadata(quote_type="EQUITY", industry="REIT - Retail", market_cap=50e9),
    "EPD": TickerMetadata(quote_type="EQUITY", industry="Oil & Gas Midstream", market_cap=60e9),
    "BRK.B": TickerMetadata(quote_type="EQUITY", industry="Insurance", market_cap=1e12),
    "SMALL": TickerMetadata(quote_type="EQUITY", industry="Software", market_cap=1e9),
    # NOINFO: Yahoo returns nothing
}

EXPECTED = {
    "AAPL": None,
    "QQQ": None,
    "SPY": None,
    "JPST": None,
    "VCSH": None,
    "PFFD": None,
    "EPD": None,
    "TQQQ": u.LEVERAGED_ETF,
    "SQQQ": u.LEVERAGED_ETF,
    "SDS": u.LEVERAGED_ETF,
    "SOXL": u.LEVERAGED_ETF,
    "LEVCAT": u.LEVERAGED_ETF,
    "BIDU": u.ADR,
    "SPCXU": u.SPECIAL_NAME,
    "SPCXW": u.SPECIAL_NAME,
    "SPCXR": u.SPECIAL_NAME,
    "SPCX": u.SPAC,
    "ZXZZT": u.TEST_ISSUE,
    "TINY": u.LOW_DOLLAR_VOLUME,
    "NEWCO": u.SHORT_HISTORY,
    "SMALL": u.SMALL_MARKET_CAP,
    "SMLETF": u.SMALL_ETF_ASSETS,
    "BZXETF": u.EXCHANGE,
    "ABR$D": u.SPECIAL_SYMBOL,
    "BRK.B": None,
    "O": u.REIT,
    "NOINFO": u.NO_METADATA,
}


class FakePrices:
    def __init__(self, spec):
        self.spec = spec
        self.requested: list[str] = []

    def history(self, tickers, start, end):
        self.requested.extend(tickers)
        sessions = market_calendar.sessions_between(start, end)
        rows = []
        for t in tickers:
            if t not in self.spec:
                continue
            n, close, volume = self.spec[t]
            for d in sessions[-n:]:
                rows.append((t, d, close, close, close, close, volume))
        return pd.DataFrame(rows, columns=PRICE_COLUMNS)


class FakeMetadata:
    def __init__(self, data):
        self.data = data
        self.calls: list[str] = []

    def metadata(self, ticker):
        self.calls.append(ticker)
        return self.data.get(ticker)


@pytest.fixture
def rows():
    texts = {name: (FIXTURES / name).read_text() for name in symbols.FILES}
    return symbols.load_symbols(texts)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "swing.db")
    yield c
    c.close()


def _reasons(conn):
    return {
        r["ticker"]: r["excluded_reason"]
        for r in conn.execute("SELECT ticker, excluded_reason FROM symbols")
    }


def _refresh(conn, rows, now=NOW, meta=None, prices=None):
    meta = meta or FakeMetadata(METADATA)
    prices = prices or FakePrices(PRICES)
    counts = u.refresh_universe(conn, Settings(), rows, prices, meta, AS_OF, now)
    return counts, prices, meta


def test_every_symbol_gets_the_expected_reason(conn, rows):
    counts, _, _ = _refresh(conn, rows)
    assert _reasons(conn) == EXPECTED
    assert counts["included"] == 8
    assert u.universe_tickers(conn) == sorted(t for t, r in EXPECTED.items() if r is None)


def test_cheap_checks_run_before_downloads_and_metadata(conn, rows):
    _, prices, meta = _refresh(conn, rows)
    symbol_dropped = {"TQQQ", "SQQQ", "SDS", "SOXL", "BIDU", "SPCXU", "SPCXW", "SPCXR"}
    symbol_dropped |= {"ZXZZT", "BZXETF", "ABR$D"}
    assert not symbol_dropped & set(prices.requested)
    # Ticker.info is only called for names that passed the dollar-volume prefilter.
    assert not {"TINY", "NEWCO"} & set(meta.calls)
    assert set(meta.calls) == {t for t in PRICES if t not in {"TINY", "NEWCO"}}


def test_metadata_is_cached_for_a_week(conn, rows):
    _refresh(conn, rows)
    _, _, meta = _refresh(conn, rows, now=NOW + timedelta(days=3))
    assert meta.calls == ["NOINFO"]  # failures are not cached
    assert _reasons(conn) == EXPECTED

    _, _, meta = _refresh(conn, rows, now=NOW + timedelta(days=8))
    assert "AAPL" in meta.calls


def test_delisted_symbols_are_marked(conn, rows):
    _refresh(conn, rows)
    _refresh(conn, [r for r in rows if r.ticker != "AAPL"])
    assert _reasons(conn)["AAPL"] == u.NOT_LISTED


def test_stats_are_stored(conn, rows):
    _refresh(conn, rows)
    row = conn.execute(
        "SELECT avg_dollar_volume, history_sessions, market_cap FROM symbols WHERE ticker='AAPL'"
    ).fetchone()
    assert row["avg_dollar_volume"] == pytest.approx(100e6)
    assert row["history_sessions"] == 261  # lookback window: 260 prior sessions + as_of
    assert row["market_cap"] == 50e9


def _row(ticker="ABC", name="ABC Inc Common Stock", exchange="N", is_etf=False):
    return SymbolRow(ticker=ticker, name=name, exchange=exchange, is_etf=is_etf, test_issue=False)


@pytest.mark.parametrize(
    "name",
    [
        "Foo Corp 5.25% Series A Preferred Stock",
        "Foo Acquisition Corp - Warrant",
        "Foo Acquisition Corp - Units",
        "Foo Acquisition Corp - Right",
    ],
)
def test_special_securities_by_name(name):
    assert u.symbol_exclusion(_row(name=name), ["N"]) == u.SPECIAL_NAME


@pytest.mark.parametrize(
    "name",
    [
        "Energy Transfer LP Common Units representing limited partner interests",
        "Brookfield Example Limited Partnership Units",
        "Icahn Enterprises L.P. - Depositary Units representing Limited Partner Interests",
        "PermRock Royalty Trust Units of Beneficial Interest",
        "United Rentals, Inc. Common Stock",
        "Bright Horizons Family Solutions Inc. Common Stock",
    ],
)
def test_ordinary_names_are_kept(name):
    assert u.symbol_exclusion(_row(name=name), ["N"]) is None


@pytest.mark.parametrize(
    "ticker", ["ABC$A", "ABC.WS", "ABC.W", "ABC.U", "ABC.R", "ABC-U", "ABC+", "ABC=", "ABC^"]
)
def test_special_symbol_characters(ticker):
    assert u.symbol_exclusion(_row(ticker=ticker), ["N"]) == u.SPECIAL_SYMBOL


@pytest.mark.parametrize("ticker", ["BRK.B", "BF.A", "MKC.V", "CIG.C"])
def test_share_classes_are_kept(ticker):
    assert u.symbol_exclusion(_row(ticker=ticker), ["N"]) is None


@pytest.mark.parametrize(
    ("name", "leveraged"),
    [
        ("ProShares Ultra S&P500", True),
        ("ProShares UltraPro QQQ", True),
        ("ProShares UltraShort 20+ Year Treasury", True),
        ("Direxion Daily Semiconductor Bear 3X Shares", True),
        ("Direxion Daily Financial Bull 2x Shares", True),
        ("Example -1x Short VIX Futures ETF", True),
        ("Example 1.5x Long NVDA Daily ETF", False),  # 1.5x is not caught by the 2x+ pattern
        ("ProShares Short S&P500", True),
        ("Example Inverse Treasury ETF", True),
        ("JPMorgan Ultra-Short Income ETF", False),
        ("Vanguard Short-Term Corporate Bond ETF", False),
        ("iShares Short Treasury Bond ETF", False),
        ("PIMCO Enhanced Short Maturity Active ETF", False),
        ("SPDR S&P 500 ETF Trust", False),
    ],
)
def test_leveraged_etf_names(name, leveraged):
    assert u.is_leveraged_etf_name(name) is leveraged


def test_adr_patterns():
    assert u.symbol_exclusion(_row(name="Foo Ltd ADR"), ["N"]) == u.ADR
    assert u.symbol_exclusion(_row(name="Foo plc American Depositary Shares"), ["N"]) == u.ADR
    assert u.symbol_exclusion(_row(name="Foo Ltd Ordinary Shares"), ["N"]) is None
    # "the right to receive" in an ADR description is not a rights issue.
    adr = "Foo S.A. American Depositary Shares (each representing the right to receive 10 Units)"
    assert u.symbol_exclusion(_row(name=adr), ["N"]) == u.ADR


def test_spac_by_name_needs_no_revenue():
    rules = Settings().universe
    row = _row(name="Foo Acquisition Corp Class A Ordinary Shares")
    no_revenue = TickerMetadata(quote_type="EQUITY", industry="Software", market_cap=3e9)
    with_revenue = TickerMetadata(
        quote_type="EQUITY", industry="Software", market_cap=3e9, total_revenue=5e8
    )
    assert u.metadata_exclusion(row, no_revenue, rules) == u.SPAC
    assert u.metadata_exclusion(row, with_revenue, rules) is None


def test_liquidity_boundaries():
    rules = Settings().universe
    assert u.liquidity_exclusion(250, 20e6, rules) is None
    assert u.liquidity_exclusion(249, 1e9, rules) == u.SHORT_HISTORY
    assert u.liquidity_exclusion(300, 19.9e6, rules) == u.LOW_DOLLAR_VOLUME
    assert u.liquidity_exclusion(None, None, rules) == u.NO_PRICES


def test_price_stats_uses_last_period_bars():
    rows = [("X", date(2026, 1, i), 1, 1, 1, 10.0, 1000 if i <= 5 else 3000) for i in range(1, 11)]
    prices = pd.DataFrame(rows, columns=PRICE_COLUMNS)
    stats = u.price_stats(prices, period=5)
    assert stats.loc["X", "sessions"] == 10
    assert stats.loc["X", "avg_dollar_volume"] == 30_000
