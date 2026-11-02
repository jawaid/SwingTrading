"""NYSE trading sessions, holidays and the last fully completed session."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache

import pandas as pd
import pandas_market_calendars as mcal


@lru_cache(maxsize=1)
def _calendar() -> mcal.MarketCalendar:
    return mcal.get_calendar("NYSE")


def _schedule(start: date, end: date) -> pd.DataFrame:
    return _calendar().schedule(start_date=start, end_date=end)


def sessions_between(start: date, end: date) -> list[date]:
    """Trading sessions from start to end, both inclusive, in order."""
    if start > end:
        return []
    return [ts.date() for ts in _schedule(start, end).index]


def is_trading_day(day: date) -> bool:
    return bool(sessions_between(day, day))


def last_completed_session(now: datetime | None = None, buffer_minutes: int = 30) -> date:
    """The most recent session whose close (plus a buffer) is at or before `now`.

    Handles early closes because it uses each session's actual close time.
    A naive `now` is treated as UTC.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now_ts = pd.Timestamp(now).tz_convert("UTC")
    sched = _schedule(now_ts.date() - timedelta(days=14), now_ts.date())
    done = sched[sched["market_close"] + pd.Timedelta(minutes=buffer_minutes) <= now_ts]
    if done.empty:
        raise LookupError(f"no completed NYSE session in the 14 days before {now}")
    return done.index[-1].date()


def session_open(day: date) -> datetime | None:
    """When the session on `day` opens (UTC), or None if `day` is not a session."""
    sched = _schedule(day, day)
    if sched.empty:
        return None
    return sched["market_open"].iloc[0].to_pydatetime()


def previous_session(day: date, n: int = 1) -> date:
    """The session `n` sessions before `day` (day itself need not be a session)."""
    if n < 1:
        raise ValueError("n must be at least 1")
    # ~252 sessions per 365 days; pad generously for holidays.
    sessions = sessions_between(day - timedelta(days=int(n * 1.6) + 10), day - timedelta(days=1))
    if len(sessions) < n:
        raise LookupError(f"not enough calendar history before {day}")
    return sessions[-n]


def next_session(day: date) -> date:
    """The first session strictly after `day`."""
    sessions = sessions_between(day + timedelta(days=1), day + timedelta(days=14))
    return sessions[0]
