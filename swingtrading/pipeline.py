"""One daily run: prices -> rank -> shortlist -> setups -> signals -> fills -> stops.

For each session to process, in order:
  1. Rank the universe (SCTR) and take the shortlist.
  2. Advance existing picks (positions.process_session): exit-pending picks close and
     earlier signals enter at the open, then open picks trail their stop or stop out on
     the close.
  3. Scan the shortlist with the configured detector, skipping tickers that still have
     an active pick, and create a signal for each setup.
  4. Write the session's `runs` row with counts.

Prices are updated once, up to the last session, before the loop. Missed sessions (the
Mac was asleep) are processed one by one from the session after the last successful run,
so nothing is skipped and nothing runs out of order. A session already processed is not
processed again. A dry run does all of this on an in-memory copy of the database, so
nothing is written; it still downloads prices into that copy.

Only one run (or dry run) at a time: a run holds a lock file next to the database, and a
second run started meanwhile (the scheduled run and Run now, say) stops with
RunInProgressError instead of processing the same session twice. The operating system
releases the lock if a run crashes, so it never goes stale.

The morning fill (fill_at_open) records the open-price fills early: shortly after the
open it takes today's opening price for picks due to enter or exit today and fills them,
so they don't wait for the evening run. It only fills; the evening run still stores the
day's bar, checks stops and scans. It does nothing unless the evening run is up to date
through the previous session (otherwise the evening catch-up fills each pick on the
session it was due), and the evening run leaves already-filled picks alone.
"""

from __future__ import annotations

import fcntl
import logging
import math
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from swingtrading import db, market_calendar, performance, positions
from swingtrading.config import Settings
from swingtrading.data.provider import PriceProvider
from swingtrading.db import ACTIVE_PICK_STATUSES, transaction
from swingtrading.prices import load_bars, update_prices
from swingtrading.ranking import rank_session, shortlist
from swingtrading.setups import make_detector, scan
from swingtrading.universe import universe_tickers

log = logging.getLogger(__name__)


class RunInProgressError(RuntimeError):
    """Another run holds the lock on this database."""


@contextmanager
def run_lock(conn: sqlite3.Connection) -> Iterator[None]:
    """Hold `<database>.lock` for the duration of a run. No-op for an in-memory database."""
    db_file = next((r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "")
    if not db_file:
        yield
        return
    with open(Path(db_file).with_suffix(".lock"), "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunInProgressError(
                "another run is already in progress; try again when it finishes"
            ) from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@dataclass
class SessionReport:
    session: date
    status: str = "success"  # success, skipped or failed
    universe_size: int = 0
    shortlist_size: int = 0
    setups: int = 0
    new_picks: list[str] = field(default_factory=list)
    entries: list[tuple[str, float]] = field(default_factory=list)  # ticker, entry price
    stops_raised: list[tuple[str, float, float]] = field(default_factory=list)  # old, new
    stop_outs: list[tuple[str, float, float]] = field(default_factory=list)  # close, stop
    exits: list[tuple[str, float]] = field(default_factory=list)  # ticker, return_pct
    errors: int = 0
    error: str | None = None


def _now_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def last_processed(conn: sqlite3.Connection) -> date | None:
    row = conn.execute("SELECT MAX(session_date) FROM runs WHERE status = 'success'").fetchone()
    return date.fromisoformat(row[0]) if row[0] else None


def sessions_to_process(conn: sqlite3.Connection, target: date) -> list[date]:
    """Every session after the last successful run, through target."""
    last = last_processed(conn)
    if last is None:
        return [target]
    if target <= last:
        return []
    return market_calendar.sessions_between(market_calendar.next_session(last), target)


def _start_run(conn: sqlite3.Connection, session: date) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT OR REPLACE INTO runs (session_date, started_at, status) VALUES (?, ?, ?)",
            (session.isoformat(), _now_text(), "running"),
        )


