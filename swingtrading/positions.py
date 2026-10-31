"""Pick lifecycle: signal -> open -> exit_pending -> closed.

- signal: a setup fired on signal_date's close.
- open: filled at the next session's open; initial stop = entry - atr_mult x ATR at signal.
- exit_pending: a close fell below the stop in effect going into that session.
- closed: filled at the next session's open. return_pct = exit_price / entry_price - 1
  (a fraction, 0.05 = +5%); days_held = sessions from entry to exit.

Every function writes but never commits: the caller (the pipeline) wraps a whole session
in one transaction, which also lets a dry run roll everything back. Processing a session
twice changes nothing the second time.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from swingtrading import market_calendar, stops
from swingtrading.config import Settings
from swingtrading.db import ACTIVE_PICK_STATUSES
from swingtrading.setups.base import SetupResult

log = logging.getLogger(__name__)


class DuplicatePickError(ValueError):
    """The ticker already has a pending, open or exit-pending pick."""


@dataclass(frozen=True)
class SessionBar:
    open: float
    close: float
    atr: float  # NaN when there is not enough history


@dataclass
class LifecycleResult:
    entries: list[int] = field(default_factory=list)  # picks filled at today's open
    exits: list[int] = field(default_factory=list)  # picks closed at today's open
    stops_raised: list[int] = field(default_factory=list)
    stop_outs: list[int] = field(default_factory=list)  # closed below the stop today
    missing: list[str] = field(default_factory=list)  # active tickers with no bar today


def _now() -> str:
    return pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S")


def session_bars(bars: pd.DataFrame, session: date, atr_period: int) -> dict[str, SessionBar]:
    """Each ticker's open, close and ATR on `session`, from a long frame of bars."""
    out: dict[str, SessionBar] = {}
    for ticker, df in bars[bars["date"] <= session].groupby("ticker", sort=False):
        df = df.sort_values("date").reset_index(drop=True)
        if df["date"].iloc[-1] != session:
            continue
        atr = float(stops.atr_series(df, atr_period).iloc[-1])
        last = df.iloc[-1]
        out[ticker] = SessionBar(float(last["open"]), float(last["close"]), atr)
    return out


# ---- reads ---------------------------------------------------------------------------


def picks(conn: sqlite3.Connection, statuses: tuple[str, ...] | None = None) -> list[dict]:
    sql = "SELECT * FROM picks"
    params: tuple[str, ...] = ()
    if statuses:
        sql += f" WHERE status IN ({', '.join('?' * len(statuses))})"
        params = statuses
    return [dict(r) for r in conn.execute(sql + " ORDER BY id", params)]


def get_pick(conn: sqlite3.Connection, pick_id: int) -> dict:
    row = conn.execute("SELECT * FROM picks WHERE id = ?", (pick_id,)).fetchone()
    if row is None:
        raise LookupError(f"no pick {pick_id}")
    return dict(row)


def stop_history(conn: sqlite3.Connection, pick_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT date, close, stop FROM stop_history WHERE pick_id = ? ORDER BY date", (pick_id,)
    )
    return [dict(r) for r in rows]


def active_tickers(conn: sqlite3.Connection) -> set[str]:
    """Tickers with a pending, open or exit-pending pick; the scan skips these."""
    return {p["ticker"] for p in picks(conn, ACTIVE_PICK_STATUSES)}


# ---- transitions -----------------------------------------------------------------------


def create_signal(
    conn: sqlite3.Connection,
    ticker: str,
    session: date,
    close: float,
    atr: float,
    sctr: float | None,
    setup: SetupResult,
) -> int:
    """A new pick in `signal` status. Raises DuplicatePickError if one is already active."""
    details: dict[str, Any] = {"detector": setup.detector, **setup.details}
    try:
        cur = conn.execute(
            "INSERT INTO picks (ticker, status, signal_date, signal_close, signal_atr,"
            " sctr_at_signal, setup_score, setup_details) VALUES (?, 'signal', ?, ?, ?, ?, ?, ?)",
            (ticker, session.isoformat(), close, atr, sctr, setup.score, json.dumps(details)),
        )
    except sqlite3.IntegrityError as exc:
        if "picks.ticker" not in str(exc):
            raise
        raise DuplicatePickError(f"{ticker} already has an active pick") from None
    return int(cur.lastrowid)


