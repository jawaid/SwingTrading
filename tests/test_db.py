import sqlite3

import pytest

from swingtrading import db

EXPECTED_COLUMNS = {
    "symbols": [
        "ticker", "name", "exchange", "is_etf", "quote_type", "sector", "industry",
        "fund_category", "market_cap", "total_assets", "excluded_reason", "updated_at",
        "total_revenue", "avg_dollar_volume", "history_sessions", "metadata_updated_at",
    ],
    "prices": ["ticker", "date", "open", "high", "low", "close", "volume", "adjusted"],
    "stop_history": ["pick_id", "date", "close", "stop"],
}  # fmt: skip

PICK_COLUMNS = {
    "id", "ticker", "status", "signal_date", "signal_close", "sctr_at_signal", "setup_score",
    "setup_details", "entry_date", "entry_price", "initial_stop", "current_stop",
    "highest_close", "exit_signal_date", "exit_date", "exit_price", "return_pct", "days_held",
}  # fmt: skip

RUN_COUNT_COLUMNS = {
    "universe_size", "shortlist_size", "new_picks", "stops_raised", "exits", "errors",
}  # fmt: skip


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "nested" / "swing.db")
    yield c
    c.close()


def _add_pick(conn, ticker="AAPL", status="signal"):
    conn.execute(
        "INSERT INTO picks (ticker, status, signal_date, signal_close) VALUES (?, ?, ?, ?)",
        (ticker, status, "2026-10-28", 100.0),
    )


def test_connect_creates_folder_and_all_tables(conn, tmp_path):
    assert (tmp_path / "nested" / "swing.db").exists()
    assert db.table_names(conn) >= {
        "symbols", "prices", "rankings", "picks", "stop_history", "runs",
    }  # fmt: skip
    assert db.schema_version(conn) == db.SCHEMA_VERSION


@pytest.mark.parametrize("table", sorted(EXPECTED_COLUMNS))
def test_table_columns(conn, table):
    assert db.column_names(conn, table) == EXPECTED_COLUMNS[table]


def test_picks_runs_rankings_columns(conn):
    assert set(db.column_names(conn, "picks")) >= PICK_COLUMNS
    assert set(db.column_names(conn, "runs")) >= RUN_COUNT_COLUMNS
    rankings = set(db.column_names(conn, "rankings"))
    assert {"date", "ticker", "sctr", "raw_score", "ppo_slope", "rsi"} <= rankings


def test_migrate_is_idempotent(tmp_path):
    path = tmp_path / "swing.db"
    db.connect(path).close()
    conn = db.connect(path)
    assert db.migrate(conn) == db.SCHEMA_VERSION
    conn.close()


def test_new_migration_is_applied(tmp_path, monkeypatch):
    path = tmp_path / "swing.db"
    db.connect(path).close()
    extra = "ALTER TABLE picks ADD COLUMN notes TEXT;"
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, extra])
    monkeypatch.setattr(db, "SCHEMA_VERSION", len(db.MIGRATIONS))
    conn = db.connect(path)
    assert db.schema_version(conn) == len(db.MIGRATIONS)
    assert "notes" in db.column_names(conn, "picks")
    conn.close()


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "swing.db"
    db.connect(path).close()
    bad = "ALTER TABLE picks ADD COLUMN notes TEXT; THIS IS NOT SQL;"
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, bad])
    monkeypatch.setattr(db, "SCHEMA_VERSION", len(db.MIGRATIONS))
    with pytest.raises(sqlite3.Error):
        db.connect(path)
    monkeypatch.undo()
    conn = db.connect(path)
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    assert "notes" not in db.column_names(conn, "picks")
    conn.close()


def test_newer_database_is_refused(tmp_path):
    path = tmp_path / "swing.db"
    conn = db.connect(path)
    conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(RuntimeError, match="newer"):
        db.connect(path)


def test_price_primary_key(conn):
    row = ("AAPL", "2026-10-28", 1, 2, 0.5, 1.5, 1000)
    sql = "INSERT INTO prices (ticker, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)"
    conn.execute(sql, row)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, row)


def test_pick_status_is_checked(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _add_pick(conn, status="bogus")


def test_one_active_pick_per_ticker(conn):
    _add_pick(conn, status="open")
    with pytest.raises(sqlite3.IntegrityError):
        _add_pick(conn, status="signal")
    conn.execute("UPDATE picks SET status = 'closed'")
    _add_pick(conn, status="signal")  # allowed once the earlier pick is closed
    _add_pick(conn, ticker="MSFT", status="signal")


def test_stop_history_cascades_on_pick_delete(conn):
    _add_pick(conn)
    pick_id = conn.execute("SELECT id FROM picks").fetchone()["id"]
    conn.execute(
        "INSERT INTO stop_history (pick_id, date, close, stop) VALUES (?, ?, ?, ?)",
        (pick_id, "2026-10-28", 100.0, 95.0),
    )
    conn.execute("DELETE FROM picks WHERE id = ?", (pick_id,))
    assert conn.execute("SELECT COUNT(*) FROM stop_history").fetchone()[0] == 0


def test_stop_history_requires_existing_pick(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO stop_history (pick_id, date, stop) VALUES (?, ?, ?)",
            (999, "2026-10-28", 95.0),
        )


def test_transaction_rolls_back_on_error(conn):
    with pytest.raises(ValueError), db.transaction(conn):
        _add_pick(conn)
        raise ValueError("boom")
    assert conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 0


def test_column_names_rejects_unknown_table(conn):
    with pytest.raises(ValueError):
        db.column_names(conn, "picks; DROP TABLE picks")