def _finish_run(conn: sqlite3.Connection, r: SessionReport) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, universe_size = ?, shortlist_size = ?,"
        " new_picks = ?, stops_raised = ?, exits = ?, errors = ?, error = ?"
        " WHERE session_date = ?",
        (_now_text(), r.status, r.universe_size, r.shortlist_size, len(r.new_picks),
         len(r.stops_raised), len(r.exits), r.errors, r.error, r.session.isoformat()),
    )  # fmt: skip


def _skip_run(conn: sqlite3.Connection, day: date) -> SessionReport:
    log.info("%s is not a trading session; nothing to do", day)
    report = SessionReport(day, status="skipped")
    _start_run(conn, day)
    with transaction(conn):
        _finish_run(conn, report)
    return report


def process_session(
    conn: sqlite3.Connection, settings: Settings, session: date, extra_errors: int = 0
) -> SessionReport:
    """Rank, advance picks, scan and signal for one session whose prices are stored."""
    report = SessionReport(session, errors=extra_errors)
    _start_run(conn, session)
    try:
        universe = universe_tickers(conn)
        report.universe_size = len(universe)
        ranked = rank_session(conn, settings, universe, session)  # commits its own rows
        short = shortlist(ranked, settings.ranking.shortlist_pct)
        report.shortlist_size = len(short)

        with transaction(conn):
            active = [p["ticker"] for p in positions.picks(conn, ACTIVE_PICK_STATUSES)]
            start = market_calendar.previous_session(
                session, settings.universe.min_history_sessions
            )
            bars = load_bars(conn, sorted(set(short["ticker"]) | set(active)), start, session)
            before = {p["id"]: p for p in positions.picks(conn, ACTIVE_PICK_STATUSES)}
            life = positions.process_session(conn, settings, session, bars)
            _describe_lifecycle(conn, report, life, before)
            report.errors += len(life.missing)

            scan_errors: list[str] = []
            skip = positions.active_tickers(conn)
            candidates = [t for t in short["ticker"] if t not in skip]
            found = scan(bars, candidates, make_detector(settings), session, scan_errors)
            report.setups = len(found)
            report.errors += len(scan_errors)
            today = positions.session_bars(
                bars[bars["ticker"].isin(list(found))], session, settings.stops.atr_period
            )
            sctr = short.set_index("ticker")["sctr"]
            for ticker, setup in found.items():
                bar = today[ticker]
                positions.create_signal(
                    conn, ticker, session, bar.close, bar.atr, float(sctr[ticker]), setup
                )
                report.new_picks.append(ticker)
            _finish_run(conn, report)
    except Exception as exc:
        log.exception("Run for %s failed", session)
        report.status, report.error = "failed", f"{type(exc).__name__}: {exc}"
        with transaction(conn):
            _finish_run(conn, report)
    return report


def _describe_lifecycle(
    conn: sqlite3.Connection,
    report: SessionReport,
    life: positions.LifecycleResult,
    before: dict[int, dict],
) -> None:
    for pid in life.entries:
        p = positions.get_pick(conn, pid)
        report.entries.append((p["ticker"], p["entry_price"]))
    for pid in life.exits:
        p = positions.get_pick(conn, pid)
        report.exits.append((p["ticker"], p["return_pct"]))
    for pid in life.stops_raised:
        p, old = positions.get_pick(conn, pid), before[pid]
        start = old["current_stop"] if old["status"] == "open" else p["initial_stop"]
        report.stops_raised.append((p["ticker"], start, p["current_stop"]))
    for pid in life.stop_outs:
        p = positions.get_pick(conn, pid)
        close = positions.stop_history(conn, pid)[-1]["close"]
        report.stop_outs.append((p["ticker"], close, p["current_stop"]))