def fill_exits(
    conn: sqlite3.Connection, session: date, today: dict[str, SessionBar], result: LifecycleResult
) -> None:
    """Close exit-pending picks at this session's open."""
    for p in picks(conn, ("exit_pending",)):
        if p["exit_signal_date"] >= session.isoformat():
            continue  # stopped out today; fills at the next open
        bar = today.get(p["ticker"])
        if bar is None:
            result.missing.append(p["ticker"])
            continue
        entry_date = date.fromisoformat(p["entry_date"])
        days = len(market_calendar.sessions_between(entry_date, session)) - 1
        conn.execute(
            "UPDATE picks SET status = 'closed', exit_date = ?, exit_price = ?, return_pct = ?,"
            " days_held = ?, updated_at = ? WHERE id = ?",
            (session.isoformat(), bar.open, bar.open / p["entry_price"] - 1, days, _now(),
             p["id"]),
        )  # fmt: skip
        result.exits.append(p["id"])


def fill_entries(
    conn: sqlite3.Connection,
    session: date,
    today: dict[str, SessionBar],
    atr_mult: float,
    result: LifecycleResult,
) -> None:
    """Open signals from earlier sessions at this session's open."""
    for p in picks(conn, ("signal",)):
        if p["signal_date"] >= session.isoformat():
            continue  # signalled today; fills at the next open
        bar = today.get(p["ticker"])
        if bar is None:
            result.missing.append(p["ticker"])
            continue
        stop = stops.initial_stop(bar.open, p["signal_atr"], atr_mult)
        conn.execute(
            "UPDATE picks SET status = 'open', entry_date = ?, entry_price = ?,"
            " initial_stop = ?, current_stop = ?, updated_at = ? WHERE id = ?",
            (session.isoformat(), bar.open, stop, stop, _now(), p["id"]),
        )
        result.entries.append(p["id"])


def update_open(
    conn: sqlite3.Connection,
    session: date,
    today: dict[str, SessionBar],
    atr_mult: float,
    result: LifecycleResult,
) -> None:
    """On this session's close: check for a stop-out, else trail the stop. Records the
    session's stop in stop_history either way."""
    for p in picks(conn, ("open",)):
        done = conn.execute(
            "SELECT 1 FROM stop_history WHERE pick_id = ? AND date = ?",
            (p["id"], session.isoformat()),
        ).fetchone()
        if done or p["entry_date"] > session.isoformat():
            continue
        bar = today.get(p["ticker"])
        if bar is None:
            result.missing.append(p["ticker"])
            continue
        old_stop = p["current_stop"]
        highest = max(p["highest_close"] or -math.inf, bar.close)
        if stops.stopped_out(bar.close, old_stop):
            new_stop, status, exit_signal = old_stop, "exit_pending", session.isoformat()
            result.stop_outs.append(p["id"])
        else:
            new_stop, status, exit_signal = (
                stops.trail_stop(old_stop, highest, bar.atr, atr_mult),
                "open",
                None,
            )
            if new_stop > old_stop:
                result.stops_raised.append(p["id"])
        conn.execute(
            "UPDATE picks SET status = ?, current_stop = ?, highest_close = ?,"
            " exit_signal_date = ?, updated_at = ? WHERE id = ?",
            (status, new_stop, highest, exit_signal, _now(), p["id"]),
        )
        conn.execute(
            "INSERT INTO stop_history (pick_id, date, close, stop) VALUES (?, ?, ?, ?)",
            (p["id"], session.isoformat(), bar.close, new_stop),
        )


def process_session(
    conn: sqlite3.Connection, settings: Settings, session: date, bars: pd.DataFrame
) -> LifecycleResult:
    """Advance every active pick through one session: exits and entries fill at the open,
    then open picks (including ones that entered today) are checked on the close."""
    today = session_bars(bars, session, settings.stops.atr_period)
    mult = settings.stops.atr_mult
    result = LifecycleResult()
    fill_exits(conn, session, today, result)
    fill_entries(conn, session, today, mult, result)
    update_open(conn, session, today, mult, result)
    result.missing = sorted(set(result.missing))
    if result.missing:
        log.warning("Picks %s: no bar for %s, left unchanged", session, ", ".join(result.missing))
    return result
