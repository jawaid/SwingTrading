"""Download and parse the NASDAQ Trader symbol directory."""

from __future__ import annotations

import csv
import io
import logging
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

BASE_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/"
FILES = ("nasdaqlisted.txt", "otherlisted.txt")
NASDAQ_EXCHANGE = "Q"


@dataclass(frozen=True)
class SymbolRow:
    ticker: str
    name: str
    exchange: str  # Q = NASDAQ, N = NYSE, A = NYSE American, P = NYSE Arca, ...
    is_etf: bool
    test_issue: bool


def _rows(text: str) -> list[dict[str, str]]:
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("File Creation Time")]
    return list(csv.DictReader(io.StringIO("\n".join(lines)), delimiter="|"))


def parse_nasdaq_listed(text: str) -> list[SymbolRow]:
    return [
        SymbolRow(
            ticker=r["Symbol"].strip(),
            name=(r["Security Name"] or "").strip(),
            exchange=NASDAQ_EXCHANGE,
            is_etf=r["ETF"].strip() == "Y",
            test_issue=r["Test Issue"].strip() == "Y",
        )
        for r in _rows(text)
        if r.get("Symbol")
    ]


def parse_other_listed(text: str) -> list[SymbolRow]:
    return [
        SymbolRow(
            ticker=r["ACT Symbol"].strip(),
            name=(r["Security Name"] or "").strip(),
            exchange=r["Exchange"].strip(),
            is_etf=r["ETF"].strip() == "Y",
            test_issue=r["Test Issue"].strip() == "Y",
        )
        for r in _rows(text)
        if r.get("ACT Symbol")
    ]


def _http_get(url: str, retries: int = 3, backoff: float = 2.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 SwingTrading"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except OSError as exc:
            if attempt == retries:
                raise
            wait = backoff * 2**attempt
            log.warning("Download of %s failed (%s); retrying in %.0fs", url, exc, wait)
            time.sleep(wait)
    raise AssertionError("unreachable")


def fetch_symbol_files(
    cache_dir: Path, today: date, fetch: Callable[[str], str] = _http_get
) -> dict[str, str]:
    """Return {filename: text}, downloading at most once per day into cache_dir/symbols/."""
    folder = cache_dir / "symbols" / today.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    texts = {}
    for name in FILES:
        path = folder / name
        if not path.exists():
            log.info("Downloading %s", name)
            path.write_text(fetch(BASE_URL + name))
        texts[name] = path.read_text()
    return texts


def load_symbols(texts: dict[str, str]) -> list[SymbolRow]:
    """Combine both files; NASDAQ-listed wins if a ticker appears twice."""
    rows = parse_nasdaq_listed(texts["nasdaqlisted.txt"]) + parse_other_listed(
        texts["otherlisted.txt"]
    )
    seen: dict[str, SymbolRow] = {}
    for row in rows:
        seen.setdefault(row.ticker, row)
    return list(seen.values())