def run(
    conn: sqlite3.Connection,
    settings: Settings,
    provider: PriceProvider,
    target: date | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    notifier: Callable[[str], object] | None = None,
) -> list[SessionReport]:
    """Process every session due through `target` (default: last completed session)."""
    done = market_calendar.last_completed_session(now, settings.schedule.close_buffer_minutes)
    target = target or done
    if target > done:
        raise ValueError(f"session {target} is not complete yet; last completed is {done}")

    with run_lock(conn):
        reports = _run_locked(conn, settings, provider, target, dry_run, now)
    if notifier and not dry_run and reports and reports[0].status != "skipped":
        notifier(notification_text(reports))  # nothing to say on a no-op or holiday
    return reports


def _run_locked(
    conn: sqlite3.Connection,
    settings: Settings,
    provider: PriceProvider,
    target: date,
    dry_run: bool,
    now: datetime | None,
) -> list[SessionReport]:
    work = db.copy_to_memory(conn) if dry_run else conn
    try:
        if not market_calendar.is_trading_day(target):
            return [_skip_run(work, target)]
        sessions = sessions_to_process(work, target)
        if not sessions:
            log.info("Already processed through %s; nothing to do", last_processed(work))
            return []
        log.info("Processing %d session(s): %s to %s", len(sessions), sessions[0], sessions[-1])

        price_errors = 0
        active = [p["ticker"] for p in positions.picks(work, ACTIVE_PICK_STATUSES)]
        # SPY too, even if it drops out of the universe: Performance compares against it.
        tickers = sorted(set(universe_tickers(work)) | set(active) | {performance.BENCHMARK})
        try:
            prices = update_prices(work, settings, provider, tickers, target, now=now)
            price_errors = len(prices.no_data) + len(prices.stale)
        except Exception as exc:
            log.exception("Price update failed")
            report = SessionReport(sessions[0], status="failed", error=f"prices: {exc}")
            _start_run(work, sessions[0])
            with transaction(work):
                _finish_run(work, report)
            return [report]

        reports = []
        for i, session in enumerate(sessions):
            report = process_session(work, settings, session, price_errors if i == 0 else 0)
            reports.append(report)
            if report.status == "failed":
                break  # later sessions depend on this one; retry on the next run
    finally:
        if dry_run:
            work.close()
    return reports


# ---- morning fill ---------------------------------------------------------------------


@dataclass
class OpenFillReport:
    session: date
    status: str = "success"  # success, skipped or failed
    reason: str | None = None  # why it was skipped, or the error
    entries: list[tuple[str, float]] = field(default_factory=list)  # ticker, entry price
    exits: list[tuple[str, float]] = field(default_factory=list)  # ticker, return_pct
    missing: list[str] = field(default_factory=list)  # due but no opening price yet


def fill_at_open(
    conn: sqlite3.Connection,
    settings: Settings,
    provider: PriceProvider,
    now: datetime | None = None,
    notifier: Callable[[str], object] | None = None,
) -> OpenFillReport:
    """Fill today's due entries and exits at today's opening price. Writes no prices."""
    now = now or datetime.now(UTC)
    today = now.astimezone(ZoneInfo(settings.schedule.timezone)).date()
    report = OpenFillReport(today)
    opens_at = market_calendar.session_open(today)
    if opens_at is None:
        report.status, report.reason = "skipped", "not a trading session"
        return report
    if now < opens_at + timedelta(minutes=settings.schedule.open_buffer_minutes):
        report.status, report.reason = "skipped", "the market has not been open long enough"
        return report

    with run_lock(conn):
        if last_processed(conn) != market_calendar.previous_session(today):
            report.status = "skipped"
            report.reason = "the evening run is behind; it fills these when it catches up"
            return report
        due = [p for p in positions.picks(conn, ("signal",)) if p["signal_date"] < str(today)]
        due += [
            p
            for p in positions.picks(conn, ("exit_pending",))
            if p["exit_signal_date"] < str(today)
        ]
        if not due:
            report.status, report.reason = "skipped", "no picks due to fill today"
            return report
        try:
            bars = provider.history(sorted({p["ticker"] for p in due}), today, today)
            today_bars = {
                r.ticker: positions.SessionBar(float(r.open), float(r.close), math.nan)
                for r in bars.itertuples()
                if r.date == today
            }
            life = positions.LifecycleResult()
            with transaction(conn):
                positions.fill_exits(conn, today, today_bars, life)
                positions.fill_entries(conn, today, today_bars, settings.stops.atr_mult, life)
        except Exception as exc:
            log.exception("Morning fill for %s failed", today)
            report.status, report.reason = "failed", f"{type(exc).__name__}: {exc}"
            return report
        for pid in life.entries:
            p = positions.get_pick(conn, pid)
            report.entries.append((p["ticker"], p["entry_price"]))
        for pid in life.exits:
            p = positions.get_pick(conn, pid)
            report.exits.append((p["ticker"], p["return_pct"]))
        report.missing = sorted(set(life.missing))
    if notifier and (report.entries or report.exits or report.status == "failed"):
        notifier(format_open_fill(report))
    return report


