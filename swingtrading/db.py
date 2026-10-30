"""SQLite schema, migrations and query helpers. All database access goes through here."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

PICK_STATUSES = ("signal", "open", "exit_pending", "closed")
ACTIVE_PICK_STATUSES = ("signal", "open", "exit_pending")

# Each migration runs once, in order. The schema version is PRAGMA user_version.
# Never edit a migration that has shipped; append a new one instead.
MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE symbols (
        ticker          TEXT PRIMARY KEY,
        name            TEXT,
        exchange        TEXT,
        is_etf          INTEGER NOT NULL DEFAULT 0,
        quote_type      TEXT,
        sector          TEXT,
        industry        TEXT,
        fund_category   TEXT,
        market_cap      REAL,
        total_assets    REAL,
        excluded_reason TEXT,
        updated_at      TEXT
    );

    CREATE TABLE prices (
        ticker   TEXT NOT NULL,
        date     TEXT NOT NULL,
        open     REAL,
        high     REAL,
        low      REAL,
        close    REAL,
        volume   REAL,
        adjusted INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (ticker, date)
    );
    CREATE INDEX idx_prices_date ON prices (date);

    CREATE TABLE rankings (
        date          TEXT NOT NULL,
        ticker        TEXT NOT NULL,
        sctr          REAL NOT NULL,
        raw_score     REAL NOT NULL,
        long_ema_pct  REAL,
        long_roc      REAL,
        mid_ema_pct   REAL,
        mid_roc       REAL,
        ppo_slope     REAL,
        rsi           REAL,
        long_ema_pts  REAL,
        long_roc_pts  REAL,
        mid_ema_pts   REAL,
        mid_roc_pts   REAL,
        ppo_slope_pts REAL,
        rsi_pts       REAL,
        PRIMARY KEY (date, ticker)
    );
    CREATE INDEX idx_rankings_date_sctr ON rankings (date, sctr DESC);

    CREATE TABLE picks (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker           TEXT NOT NULL,
        status           TEXT NOT NULL
                         CHECK (status IN ('signal', 'open', 'exit_pending', 'closed')),
        signal_date      TEXT NOT NULL,
        signal_close     REAL NOT NULL,
        sctr_at_signal   REAL,
        setup_score      REAL,
        setup_details    TEXT,
        entry_date       TEXT,
        entry_price      REAL,
        initial_stop     REAL,
        current_stop     REAL,
        highest_close    REAL,
        exit_signal_date TEXT,
        exit_date        TEXT,
        exit_price       REAL,
        return_pct       REAL,
        days_held        INTEGER,
        created_at       TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX idx_picks_status ON picks (status);
    -- At most one active (not closed) pick per ticker.
    CREATE UNIQUE INDEX idx_picks_one_active_per_ticker
        ON picks (ticker) WHERE status != 'closed';

    CREATE TABLE stop_history (
        pick_id INTEGER NOT NULL REFERENCES picks (id) ON DELETE CASCADE,
        date    TEXT NOT NULL,
        close   REAL,
        stop    REAL NOT NULL,
        PRIMARY KEY (pick_id, date)
    );

    CREATE TABLE runs (
        session_date   TEXT PRIMARY KEY,
        started_at     TEXT NOT NULL,
        finished_at    TEXT,
        status         TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed', 'skipped')),
        universe_size  INTEGER,
        shortlist_size INTEGER,
        new_picks      INTEGER,
        stops_raised   INTEGER,
        exits          INTEGER,
        errors         INTEGER,
        error          TEXT
    );
    """,
    # 2: universe prefilter stats and metadata cache timestamp on symbols
    """
    ALTER TABLE symbols ADD COLUMN total_revenue REAL;
    ALTER TABLE symbols ADD COLUMN avg_dollar_volume REAL;
    ALTER TABLE symbols ADD COLUMN history_sessions INTEGER;
    ALTER TABLE symbols ADD COLUMN metadata_updated_at TEXT;
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open the database (creating its folder if needed) and bring the schema up to date."""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations in order. Returns the resulting schema version."""
    current = schema_version(conn)
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema version {current} is newer than this code ({SCHEMA_VERSION})"
        )
    for version in range(current + 1, SCHEMA_VERSION + 1):
        log.info("Applying database migration %d", version)
        # executescript commits first, so wrap each migration in its own transaction.
        conn.executescript(
            f"BEGIN;\n{MIGRATIONS[version - 1]}\nPRAGMA user_version = {version};\nCOMMIT;"
        )
    return schema_version(conn)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on any exception."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r["name"] for r in rows}


def column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    if table not in table_names(conn):
        raise ValueError(f"unknown table {table!r}")
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
