"""`swing` command-line entry points."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from swingtrading import db, market_calendar
from swingtrading.config import Settings, load_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="swing", description="End-of-day swing trading signals")
    parser.add_argument("--settings", help="path to settings.yaml (default: config/settings.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("refresh-universe", help="rebuild the symbol list and metadata cache")
    backfill = sub.add_parser("backfill", help="download the full price history window")
    backfill.add_argument("--tickers", nargs="+", help="only these tickers (default: universe)")
    update = sub.add_parser("update-prices", help="fetch only sessions missing since last run")
    update.add_argument("--tickers", nargs="+", help="only these tickers (default: universe)")
    rank = sub.add_parser("rank", help="compute SCTR rankings from stored prices")
    rank.add_argument("--date", help="session date YYYY-MM-DD (default: last completed session)")
    rank.add_argument("--top", type=int, default=25, help="how many names to print")
    scan = sub.add_parser("scan", help="scan the stored shortlist for setups (writes nothing)")
    scan.add_argument("--date", help="session date YYYY-MM-DD (default: last completed session)")
    run = sub.add_parser("run", help="run the daily pipeline")
    run.add_argument("--date", help="session date YYYY-MM-DD (default: last completed session)")
    run.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    app = sub.add_parser("app", help="launch the Streamlit app")
    app.add_argument("--port", type=int, default=8501, help="local port (default: 8501)")
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


def prices_cmd(
    conn: sqlite3.Connection, settings: Settings, tickers: list[str] | None, full: bool
) -> int:
    from swingtrading.data.yahoo import YahooPriceProvider
    from swingtrading.prices import update_prices
    from swingtrading.universe import universe_tickers

    tickers = tickers or universe_tickers(conn)
    if not tickers:
        print("The universe is empty. Run `swing refresh-universe` first.")
        return 1
    now = datetime.now(UTC)
    as_of = market_calendar.last_completed_session(now, settings.schedule.close_buffer_minutes)
    result = update_prices(
        conn, settings, YahooPriceProvider(settings.data), tickers, as_of, full=full, now=now
    )
    print(f"\nPrices through {as_of}: {result.summary()}")
    return 0


def _session(settings: Settings, day: str | None) -> date:
    if day:
        return date.fromisoformat(day)
    return market_calendar.last_completed_session(None, settings.schedule.close_buffer_minutes)


def rank_cmd(conn: sqlite3.Connection, settings: Settings, day: str | None, top: int) -> int:
    from swingtrading.ranking import rank_session, shortlist
    from swingtrading.universe import universe_tickers

    as_of = _session(settings, day)
    tickers = universe_tickers(conn)
    ranked = rank_session(conn, settings, tickers, as_of)
    if ranked.empty:
        print(f"Nothing ranked for {as_of}. Run `swing backfill` first.")
        return 1
    short = shortlist(ranked, settings.ranking.shortlist_pct)
    print(
        f"\nSCTR for {as_of}: {len(ranked)} ranked, shortlist (top "
        f"{settings.ranking.shortlist_pct:g}%) = {len(short)} names\n"
    )
    cols = ["ticker", "sctr", "raw_score", "long_ema_pct", "long_roc", "mid_ema_pct", "mid_roc",
            "ppo_slope", "rsi"]  # fmt: skip
    print(ranked[cols].head(top).to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    return 0


def scan_cmd(conn: sqlite3.Connection, settings: Settings, day: str | None) -> int:
    import pandas as pd

    from swingtrading.prices import load_bars
    from swingtrading.ranking import load_rankings, shortlist
    from swingtrading.setups import make_detector, scan

    as_of = _session(settings, day)
    ranked = load_rankings(conn, as_of)
    if ranked.empty:
        print(f"No rankings for {as_of}. Run `swing rank --date {as_of}` first.")
        return 1
    short = shortlist(ranked, settings.ranking.shortlist_pct)
    detector = make_detector(settings)
    start = market_calendar.previous_session(as_of, settings.universe.min_history_sessions)
    bars = load_bars(conn, short["ticker"].tolist(), start=start, end=as_of)
    found = scan(bars, short["ticker"], detector, as_of)
    print(f"\n{detector.name} on {as_of}: {len(found)} setups in a shortlist of {len(short)}\n")
    if found:
        sctr = short.set_index("ticker")["sctr"]
        rows = [
            {"ticker": t, "sctr": sctr[t], "score": r.score, **r.details} for t, r in found.items()
        ]
        table = pd.DataFrame(rows).sort_values(["score", "sctr"], ascending=False)
        print(table.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    return 0


def run_cmd(conn: sqlite3.Connection, settings: Settings, day: str | None, dry_run: bool) -> int:
    from swingtrading import notify, pipeline
    from swingtrading.data.yahoo import YahooPriceProvider

    target = date.fromisoformat(day) if day else None
    try:
        reports = pipeline.run(
            conn,
            settings,
            YahooPriceProvider(settings.data),
            target=target,
            dry_run=dry_run,
            notifier=None if dry_run else notify.send,
        )
    except pipeline.RunInProgressError as exc:
        print(f"Not started: {exc}.")
        return 1
    print("\n" + pipeline.format_reports(reports, dry_run))
    return 1 if any(r.status == "failed" for r in reports) else 0


def app_cmd(settings: Settings, settings_file: str | None, port: int) -> int:
    """Run the Streamlit app in the foreground until Ctrl+C."""
    import os
    import subprocess

    from swingtrading.config import PROJECT_ROOT, SETTINGS_ENV_VAR

    db.connect(settings.resolve(settings.paths.db_path)).close()  # create/migrate first
    env = dict(os.environ)
    if settings_file:
        env[SETTINGS_ENV_VAR] = str(Path(settings_file).resolve())
    cmd = [sys.executable, "-m", "streamlit", "run", str(PROJECT_ROOT / "app" / "Home.py"),
           "--server.port", str(port), "--server.address", "localhost",
           "--server.headless", "true", "--browser.gatherUsageStats", "false",
           "--theme.base", "light", "--client.toolbarMode", "minimal"]  # fmt: skip
    print(f"SwingTrading app: http://localhost:{port}  (Ctrl+C to stop)")
    try:
        return subprocess.call(cmd, env=env, cwd=PROJECT_ROOT)
    except KeyboardInterrupt:
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = load_settings(args.settings)

    if args.command == "app":
        return app_cmd(settings, args.settings, args.port)

    setup_logging(settings.resolve(settings.paths.log_dir), datetime.now().date().isoformat())
    conn = db.connect(settings.resolve(settings.paths.db_path))
    try:
        if args.command == "refresh-universe":
            return refresh_universe_cmd(conn, settings)
        if args.command == "rank":
            return rank_cmd(conn, settings, args.date, args.top)
        if args.command == "run":
            return run_cmd(conn, settings, args.date, args.dry_run)
        if args.command == "scan":
            return scan_cmd(conn, settings, args.date)
        if args.command in ("backfill", "update-prices"):
            return prices_cmd(conn, settings, args.tickers, full=args.command == "backfill")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