def format_open_fill(r: OpenFillReport) -> str:
    head = f"Morning fill {r.session}"
    if r.status != "success":
        return f"{head}: {'FAILED - ' if r.status == 'failed' else ''}{r.reason}"
    lines = [f"{head}: {len(r.entries)} entered, {len(r.exits)} closed"]
    if r.entries:
        lines.append("  Entered at the open: " + ", ".join(f"{t} {p:.2f}" for t, p in r.entries))
    if r.exits:
        lines.append("  Closed at the open: " + ", ".join(f"{t} {x:+.1%}" for t, x in r.exits))
    if r.missing:
        lines.append(f"  No opening price yet (the evening run fills these): {' '.join(r.missing)}")
    return "\n".join(lines)


# ---- output -------------------------------------------------------------------------


def notification_text(reports: list[SessionReport]) -> str:
    if not reports:
        return "Already up to date."
    if any(r.status == "failed" for r in reports):
        failed = next(r for r in reports if r.status == "failed")
        return f"Run for {failed.session} failed: {failed.error}"
    last = reports[-1]
    new = sum(len(r.new_picks) for r in reports)
    raised = sum(len(r.stops_raised) for r in reports)
    outs = sum(len(r.stop_outs) for r in reports)
    closed = sum(len(r.exits) for r in reports)
    span = f"{reports[0].session} to {last.session}" if len(reports) > 1 else str(last.session)
    return f"{span}: {new} new picks, {raised} stops raised, {outs} stopped out, {closed} closed"


def format_reports(reports: list[SessionReport], dry_run: bool = False) -> str:
    if not reports:
        return "Already up to date; nothing to process."
    lines = []
    for r in reports:
        head = f"Session {r.session}"
        if r.status == "skipped":
            lines.append(f"{head}: not a trading session, skipped")
            continue
        if r.status == "failed":
            lines.append(f"{head}: FAILED - {r.error}")
            continue
        lines.append(
            f"{head}: universe {r.universe_size}, shortlist {r.shortlist_size}, "
            f"{r.setups} setups, {r.errors} errors"
        )
        lines.append(f"  New picks ({len(r.new_picks)}): {' '.join(r.new_picks) or '-'}")
        if r.entries:
            lines.append(
                "  Entered at the open: " + ", ".join(f"{t} {p:.2f}" for t, p in r.entries)
            )
        if r.exits:
            lines.append("  Closed at the open: " + ", ".join(f"{t} {x:+.1%}" for t, x in r.exits))
        if r.stops_raised:
            lines.append(
                f"  Stops raised ({len(r.stops_raised)}): "
                + ", ".join(f"{t} {a:.2f}->{b:.2f}" for t, a, b in r.stops_raised)
            )
        if r.stop_outs:
            lines.append(
                "  Stopped out (exit at next open): "
                + ", ".join(f"{t} close {c:.2f} < stop {s:.2f}" for t, c, s in r.stop_outs)
            )
    if dry_run:
        lines.append("\nDry run: nothing was written to the database.")
    return "\n".join(lines)
