from datetime import date
from pathlib import Path

from swingtrading.data import symbols

FIXTURES = Path(__file__).parent / "fixtures"


def _texts() -> dict[str, str]:
    return {name: (FIXTURES / name).read_text() for name in symbols.FILES}


def test_parse_nasdaq_listed():
    rows = symbols.parse_nasdaq_listed(_texts()["nasdaqlisted.txt"])
    by_ticker = {r.ticker: r for r in rows}
    assert "File Creation Time: 1028202618:01" not in by_ticker
    assert by_ticker["AAPL"].exchange == "Q"
    assert by_ticker["QQQ"].is_etf
    assert not by_ticker["AAPL"].is_etf
    assert by_ticker["ZXZZT"].test_issue


def test_parse_other_listed():
    rows = symbols.parse_other_listed(_texts()["otherlisted.txt"])
    by_ticker = {r.ticker: r for r in rows}
    assert by_ticker["SPY"].exchange == "P"
    assert by_ticker["SPY"].is_etf
    assert by_ticker["O"].exchange == "N"
    assert by_ticker["ABR$D"].name.endswith("Preferred Stock")


def test_load_symbols_dedupes_preferring_nasdaq():
    rows = symbols.load_symbols(_texts())
    tickers = [r.ticker for r in rows]
    assert len(tickers) == len(set(tickers))
    aapl = next(r for r in rows if r.ticker == "AAPL")
    assert aapl.exchange == "Q"


def test_fetch_symbol_files_downloads_once_per_day(tmp_path):
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return (FIXTURES / url.rsplit("/", 1)[1]).read_text()

    day = date(2026, 10, 28)
    first = symbols.fetch_symbol_files(tmp_path, day, fetch=fake_fetch)
    second = symbols.fetch_symbol_files(tmp_path, day, fetch=fake_fetch)
    assert first == second == _texts()
    assert len(calls) == 2  # one per file, not repeated
    symbols.fetch_symbol_files(tmp_path, date(2026, 10, 29), fetch=fake_fetch)
    assert len(calls) == 4
