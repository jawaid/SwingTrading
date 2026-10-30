"""`swing` command-line entry points."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from swingtrading import db, market_calendar
from swingtrading.config import Settings, load_settings

# Commands that later build phases implement. Until then they say so and exit non-zero.
_PENDING = {
    "backfill": "Phase 3",
    "run": "Phase 7",
    "app": "Phase 8",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="swing", description="End-of-day swing trading signals")
    parser.add_argument("--settings", help="path to settings.yaml (default: config/settings.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("refresh-universe", help="rebuild the symbol list and metadata cache")
    sub.add_parser("backfill", help="download price history for the universe")
    run = sub.add_parser("run", help="run the daily pipeline")
    run.add_argument("--date", help="session date YYYY-MM-DD (default: last completed session)")
    run.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    sub.add_parser("app", help="launch the Streamlit app")
    return parser


def setup_logging(log_dir: Path, day: str) -> None:
    """Log to the console and to data/logs/run_YYYY-MM-DD.log."""
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(), logging.FileHandler(log_dir / f"run_{day}.log")):
        handler.setFormatter(fmt)
        root.addHandler(handler)


def refresh_universe_cmd(conn: sqlite3.Connection, settings: Settings) -> int:
    from swingtrading.data import symbols
    from swingtrading.data.yahoo import YahooMetadataProvider, YahooPriceProvider
    from swingtrading.universe import refresh_universe

    now = datetime.now(UTC)
    as_of = market_calendar.last_completed_session(now, settings.schedule.close_buffer_minutes)
    texts = symbols.fetch_symbol_files(settings.resolve(settings.paths.cache_dir), now.date())
    counts = refresh_universe(
        conn,
        settings,
        symbols.load_symbols(texts),
        YahooPriceProvider(settings.data),
        YahooMetadataProvider(settings.data),
        as_of=as_of,
        now=now,
    )
    print(f"\nUniverse as of {as_of}: {sum(counts.values())} symbols")
    for reason, n in sorted(counts.items(), key=lambda kv: (kv[0] != "included", -kv[1])):
        print(f"  {reason:<38}{n:>6}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = load_settings(args.settings)

    phase = _PENDING.get(args.command)
    if phase:
        db.connect(settings.resolve(settings.paths.db_path)).close()
        print(f"`swing {args.command}` is not built yet ({phase} of BUILD_PLAN.md).")
        return 1

    setup_logging(settings.resolve(settings.paths.log_dir), datetime.now().date().isoformat())
    conn = db.connect(settings.resolve(settings.paths.db_path))
    try:
        if args.command == "refresh-universe":
            return refresh_universe_cmd(conn, settings)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
