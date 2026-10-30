from datetime import UTC, date, datetime

import pytest

from swingtrading import market_calendar as mc


def _et(year, month, day, hour, minute):
    # Late October is EDT (UTC-4); late November is EST (UTC-5).
    offset = 4 if month < 11 or (month == 11 and day < 2) else 5
    return datetime(year, month, day, hour + offset, minute, tzinfo=UTC)


def test_weekends_and_holidays_are_not_trading_days():
    assert mc.is_trading_day(date(2026, 10, 28))  # Wednesday
    assert not mc.is_trading_day(date(2026, 10, 31))  # Saturday
    assert not mc.is_trading_day(date(2026, 11, 26))  # Thanksgiving
    assert not mc.is_trading_day(date(2026, 12, 25))  # Christmas


def test_sessions_between_inclusive_and_skips_holidays():
    sessions = mc.sessions_between(date(2026, 11, 23), date(2026, 11, 30))
    assert sessions == [
        date(2026, 11, 23),
        date(2026, 11, 24),
        date(2026, 11, 25),
        date(2026, 11, 27),
        date(2026, 11, 30),
    ]
    assert mc.sessions_between(date(2026, 11, 30), date(2026, 11, 23)) == []


def test_last_completed_session_respects_close_and_buffer():
    # Wednesday 2026-10-28: close 16:00 ET, buffer 30 minutes.
    assert mc.last_completed_session(_et(2026, 10, 28, 16, 29), 30) == date(2026, 10, 27)
    assert mc.last_completed_session(_et(2026, 10, 28, 16, 30), 30) == date(2026, 10, 28)
    assert mc.last_completed_session(_et(2026, 10, 28, 9, 0), 30) == date(2026, 10, 27)


def test_last_completed_session_on_weekend_and_monday_morning():
    assert mc.last_completed_session(_et(2026, 10, 31, 12, 0)) == date(2026, 10, 30)
    assert mc.last_completed_session(_et(2026, 11, 2, 10, 0)) == date(2026, 10, 30)


def test_last_completed_session_uses_early_close():
    # Day after Thanksgiving closes at 13:00 ET.
    assert mc.last_completed_session(_et(2026, 11, 27, 13, 45), 30) == date(2026, 11, 27)


def test_naive_now_is_utc():
    naive = datetime(2026, 10, 28, 20, 45)  # 16:45 ET
    assert mc.last_completed_session(naive, 30) == date(2026, 10, 28)


def test_previous_and_next_session():
    assert mc.previous_session(date(2026, 11, 27)) == date(2026, 11, 25)
    assert mc.previous_session(date(2026, 10, 28), 3) == date(2026, 10, 23)
    assert mc.next_session(date(2026, 11, 25)) == date(2026, 11, 27)
    assert mc.next_session(date(2026, 10, 30)) == date(2026, 11, 2)
    with pytest.raises(ValueError):
        mc.previous_session(date(2026, 10, 28), 0)


def test_previous_session_far_back_counts_sessions():
    start = mc.previous_session(date(2026, 10, 28), 260)
    assert len(mc.sessions_between(start, date(2026, 10, 27))) == 260
