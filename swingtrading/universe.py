"""Build the tradable universe: filters and exclusions from the domain rules.

Every symbol from the NASDAQ Trader files gets a row in `symbols`. Included symbols have
excluded_reason NULL; every dropped symbol records why, so the universe can be audited.

Order: cheap checks on the symbol files, then a batch price download for the dollar-volume
and history prefilter, and only then per-ticker metadata (Ticker.info) for the survivors.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from swingtrading import market_calendar
from swingtrading.config import Settings, UniverseSettings
from swingtrading.data.provider import MetadataProvider, PriceProvider, TickerMetadata
from swingtrading.data.symbols import SymbolRow
from swingtrading.db import transaction

log = logging.getLogger(__name__)

# Exclusion reasons stored in symbols.excluded_reason.
TEST_ISSUE = "test_issue"
EXCHANGE = "exchange_not_allowed"
SPECIAL_SYMBOL = "preferred_warrant_unit_right_symbol"
SPECIAL_NAME = "preferred_warrant_unit_right_name"
ADR = "adr"
LEVERAGED_ETF = "leveraged_inverse_etf"
NO_PRICES = "no_price_data"
SHORT_HISTORY = "short_history"
LOW_DOLLAR_VOLUME = "low_dollar_volume"
NO_METADATA = "metadata_unavailable"
REIT = "reit"
SPAC = "spac"
SMALL_MARKET_CAP = "market_cap_below_min"
SMALL_ETF_ASSETS = "etf_assets_below_min"
NOT_LISTED = "not_listed"

_SPECIAL_SYMBOL_CHARS = set("$.-+=^")
_SPECIAL_NAME = re.compile(r"\b(?:preferred|pfd|warrants?|rights?|units?)\b", re.IGNORECASE)
# Units that are ordinary equity (MLPs, LPs, royalty trusts), not SPAC units; removed
# before the special-name check.
_EQUITY_UNITS = re.compile(
    r"\b(?:common|partnership|depositary)\s+units?\b"
    r"|\bunits?\s+(?:representing|of\s+beneficial\s+interest)\b",
    re.IGNORECASE,
)
_ADR = re.compile(r"American Depositary|\bADRs?\b|\bADSs?\b")
# Plain bond funds whose names contain "short" / "ultra-short"; removed before the
# leveraged-name check so they are not mistaken for inverse ETFs.
_SHORT_DURATION = re.compile(
    r"(?:ultra[- ]?)?short[- ]?(?:term|duration|maturity|treasury|bond|income|government"
    r"|municipal|muni|corporate|high yield)",
    re.IGNORECASE,
)
_LEVERAGED_NAME = re.compile(
    r"(?<![\w.])(?:[2-9](?:\.\d+)?|-\d(?:\.\d+)?)x\b"
    r"|\bUltra(?:Pro)?(?:Short)?\b|\bBull\b|\bBear\b|\bInverse\b|\bShort\b",
    re.IGNORECASE,
)
_LEVERAGED_CATEGORY = re.compile(r"leveraged|inverse|trading", re.IGNORECASE)
_SPAC_NAME = re.compile(r"\bAcquisition\s+(?:Corp|Corporation|Company|Co|Inc|Ltd)\b", re.I)


# ---- pure rule functions --------------------------------------------------------------


def is_leveraged_etf_name(name: str) -> bool:
    return bool(_LEVERAGED_NAME.search(_SHORT_DURATION.sub(" ", name)))


def symbol_exclusion(row: SymbolRow, exchanges: Iterable[str]) -> str | None:
    """Checks that need only the symbol files."""
    if row.test_issue:
        return TEST_ISSUE
    if row.exchange not in set(exchanges):
        return EXCHANGE
    if _SPECIAL_SYMBOL_CHARS & set(row.ticker):
        return SPECIAL_SYMBOL
    if _ADR.search(row.name):
        return ADR
    if not row.is_etf and _SPECIAL_NAME.search(_EQUITY_UNITS.sub(" ", row.name)):
        return SPECIAL_NAME
    if row.is_etf and is_leveraged_etf_name(row.name):
        return LEVERAGED_ETF
    return None


def price_stats(prices: pd.DataFrame, period: int) -> pd.DataFrame:
    """Per ticker: sessions of history and average dollar volume over the last `period` bars."""
    if prices.empty:
        return pd.DataFrame(columns=["sessions", "avg_dollar_volume"])
    p = prices.sort_values(["ticker", "date"])
    p = p.assign(dollar_volume=p["close"] * p["volume"])
    grouped = p.groupby("ticker")
    return pd.DataFrame(
        {
            "sessions": grouped.size(),
            "avg_dollar_volume": grouped["dollar_volume"].apply(lambda s: s.tail(period).mean()),
        }
    )


def liquidity_exclusion(
    sessions: int | None, avg_dollar_volume: float | None, rules: UniverseSettings
) -> str | None:
    if not sessions:
        return NO_PRICES
    if sessions < rules.min_history_sessions:
        return SHORT_HISTORY
    if avg_dollar_volume is None or avg_dollar_volume < rules.min_avg_dollar_volume:
        return LOW_DOLLAR_VOLUME
    return None


def metadata_exclusion(
    row: SymbolRow, meta: TickerMetadata | None, rules: UniverseSettings
) -> str | None:
    """Checks that need Yahoo metadata."""
    if meta is None:
        return NO_METADATA
    if row.is_etf or meta.quote_type == "ETF":
        if meta.fund_category and _LEVERAGED_CATEGORY.search(meta.fund_category):
            return LEVERAGED_ETF
        if meta.name and is_leveraged_etf_name(meta.name):
            return LEVERAGED_ETF
        if (meta.total_assets or 0) < rules.min_etf_total_assets:
            return SMALL_ETF_ASSETS
        return None
    if (meta.industry or "").startswith("REIT"):
        return REIT
    if meta.industry == "Shell Companies":
        return SPAC
    if _SPAC_NAME.search(f"{row.name} {meta.name or ''}") and not meta.total_revenue:
        return SPAC
    if (meta.market_cap or 0) < rules.min_market_cap:
        return SMALL_MARKET_CAP
    return None


# ---- database ---------------------------------------------------------------------------


def _cached_metadata(conn: sqlite3.Connection, fresh_after: str) -> dict[str, TickerMetadata]:
    rows = conn.execute(
        """SELECT ticker, quote_type, name, sector, industry, fund_category, market_cap,
                  total_assets, total_revenue
           FROM symbols WHERE metadata_updated_at >= ?""",
        (fresh_after,),
    ).fetchall()
    return {
        r["ticker"]: TickerMetadata(
            quote_type=r["quote_type"],
            name=r["name"],
            sector=r["sector"],
            industry=r["industry"],
            fund_category=r["fund_category"],
            market_cap=r["market_cap"],
            total_assets=r["total_assets"],
            total_revenue=r["total_revenue"],
        )
        for r in rows
    }


def _save_metadata(conn: sqlite3.Connection, row: SymbolRow, meta: TickerMetadata, now: str):
    conn.execute(
        """INSERT INTO symbols (ticker, name, exchange, is_etf, quote_type, sector, industry,
               fund_category, market_cap, total_assets, total_revenue, metadata_updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (ticker) DO UPDATE SET
               quote_type = excluded.quote_type, sector = excluded.sector,
               industry = excluded.industry, fund_category = excluded.fund_category,
               market_cap = excluded.market_cap, total_assets = excluded.total_assets,
               total_revenue = excluded.total_revenue,
               metadata_updated_at = excluded.metadata_updated_at""",
        (
            row.ticker, row.name, row.exchange, int(row.is_etf), meta.quote_type, meta.sector,
            meta.industry, meta.fund_category, meta.market_cap, meta.total_assets,
            meta.total_revenue, now,
        ),
    )  # fmt: skip


def universe_tickers(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT ticker FROM symbols WHERE excluded_reason IS NULL ORDER BY ticker"
    ).fetchall()
    return [r["ticker"] for r in rows]


# ---- orchestration ---------------------------------------------------------------------


def refresh_universe(
    conn: sqlite3.Connection,
    settings: Settings,
    rows: list[SymbolRow],
    prices: PriceProvider,
    metadata: MetadataProvider,
    as_of: date,
    now: datetime | None = None,
) -> Counter[str]:
    """Rebuild `symbols`. Returns counts by exclusion reason ("included" for kept symbols)."""
    rules = settings.universe
    now = now or datetime.now(UTC)
    now_iso = now.isoformat(timespec="seconds")
    reasons: dict[str, str | None] = {}

    # 1. Symbol-file checks.
    for row in rows:
        reasons[row.ticker] = symbol_exclusion(row, rules.exchanges)
    candidates = [r for r in rows if reasons[r.ticker] is None]
    log.info("%d symbols, %d pass symbol checks", len(rows), len(candidates))

    # 2. Batch prices: history length and dollar volume, before any Ticker.info call.
    lookback = max(rules.min_history_sessions, rules.dollar_volume_period) + 10
    start = market_calendar.previous_session(as_of, lookback)
    bars = prices.history([r.ticker for r in candidates], start, as_of)
    stats = price_stats(bars, rules.dollar_volume_period)
    for row in candidates:
        s = stats.loc[row.ticker] if row.ticker in stats.index else None
        sessions = int(s["sessions"]) if s is not None else None
        adv = float(s["avg_dollar_volume"]) if s is not None else None
        reasons[row.ticker] = liquidity_exclusion(sessions, adv, rules)
    survivors = [r for r in candidates if reasons[r.ticker] is None]
    log.info("%d pass the history and dollar-volume prefilter", len(survivors))

    # 3. Metadata for survivors only, cached for metadata_refresh_days.
    fresh_after = (now - timedelta(days=rules.metadata_refresh_days)).isoformat()
    cache = _cached_metadata(conn, fresh_after)
    to_fetch = [r for r in survivors if r.ticker not in cache]
    log.info("Metadata: %d cached, %d to fetch", len(survivors) - len(to_fetch), len(to_fetch))
    for i, row in enumerate(to_fetch, 1):
        meta = metadata.metadata(row.ticker)
        if meta is not None:
            cache[row.ticker] = meta
            _save_metadata(conn, row, meta, now_iso)
        if i % 50 == 0:
            conn.commit()
            log.info("Metadata %d/%d", i, len(to_fetch))
    conn.commit()
    for row in survivors:
        reasons[row.ticker] = metadata_exclusion(row, cache.get(row.ticker), rules)

    # 4. Write every symbol with its reason; anything no longer listed is marked.
    with transaction(conn):
        for row in rows:
            s = stats.loc[row.ticker] if row.ticker in stats.index else None
            meta = cache.get(row.ticker)
            conn.execute(
                """INSERT INTO symbols (ticker, name, exchange, is_etf, excluded_reason,
                       avg_dollar_volume, history_sessions, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (ticker) DO UPDATE SET
                       name = excluded.name, exchange = excluded.exchange,
                       is_etf = excluded.is_etf, excluded_reason = excluded.excluded_reason,
                       avg_dollar_volume = excluded.avg_dollar_volume,
                       history_sessions = excluded.history_sessions,
                       updated_at = excluded.updated_at""",
                (
                    row.ticker, row.name, row.exchange,
                    int(row.is_etf or (meta is not None and meta.quote_type == "ETF")),
                    reasons[row.ticker],
                    None if s is None else float(s["avg_dollar_volume"]),
                    None if s is None else int(s["sessions"]),
                    now_iso,
                ),
            )  # fmt: skip
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS listed (ticker TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM listed")
        conn.executemany("INSERT INTO listed VALUES (?)", [(r.ticker,) for r in rows])
        conn.execute(
            "UPDATE symbols SET excluded_reason = ?, updated_at = ? "
            "WHERE ticker NOT IN (SELECT ticker FROM listed)",
            (NOT_LISTED, now_iso),
        )

    counts = Counter(reason or "included" for reason in reasons.values())
    log.info("Universe: %d included", counts["included"])
    return counts
